# plugins/rossum-sa/hooks/detect_friction.py
"""Friction detector hook for the rossum-sa plugin (stdlib-only).

Counts friction into a per-session state file and, on a tripped threshold,
injects a one-line additionalContext nudge asking the model to OFFER the
plugin-feedback skill. Never sends anything; never blocks; always exits 0.
"""
from __future__ import annotations

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


def _corroborators(state: dict) -> list[str]:
    out = []
    if state.get("counts", {}).get("reprompts", 0) >= REPROMPT_THRESHOLD:
        out.append("reprompted_a_lot")
    if state.get("frustration_hit"):
        out.append("frustration")
    return out


REPROMPT_THRESHOLD = 4
