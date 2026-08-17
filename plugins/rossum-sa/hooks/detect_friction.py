# plugins/rossum-sa/hooks/detect_friction.py
"""Friction detector hook for the rossum-sa plugin (stdlib-only).

Counts friction into a per-session state file and, on a tripped threshold,
injects a one-line additionalContext nudge asking the model to OFFER the
plugin-feedback skill. Never sends anything; never blocks; always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Frozen sanitized allow-list — the shared contract (mirror: payload-contract.md).
FEEDBACK_FIELDS = (
    "route", "signal", "corroborators", "tool_name", "endpoint", "method",
    "error_class", "http_status", "expected", "got", "reference_pack",
    "section", "counts", "plugin_version", "description",
)


def new_state(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "counts": {"tool_errors": 0, "devloop_cycles": 0, "reprompts": 0},
        "tool_error_streaks": {},   # tool_name -> consecutive-failure count
        "signals_tripped": [],
        "last_tool": None,
        "last_error_class": None,
        "frustration_hit": False,
        "edit_seen": False,
        "offered": False,
        "progress_since_prompt": True,
    }


def state_to_payload(state: dict, route: str, description: str) -> dict:
    counts = state.get("counts", {})
    payload = {
        "route": route,
        "signal": (state.get("signals_tripped") or [None])[0],
        "corroborators": _corroborators(state),
        "tool_name": state.get("last_tool"),
        "endpoint": None,
        "method": None,
        "error_class": state.get("last_error_class"),
        "http_status": None,
        "expected": None,
        "got": None,
        "reference_pack": None,
        "section": None,
        "counts": {
            "errors": counts.get("tool_errors", 0),
            "cycles": counts.get("devloop_cycles", 0),
            "reprompts": counts.get("reprompts", 0),
        },
        "plugin_version": None,
        "description": description,
    }
    return {k: payload[k] for k in FEEDBACK_FIELDS}


REPROMPT_THRESHOLD = 4


def _corroborators(state: dict) -> list[str]:
    out = []
    if state.get("counts", {}).get("reprompts", 0) >= REPROMPT_THRESHOLD:
        out.append("reprompted_a_lot")
    if state.get("frustration_hit"):
        out.append("frustration")
    return out


FRUSTRATION_RE = re.compile(
    r"\b(still (broken|wrong|not working|failing)|you keep|i already (told|said)"
    r"|not what i (asked|wanted)|that'?s (wrong|not right)"
    r"|wtf|ffs|for f\w*'?s sake)\b",
    re.IGNORECASE,
)


def scan_frustration(prompt: str) -> bool:
    return bool(FRUSTRATION_RE.search(prompt or ""))


# Allow-list extraction: error_class must never carry raw error text — real
# messages embed org hostnames, annotation IDs, and file paths, all of which
# the payload contract promises never leave the machine. A pattern either
# matches a safe, generic class or the field degrades to "unknown".
_ERROR_CLASS_PATTERNS = (
    re.compile(r"\b[A-Z][A-Za-z0-9_]+(?:Error|Exception)\b"),
    re.compile(r"\bHTTP[ /]?\d{3}\b", re.IGNORECASE),
    re.compile(r"^Exit code \d+\b"),
    re.compile(r"\b(?:ENOENT|ECONNREFUSED|ETIMEDOUT|EACCES|EPERM)\b"),
    re.compile(r"\b(?:file|path|directory) does not exist\b", re.IGNORECASE),
    re.compile(r"\bno such file or directory\b", re.IGNORECASE),
    re.compile(r"\bpermission denied\b", re.IGNORECASE),
    re.compile(r"\btimed? ?out\b", re.IGNORECASE),
)


def _error_class(event: dict) -> str:
    """Extract a safe, generic error class from a PostToolUseFailure event.

    Reads the documented top-level ``error`` first (tool_response.error/.stderr
    kept as fallbacks), then matches ALLOW-LIST patterns only — the raw text is
    never passed through. No match -> "unknown".
    """
    resp = event.get("tool_response") or {}
    if not isinstance(resp, dict):
        resp = {"error": str(resp)}
    raw = " ".join(
        str(event.get("error") or resp.get("error") or resp.get("stderr") or "").split()
    )
    for pattern in _ERROR_CLASS_PATTERNS:
        m = pattern.search(raw)
        if m:
            return m.group(0)[:80]
    return "unknown"


ERROR_THRESHOLD = 3
DEVLOOP_THRESHOLD = 3
THRESHOLD_FLOOR = 2


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "rossum-sa"


def is_opted_out() -> bool:
    if os.environ.get("ROSSUM_SA_NO_FEEDBACK") == "1":
        return True
    return (_cache_dir() / "feedback-optout").exists()


def evaluate(state: dict) -> str | None:
    n_corr = len(_corroborators(state))
    err_thr = max(THRESHOLD_FLOOR, ERROR_THRESHOLD - n_corr)
    dev_thr = max(THRESHOLD_FLOOR, DEVLOOP_THRESHOLD - n_corr)
    max_streak = max(state["tool_error_streaks"].values(), default=0)
    if max_streak >= err_thr:
        desc = f"repeated_tool_error x{max_streak} on {state.get('last_tool')}"
        state["signals_tripped"].insert(0, "repeated_tool_error")
        return desc
    if state["counts"]["devloop_cycles"] >= dev_thr:
        desc = f"devloop_stall x{state['counts']['devloop_cycles']}"
        state["signals_tripped"].insert(0, "devloop_stall")
        return desc
    return None


def apply_event(state: dict, event: dict) -> dict:
    ev = event.get("hook_event_name")
    tool = event.get("tool_name")
    if ev == "PostToolUseFailure":
        state["last_tool"] = tool
        state["last_error_class"] = _error_class(event)
        if tool is not None:
            streak = state["tool_error_streaks"].get(tool, 0) + 1
            state["tool_error_streaks"][tool] = streak
            state["counts"]["tool_errors"] = max(state["counts"]["tool_errors"], streak)
        if tool == "Bash" and state["edit_seen"]:
            state["counts"]["devloop_cycles"] += 1
    elif ev == "PostToolUse":
        if tool in ("Edit", "Write", "NotebookEdit"):
            state["edit_seen"] = True
        if tool is not None:
            state["tool_error_streaks"][tool] = 0
        if tool == "Bash":
            state["counts"]["devloop_cycles"] = 0
        state["progress_since_prompt"] = True
    elif ev == "UserPromptSubmit":
        # A "reprompt" is a user turn with NO successful tool call since the
        # previous turn. Plain conversation must not build the corroborator —
        # it lowers the error threshold, and counting every turn made TWO
        # failures fire in any session longer than four turns.
        if state.get("progress_since_prompt", True):
            state["counts"]["reprompts"] = 0
        else:
            state["counts"]["reprompts"] += 1
        state["progress_since_prompt"] = False
        if scan_frustration(event.get("prompt", "")):
            state["frustration_hit"] = True
    return state


_NUDGE_EVENTS = ("UserPromptSubmit", "Stop")


def state_path(session_id: str) -> Path:
    # No session_id -> per-process file, NOT a shared nosession.json: a shared
    # file latches offered=true once and kills the detector for every future
    # session that lacks an id.
    name = session_id or f"nosession-{os.getpid()}"
    return _cache_dir() / "friction" / f"{name}.json"


def _valid_state(data) -> bool:
    if not isinstance(data, dict):
        return False
    counts = data.get("counts")
    return (
        isinstance(counts, dict)
        and {"tool_errors", "devloop_cycles", "reprompts"} <= counts.keys()
        and isinstance(data.get("tool_error_streaks"), dict)
        and isinstance(data.get("signals_tripped"), list)
    )


def load_state(session_id: str) -> dict:
    p = state_path(session_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = None
        # A structurally bad file must not kill the detector for the rest of
        # the session: apply_event would raise, main() would swallow it, and
        # save_state would never replace the poisoned file.
        if _valid_state(data):
            return data
    return new_state(session_id)


def save_state(state: dict) -> None:
    p = state_path(state["session_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, p)


def build_nudge(signal_desc: str) -> str:
    return (
        f"[rossum-sa] Friction detected this session ({signal_desc}). "
        "In your next reply, OFFER once, in one plain sentence with no detector "
        "jargon (no signal names, thresholds, or tool streaks), e.g.: \"I keep "
        "hitting errors here — want me to send a short anonymous report to the "
        "plugin team so they can fix it? (/rossum-sa:plugin-feedback)\" Do NOT "
        "run the skill without an explicit yes. If the user declines, do not "
        "offer again this session."
    )


def run(event: dict) -> dict | None:
    session_id = event.get("session_id", "")
    state = load_state(session_id)
    apply_event(state, event)
    result = None
    ev = event.get("hook_event_name")
    if ev in _NUDGE_EVENTS and not state["offered"] and not is_opted_out():
        desc = evaluate(state)
        if desc:
            state["offered"] = True
            result = {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": ev,
                    "additionalContext": build_nudge(desc),
                },
            }
    save_state(state)
    return result


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        out = run(event)
        if out is not None:
            sys.stdout.write(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
