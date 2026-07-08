# tests/test_detect_friction.py
import importlib.util
from pathlib import Path

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
    assert set(payload) <= set(m.FEEDBACK_FIELDS)
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
    m.apply_event(st, {"hook_event_name": "UserPromptSubmit", "prompt": "please fix"})
    assert st["counts"]["reprompts"] == 1 and st["frustration_hit"] is False
    m.apply_event(st, {"hook_event_name": "UserPromptSubmit",
                       "prompt": "this is STILL broken, you keep failing"})
    assert st["frustration_hit"] is True
    assert m.scan_frustration("that's not what I asked") is True
    assert m.scan_frustration("great, thanks") is False


import os

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


def test_run_emits_nudge_once_then_silent(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("ROSSUM_SA_NO_FEEDBACK", raising=False)
    for _ in range(3):
        m.run(_fail() | {"session_id": "s9"})
    out = m.run({"hook_event_name": "UserPromptSubmit",
                 "prompt": "help", "session_id": "s9"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "plugin-feedback" in ctx and out["continue"] is True
    again = m.run({"hook_event_name": "UserPromptSubmit",
                   "prompt": "again", "session_id": "s9"})
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


def test_main_always_exits_zero_on_internal_error(monkeypatch):
    m = _load()
    import io
    monkeypatch.setattr(m.sys, "stdin", io.StringIO(
        '{"hook_event_name":"UserPromptSubmit","prompt":"hi","session_id":"x"}'))
    def boom(event):
        raise OSError("disk full")
    monkeypatch.setattr(m, "run", boom)
    assert m.main() == 0


import json as _json

def test_hooks_json_wires_all_events():
    cfg = _json.loads((ROOT / "plugins/rossum-sa/hooks/hooks.json").read_text())
    hooks = cfg["hooks"]
    for ev in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop"):
        assert ev in hooks, f"missing {ev}"
        cmd = hooks[ev][0]["hooks"][0]["command"]
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/detect_friction.py" in cmd
        assert cmd.startswith("python3 ")
