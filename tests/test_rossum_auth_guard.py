"""Guard: the rossum-sa auth-guard hook blocks the credential hunt.

The hook exists because instructions alone did not stop a filesystem search for
a token after an unauthenticated MCP call. These tests pin the four things
that would silently disarm it: the marker drifting away from the server's own
message, the scope widening past the rossum-api tools, the block decision
losing its shape, and the subagent branch collapsing back into "ask the user"
advice that a subagent cannot act on.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins" / "rossum-sa" / "hooks"
GUARD = HOOKS / "rossum_auth_guard.py"
HOOKS_JSON = HOOKS / "hooks.json"
SERVER_PY = ROOT / "plugins" / "rossum-sa" / "mcp-servers" / "rossum-api" / "server.py"

_STDLIB_OK = {"__future__", "json", "sys"}

sys.path.insert(0, str(HOOKS))
import rossum_auth_guard as guard  # noqa: E402

_TOOL = guard.TOOL_PREFIX + "rossum_list_queues"


def _event(tool_name: str, response, agent_id: str | None = None,
           agent_type: str | None = None) -> dict:
    event = {"hook_event_name": "PostToolUse", "tool_name": tool_name,
             "tool_response": response}
    if agent_id is not None:
        event["agent_id"] = agent_id
    if agent_type is not None:
        event["agent_type"] = agent_type
    return event


def test_is_stdlib_only():
    """Same contract as the server and the friction hook: no third-party imports."""
    mods = set()
    for node in ast.walk(ast.parse(GUARD.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= _STDLIB_OK, f"non-stdlib imports: {mods - _STDLIB_OK}"


def test_marker_matches_the_servers_own_message():
    """The guard keys off the server's not-connected message. If that message is
    reworded and the marker is not, the guard stops firing and nothing fails —
    the exact silent break this pins."""
    assert f'_NOT_CONNECTED_MSG = "{guard.NOT_CONNECTED_MARKER}' in SERVER_PY.read_text(
        encoding="utf-8"
    ), "server.py's _NOT_CONNECTED_MSG no longer starts with the guard's marker"


def test_blocks_on_not_connected():
    out = guard.run(_event(_TOOL, "Not connected to Rossum. Call rossum_set_token "
                                  "to establish a connection."))
    assert out is not None and out["decision"] == "block"
    assert "ask the user" in out["reason"].lower()
    assert out["systemMessage"]


def test_block_reason_names_the_stores_it_forbids():
    """The reason is what the model reads next, so it has to say which search is
    off-limits — a bare 'do not search' invites a creative reading."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER))["reason"].lower()
    for store in ("credentials.yaml", ".env", "prd_config", "keyring"):
        assert store in reason, f"block reason does not name {store}"


def test_block_reason_points_at_a_pasted_token():
    """A token already in the conversation should be used, not asked for again."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER))["reason"]
    assert "rossum_set_token" in reason and "40-character hex" in reason


def test_structured_response_shapes_are_searched():
    """Tool responses arrive as strings, dicts, or MCP content-block lists."""
    for response in (
        {"error": guard.NOT_CONNECTED_MARKER},
        [{"type": "text", "text": guard.NOT_CONNECTED_MARKER}],
        {"content": [{"type": "text", "text": guard.NOT_CONNECTED_MARKER}]},
    ):
        assert guard.run(_event(_TOOL, response)) is not None, response


def test_passes_through_healthy_calls():
    assert guard.run(_event(_TOOL, {"results": [{"id": 1}]})) is None


def test_ignores_other_tools():
    """A Bash call whose output happens to quote the message must not be blocked —
    scope creep here would block the very command a user ran on purpose."""
    assert guard.run(_event("Bash", guard.NOT_CONNECTED_MARKER)) is None


def test_missing_fields_do_not_raise():
    for event in ({}, {"tool_name": _TOOL}, {"tool_name": _TOOL, "tool_response": None}):
        assert guard.run(event) is None


def test_main_is_silent_and_zero_on_a_healthy_call():
    """End to end over stdin: exit 0 and no stdout, so the turn is untouched."""
    proc = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(_event(_TOOL, "ok")),
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0 and proc.stdout == ""


def test_main_emits_the_block_decision_over_stdin():
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(_event(_TOOL, guard.NOT_CONNECTED_MARKER)),
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["decision"] == "block"


def test_main_survives_malformed_stdin():
    proc = subprocess.run([sys.executable, str(GUARD)], input="not json",
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0 and proc.stdout == ""


# --- subagent branch: the session that cannot answer a credential prompt ---

def test_subagent_branch_does_not_tell_it_to_ask_or_wait():
    """`agent_id` means no user is reachable. The main-session reason ("ask the
    user ... then wait for them") is unfollowable there, and following it anyway
    is what produced ten failed calls in issue #133."""
    out = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER, agent_id="ag_1"))
    assert out is not None and out["decision"] == "block"
    reason = out["reason"].lower()
    assert "ask the user" not in reason
    assert "wait for them" not in reason
    assert out["reason"] != guard.BLOCK_REASON


