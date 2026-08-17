# tests/test_detect_friction.py
import importlib.util
import json as _json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins/rossum-sa/hooks/detect_friction.py"

def _load():
    spec = importlib.util.spec_from_file_location("detect_friction", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_contract_fields_frozen():
    m = _load()
    assert m.FEEDBACK_FIELDS == (
        "route", "signal", "corroborators", "tool_name", "endpoint", "method",
        "error_class", "http_status", "expected", "got", "reference_pack",
        "section", "counts", "plugin_version", "description",
    )

def test_state_to_payload_only_allowlisted():
    m = _load()
    st = m.new_state("sess-1")
    st["last_tool"] = "rossum_get_annotation"
    st["counts"]["tool_errors"] = 3
    st["_secret_leak"] = {"annotation_id": 999, "raw": "customer data"}
    payload = m.state_to_payload(st, route="agent-bug", description="hi")
    assert set(payload) == set(m.FEEDBACK_FIELDS)
    assert "annotation_id" not in str(payload)
    assert payload["tool_name"] == "rossum_get_annotation"
    assert payload["counts"]["errors"] == 3

def test_error_streak_and_reset():
    m = _load()
    st = m.new_state("s")
    fail = {"hook_event_name": "PostToolUseFailure",
            "tool_name": "rossum_get", "tool_response": {"error": "HTTP 500"}}
    for _ in range(3):
        m.apply_event(st, fail)
    assert st["tool_error_streaks"]["rossum_get"] == 3
    assert st["counts"]["tool_errors"] == 3
    assert st["last_error_class"] == "HTTP 500"
    m.apply_event(st, {"hook_event_name": "PostToolUse",
                       "tool_name": "rossum_get", "tool_response": {"exit_code": 0}})
    assert st["tool_error_streaks"]["rossum_get"] == 0

def test_error_class_reads_documented_top_level_error():
    """The real PostToolUseFailure contract: failure text is event["error"],
    NOT tool_response.error. Live 2026-08-14: Read failures carried only this
    key and the old parser degraded every report to "unknown"."""
    m = _load()
    st = m.new_state("s")
    ev = {"hook_event_name": "PostToolUseFailure", "tool_name": "Read",
          "session_id": "s", "tool_response": {},
          "error": "File does not exist."}
    m.apply_event(st, ev)
    assert st["last_error_class"] == "File does not exist"

def test_error_class_flattens_multiline_bash_error():
    m = _load()
    st = m.new_state("s")
    ev = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
          "session_id": "s",
          "error": "Exit code 1\nError: Cannot find module 'express'"}
    m.apply_event(st, ev)
    assert st["last_error_class"] == "Exit code 1"

def test_error_class_is_allowlist_only_never_raw_text():
    """BLOCKER regression (review 2026-08-17): raw error strings embed customer
    hostnames, annotation IDs, and paths; only allow-listed classes may survive."""
    m = _load()
    cases = [
        ("HTTP 404 for https://acme-corp.rossum.app/api/v1/annotations/8472913 not found",
         "HTTP 404"),
        ("File does not exist: /Users/vaclavrut/Projects/hyundai/queue_9912/invoice_backlog.csv",
         "File does not exist"),
        ("totally novel gibberish mentioning acme-corp and 8472913", "unknown"),
    ]
    for raw, expected in cases:
        st = m.new_state("s")
        ev = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
              "session_id": "s", "error": raw}
        m.apply_event(st, ev)
        out = st["last_error_class"]
        assert out == expected
        for leak in ("acme", "8472913", "/Users", "hyundai", "queue_9912"):
            assert leak not in out

def test_devloop_cycles_need_prior_edit():
    m = _load()
    st = m.new_state("s")
    bash_fail = {"hook_event_name": "PostToolUseFailure",
                 "tool_name": "Bash", "tool_response": {"exit_code": 1}}
    m.apply_event(st, bash_fail)                      # no edit yet -> ignored
    assert st["counts"]["devloop_cycles"] == 0
    m.apply_event(st, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                       "tool_response": {}})
    for _ in range(3):
        m.apply_event(st, bash_fail)
    assert st["counts"]["devloop_cycles"] == 3

def test_reprompt_and_frustration():
    m = _load()
    st = m.new_state("s")
    # First prompt of a session never counts as a reprompt (no progress could
    # have happened before it yet) -> counter stays at 0.
    m.apply_event(st, {"hook_event_name": "UserPromptSubmit", "prompt": "please fix"})
    assert st["counts"]["reprompts"] == 0 and st["frustration_hit"] is False
    # Second prompt with still no tool success in between IS a stalled turn.
    m.apply_event(st, {"hook_event_name": "UserPromptSubmit",
                       "prompt": "this is STILL broken, you keep failing"})
    assert st["counts"]["reprompts"] == 1
    assert st["frustration_hit"] is True
    assert m.scan_frustration("that's not what I asked") is True
    assert m.scan_frustration("great, thanks") is False

