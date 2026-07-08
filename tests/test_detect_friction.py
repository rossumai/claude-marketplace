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