def test_subagent_branch_sends_it_back_to_the_parent():
    """The only move available to a subagent: stop and report upward."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER,
                              agent_id="ag_1"))["reason"].lower()
    assert "parent" in reason
    assert "do not retry" in reason
    assert "rossum_set_token" in reason, "must name the call it is forbidding"


def test_subagent_branch_still_forbids_the_credential_hunt():
    """The filesystem hunt is the same wrong instinct in either session type, so
    neither branch may lose the list of stores it rules out."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER,
                              agent_id="ag_1"))["reason"].lower()
    for store in ("credentials.yaml", ".env", "prd_config", "keyring"):
        assert store in reason, f"subagent reason does not name {store}"


def test_subagent_branch_points_at_the_file_dump_alternative():
    """A read-only fan-out does not need the connection at all: the parent can
    dump objects with out_file_path and re-dispatch over local files. Naming that
    in the report is what stops the parent re-running the same broken pattern."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER,
                              agent_id="ag_1"))["reason"]
    assert "out_file_path" in reason


def test_main_session_keeps_the_interactive_reason():
    """No agent_id -> the main thread, where asking the user IS the right move."""
    out = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER))
    assert out["reason"] == guard.BLOCK_REASON
    assert out["systemMessage"] == guard.SYSTEM_MESSAGE


def test_agent_id_only_matters_when_not_connected():
    """The branch must not widen the guard's scope: a healthy subagent call is
    still none of the guard's business."""
    assert guard.run(_event(_TOOL, {"results": []}, agent_id="ag_1")) is None
    assert guard.run(_event("Bash", guard.NOT_CONNECTED_MARKER, agent_id="ag_1")) is None


def test_empty_agent_id_is_treated_as_the_main_thread():
    """An empty string is absence, not a subagent — the harness omits the field
    on the main thread, and a falsy value must not select the bounce branch."""
    out = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER, agent_id=""))
    assert out["reason"] == guard.BLOCK_REASON


# --- unattended branch: --agent sessions, detectable but with no user ---

def test_unattended_branch_points_at_the_environment():
    """`agent_type` without `agent_id` is a --agent session: a main thread, so
    there is no parent to report to, but still nobody to prompt. The environment
    is its only way in, so the reason has to name the variables."""
    out = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER, agent_type="reviewer"))
    assert out is not None and out["decision"] == "block"
    assert out["reason"] == guard.UNATTENDED_BLOCK_REASON
    reason = out["reason"]
    assert "ROSSUM_TOKEN" in reason and "ROSSUM_API_URL" in reason
    assert "ask the user" not in reason.lower()


def test_unattended_branch_still_forbids_the_credential_hunt():
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER,
                              agent_type="reviewer"))["reason"].lower()
    for store in ("credentials.yaml", ".env", "prd_config", "keyring"):
        assert store in reason, f"unattended reason does not name {store}"
    assert "do not retry" in reason


def test_a_subagent_carrying_both_fields_takes_the_subagent_branch():
    """A subagent reports agent_type as well, so the order of the two checks is
    load-bearing: it has a parent to return to, which an --agent run does not."""
    out = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER,
                           agent_id="ag_1", agent_type="Explore"))
    assert out["reason"] == guard.SUBAGENT_BLOCK_REASON


def test_interactive_reason_covers_the_session_it_cannot_detect():
    """A bare `claude -p` carries neither field, so it lands on the interactive
    reason. That reason must therefore not end at "wait for them"."""
    reason = guard.run(_event(_TOOL, guard.NOT_CONNECTED_MARKER))["reason"]
    assert "ROSSUM_TOKEN" in reason and "do not wait" in reason.lower()


def test_main_emits_the_subagent_decision_over_stdin():
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(_event(_TOOL, guard.NOT_CONNECTED_MARKER, agent_id="ag_1")),
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "block" and "parent" in out["reason"].lower()


def test_hooks_json_wires_the_guard_scoped_to_the_mcp_tools():
    """Registered on PostToolUse, and matched to the rossum-api tools only."""
    entries = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
    mine = [e for e in entries
            if any("rossum_auth_guard.py" in h.get("command", "")
                   for h in e.get("hooks", []))]
    assert len(mine) == 1, "guard should be registered exactly once on PostToolUse"
    assert mine[0].get("matcher", "").startswith("mcp__plugin_rossum-sa_rossum-api__"), (
        "guard must stay scoped to the rossum-api tools"
    )