def test_reprompts_ignore_productive_turns():
    """FINDING 2 regression: 4 normal prompts, each followed by a successful
    tool call, plus 2 Bash failures must NOT fire — plain conversation with
    progress in between must never build the reprompt corroborator."""
    m = _load()
    st = m.new_state("s")
    for i in range(4):
        m.apply_event(st, {"hook_event_name": "UserPromptSubmit", "prompt": f"turn {i}"})
        m.apply_event(st, {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                           "tool_response": {"exit_code": 0}})
    for _ in range(2):
        m.apply_event(st, _fail("Bash"))
    assert m._corroborators(st) == []
    assert m.evaluate(st) is None

def test_reprompts_build_on_stalled_turns_and_lower_threshold():
    """FINDING 2 regression: consecutive prompts with NO tool success in
    between are genuinely stalled turns and must still build the
    "reprompted_a_lot" corroborator, lowering the error threshold (3 -> 2)
    so 2 same-tool failures fire. (The very first prompt of a fresh session
    never counts, so reaching the reprompts threshold of 4 needs 5 prompts.)
    """
    m = _load()
    st = m.new_state("s")
    for i in range(5):
        m.apply_event(st, {"hook_event_name": "UserPromptSubmit", "prompt": f"turn {i}"})
    for _ in range(2):
        m.apply_event(st, _fail("Read"))
    assert "reprompted_a_lot" in m._corroborators(st)
    desc = m.evaluate(st)
    assert desc is not None and "repeated_tool_error" in desc


def _fail(tool="rossum_get"):
    return {"hook_event_name": "PostToolUseFailure", "tool_name": tool,
            "tool_response": {"error": "HTTP 500"}}

def test_three_errors_trigger():
    m = _load()
    st = m.new_state("s")
    for _ in range(3):
        m.apply_event(st, _fail())
    assert m.evaluate(st) is not None

def test_two_errors_plus_frustration_trigger():
    m = _load()
    st = m.new_state("s")
    for _ in range(2):
        m.apply_event(st, _fail())
    m.apply_event(st, {"hook_event_name": "UserPromptSubmit",
                       "prompt": "still broken"})
    assert m.evaluate(st) is not None      # threshold lowered 3 -> 2

def test_two_errors_alone_no_trigger():
    m = _load()
    st = m.new_state("s")
    for _ in range(2):
        m.apply_event(st, _fail())
    assert m.evaluate(st) is None

def test_chatty_session_no_trigger():
    m = _load()
    st = m.new_state("s")
    for _ in range(8):
        m.apply_event(st, {"hook_event_name": "UserPromptSubmit", "prompt": "next"})
    assert m.evaluate(st) is None

def test_opt_out_env(monkeypatch):
    m = _load()
    monkeypatch.setenv("ROSSUM_SA_NO_FEEDBACK", "1")
    assert m.is_opted_out() is True


@pytest.mark.parametrize("event_name", ["UserPromptSubmit", "Stop"])
def test_run_emits_nudge_once_then_silent(tmp_path, monkeypatch, event_name):
    # additionalContext delivery on Stop is not an explicitly documented harness
    # contract — it was verified empirically in the live E2E of 2026-08-14, and
    # this test pins our emission side so a regression is caught in CI.
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("ROSSUM_SA_NO_FEEDBACK", raising=False)
    for _ in range(3):
        m.run(_fail() | {"session_id": "s9"})
    nudge_event = {"hook_event_name": event_name, "session_id": "s9"}
    if event_name == "UserPromptSubmit":
        nudge_event["prompt"] = "help"
    out = m.run(nudge_event)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "plugin-feedback" in ctx and out["continue"] is True
    again_event = {"hook_event_name": event_name, "session_id": "s9"}
    if event_name == "UserPromptSubmit":
        again_event["prompt"] = "again"
    again = m.run(again_event)
    assert again is None or "additionalContext" not in (again or {}).get(
        "hookSpecificOutput", {})

