# plugins/rossum-sa/hooks/rossum_auth_guard.py
"""Auth guard hook for the rossum-sa plugin (stdlib-only).

When a rossum-api MCP tool answers "Not connected to Rossum", the useful next
move is to ask the user for a fresh token. The unhelpful one — reliably
observed — is a filesystem hunt: `credentials.yaml`, `.env`, `~/.prd2`,
`prd_config`, the keyring. That search does not succeed, costs many round
trips, and reads credential stores nobody asked us to read.

Instructions alone do not stop it; this blocks it. PostToolUse `decision:
block` feeds `reason` back to the model as the next thing it sees, so the
correction lands at the exact moment the wrong instinct fires.

The remediation splits on who is actually reachable, which the harness reports
through two fields: `agent_id` only inside a subagent, `agent_type` inside a
subagent OR when the session runs with `--agent`. "Ask the user and wait" is
unfollowable in either — nobody is there to ask — so a subagent is bounced back
to its parent, and an `--agent` run is pointed at the environment instead of a
prompt it cannot raise.

A bare `claude -p` carries neither field and is therefore indistinguishable
from an interactive session; the interactive reason names the environment path
as its fallback so that case still reads sensibly in a transcript.

Scope is deliberately narrow: only the rossum-api tools, only the
not-connected marker. Everything else passes through untouched, and any
unexpected input exits 0 silently — a guard must never be the reason a
session stalls.

Credit: the mechanism is Jan Šedo's, from a local settings.json PostToolUse
rule (PR #127 review). Shipping it with the plugin is the only change — so
every install gets it instead of each person configuring it.
"""
from __future__ import annotations

import json
import sys

# The server's own not-connected message (server.py `_NOT_CONNECTED_MSG`).
# Matched on this stable prefix so a reworded tail does not silently disarm
# the guard; tests/test_rossum_auth_guard.py pins the two together.
NOT_CONNECTED_MARKER = "Not connected to Rossum"

# hooks.json already scopes this with a matcher; checking the prefix here too
# keeps the guard correct if it is ever wired up without one.
TOOL_PREFIX = "mcp__plugin_rossum-sa_rossum-api__"

BLOCK_REASON = (
    "The Rossum MCP server is not authenticated (it returned "
    f'"{NOT_CONNECTED_MARKER}"). '
    "STOP and ask the user for a fresh Rossum API token, then wait for them. "
    "Do NOT search the filesystem for one: no cat/grep/find of credentials.yaml, "
    ".env, ~/.prd2, prd_config, keyrings, or any other credential store. That "
    "search does not succeed, and reading credential files nobody pointed you at "
    "is not yours to do. If the user has already pasted a token in this "
    "conversation — a bare 40-character hex string is one — call rossum_set_token "
    "with it instead of asking again. If this session has no user to answer (an "
    "unattended -p or scheduled run), do not wait: report that ROSSUM_TOKEN and "
    "ROSSUM_API_URL have to be set in the environment for runs like this one."
)

SYSTEM_MESSAGE = (
    "Rossum not connected: asking the user for a fresh token instead of "
    "searching for one."
)

# A subagent cannot resolve this: it has no user to prompt, and the connection
# belongs to the session that spawned it. Retrying or prompting here is what
# turned one missing token into ten failed calls (issue #133), so this branch
# spends the turn on a report the parent can act on instead.
SUBAGENT_BLOCK_REASON = (
    "The Rossum MCP server is not authenticated (it returned "
    f'"{NOT_CONNECTED_MARKER}"). '
    "You are a SUBAGENT: there is no user here to prompt, and the connection "
    "belongs to the session that dispatched you. Do NOT retry this call, do NOT "
    "call rossum_set_token, and do NOT search the filesystem for a token: no "
    "cat/grep/find of credentials.yaml, .env, ~/.prd2, prd_config, keyrings, or "
    "any other credential store. Stop now and return to the parent, reporting "
    "that the Rossum MCP connection has to be established in the main session "
    "before this work can run. If your task only needs to READ Rossum objects, "
    "say so in that report: the parent can dump them to local files with "
    "rossum_get_schema / rossum_get_hook out_file_path and re-dispatch you over "
    "the files, which needs no MCP call from you at all."
)

SUBAGENT_SYSTEM_MESSAGE = (
    "Rossum not connected inside a subagent: returning to the parent instead of "
    "prompting or retrying."
)

# `agent_type` without `agent_id` is a session started with --agent: a main
# thread, so there is no parent to hand back to, but still nobody to prompt.
# The environment is the only way in for it.
UNATTENDED_BLOCK_REASON = (
    "The Rossum MCP server is not authenticated (it returned "
    f'"{NOT_CONNECTED_MARKER}"). '
    "This session runs unattended (--agent), so there is no user to ask for a "
    "token and the credential prompt cannot be raised. Do NOT retry this call, "
    "and do NOT search the filesystem for a token: no cat/grep/find of "
    "credentials.yaml, .env, ~/.prd2, prd_config, keyrings, or any other "
    "credential store. Stop and report that this run needs ROSSUM_TOKEN and "
    "ROSSUM_API_URL set in its environment — the server connects from those on "
    "its own, with no prompt. If a token was supplied in your instructions, call "
    "rossum_set_token with it instead."
)

UNATTENDED_SYSTEM_MESSAGE = (
    "Rossum not connected in an unattended run: reporting the missing "
    "ROSSUM_TOKEN/ROSSUM_API_URL instead of prompting."
)


def _response_text(event: dict) -> str:
    """Flatten a tool response to text.

    The payload shape varies by tool and by transport — a plain string, a dict,
    a list of MCP content blocks — so this stringifies whatever arrived rather
    than assuming one shape. json.dumps covers the structured cases; str() is
    the fallback for anything that will not serialize.
    """
    response = event.get("tool_response")
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    try:
        return json.dumps(response)
    except (TypeError, ValueError):
        return str(response)


def run(event: dict) -> dict | None:
    """Block decision when a rossum-api tool reports it is not connected.

    Ordered most specific first: `agent_id` (a subagent) before `agent_type`
    (which a subagent also carries, but which alone means an --agent session).
    """
    tool_name = str(event.get("tool_name") or "")
    if not tool_name.startswith(TOOL_PREFIX):
        return None
    if NOT_CONNECTED_MARKER not in _response_text(event):
        return None
    if event.get("agent_id"):
        return {
            "decision": "block",
            "reason": SUBAGENT_BLOCK_REASON,
            "systemMessage": SUBAGENT_SYSTEM_MESSAGE,
        }
    if event.get("agent_type"):
        return {
            "decision": "block",
            "reason": UNATTENDED_BLOCK_REASON,
            "systemMessage": UNATTENDED_SYSTEM_MESSAGE,
        }
    return {
        "decision": "block",
        "reason": BLOCK_REASON,
        "systemMessage": SYSTEM_MESSAGE,
    }


def main() -> int:
    try:
        out = run(json.loads(sys.stdin.read() or "{}"))
        if out is not None:
            sys.stdout.write(json.dumps(out))
    except Exception:
        # A guard that crashes must not take the turn with it.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