def test_run_respects_optout(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("ROSSUM_SA_NO_FEEDBACK", "1")
    for _ in range(3):
        m.run(_fail() | {"session_id": "s10"})
    out = m.run({"hook_event_name": "UserPromptSubmit",
                 "prompt": "help", "session_id": "s10"})
    assert out is None


def test_load_state_rejects_malformed_shapes_and_recovers(tmp_path, monkeypatch):
    """FINDING 4 regression: a structurally-bad state file (wrong top-level
    type, or a dict missing required keys) must not kill the detector for the
    rest of the session — load_state hands back a fresh valid state instead
    of a shape apply_event/save_state can't handle."""
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    for session_id, bad_payload in (
        ("s4", "[]"),
        ("s4b", _json.dumps({"session_id": "s4b"})),
    ):
        p = m.state_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(bad_payload, encoding="utf-8")
        st = m.load_state(session_id)
        assert m._valid_state(st)
        assert st["session_id"] == session_id
        # Must not raise on the recovered state.
        m.apply_event(st, {"hook_event_name": "PostToolUse", "tool_name": "Bash"})
        out = m.run({"hook_event_name": "UserPromptSubmit", "prompt": "hi",
                     "session_id": session_id})
        assert out is None or isinstance(out, dict)

def test_state_path_no_session_is_pid_scoped_not_shared():
    """FINDING 4 regression: the no-session fallback must be per-process, not
    a shared nosession.json — a shared file latches offered=true once and
    kills the detector for every future session lacking an id."""
    m = _load()
    p = m.state_path("")
    assert str(os.getpid()) in p.name
    assert p.name != "nosession.json"

def test_missing_tool_name_does_not_pollute_streaks(tmp_path, monkeypatch):
    """FINDING 6 regression: an event with no tool_name (tool is None) must
    not create a None/"null" entry in tool_error_streaks, including across a
    save/load round-trip (json.dumps would coerce a None key to "null")."""
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    st = m.new_state("s6")
    m.apply_event(st, {"hook_event_name": "PostToolUseFailure", "error": "boom"})
    m.apply_event(st, {"hook_event_name": "PostToolUse"})
    assert None not in st["tool_error_streaks"]
    m.save_state(st)
    reloaded = _json.loads(m.state_path("s6").read_text(encoding="utf-8"))
    assert "null" not in reloaded["tool_error_streaks"]


def test_load_state_requires_full_key_set_and_recovers(tmp_path, monkeypatch):
    """OPEN FINDING 1 (re-review 2026-08-17): _valid_state previously checked
    only counts/tool_error_streaks/signals_tripped, so a state file that had
    those but was missing newer keys (edit_seen/offered/session_id/...)
    passed validation and was handed back as-is. apply_event then raised
    KeyError('edit_seen') on a Bash failure (state["edit_seen"] lookup) and
    run() raised KeyError('offered') on a nudge event; main()'s outer
    try/except swallowed the crash and the poisoned file was never replaced.
    _valid_state must require the FULL new_state key set."""
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    session_id = "s11"
    poisoned = {
        "counts": {"tool_errors": 0, "devloop_cycles": 0, "reprompts": 0},
        "tool_error_streaks": {},
        "signals_tripped": [],
        # edit_seen, offered, session_id, last_tool, etc. deliberately absent
    }
    p = m.state_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(poisoned), encoding="utf-8")
    st = m.load_state(session_id)
    assert set(m.new_state("")) <= set(st)          # fresh state, not the poisoned dict
    assert st["session_id"] == session_id
    assert st["edit_seen"] is False and st["offered"] is False
    # Must not raise -- pre-fix this KeyError'd on state["edit_seen"].
    m.apply_event(st, {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                       "error": "boom"})
    out = m.run({"hook_event_name": "Stop", "session_id": session_id})
    assert out is None or isinstance(out, dict)
    reloaded = _json.loads(p.read_text(encoding="utf-8"))
    assert set(m.new_state("")) <= set(reloaded)    # file was rewritten with the full shape


def test_error_class_rejects_glued_identifiers_leaking_tenant_data():
    """OPEN FINDING 2 (re-review 2026-08-17): the Error/Exception allow-list
    pattern allowed digits/underscores with no length cap, so a tenant name
    or numeric ID glued onto "Error" (no word boundary to split on) rode
    through as if it were a generic class name."""
    m = _load()
    cases = [
        ("AcmeCorp8472913Error occurred while processing", "unknown"),
        ("Tenant_QueueError raised", "unknown"),
    ]
    for raw, expected in cases:
        st = m.new_state("s")
        ev = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
              "session_id": "s", "error": raw}
        m.apply_event(st, ev)
        out = st["last_error_class"]
        assert out == expected
        for leak in ("Acme", "8472913", "Tenant", "Queue"):
            assert leak not in out

def test_error_class_still_matches_real_exception_names():
    """Companion to the fix above: letters-only, length-capped still matches
    real, generic exception class names -- the fix must not overcorrect."""
    m = _load()
    cases = [
        ("ModuleNotFoundError: No module named 'express'", "ModuleNotFoundError"),
        ("raised HTTPError during request", "HTTPError"),
        ("SSLCertVerificationError: certificate verify failed", "SSLCertVerificationError"),
    ]
    for raw, expected in cases:
        st = m.new_state("s")
        ev = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
              "session_id": "s", "error": raw}
        m.apply_event(st, ev)
        assert st["last_error_class"] == expected


def test_main_always_exits_zero_on_internal_error(monkeypatch):
    m = _load()
    import io
    monkeypatch.setattr(m.sys, "stdin", io.StringIO(
        '{"hook_event_name":"UserPromptSubmit","prompt":"hi","session_id":"x"}'))
    def boom(event):
        raise OSError("disk full")
    monkeypatch.setattr(m, "run", boom)
    assert m.main() == 0


def test_hooks_json_wires_all_events():
    cfg = _json.loads((ROOT / "plugins/rossum-sa/hooks/hooks.json").read_text())
    hooks = cfg["hooks"]
    for ev in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop"):
        assert ev in hooks, f"missing {ev}"
        cmd = hooks[ev][0]["hooks"][0]["command"]
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/detect_friction.py" in cmd
        assert cmd.startswith("python3 ")
