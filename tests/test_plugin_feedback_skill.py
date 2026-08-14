# tests/test_plugin_feedback_skill.py
import importlib.util, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins/rossum-sa/skills/plugin-feedback"

def test_frontmatter_present():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert re.search(r"^name:\s*plugin-feedback\s*$", text, re.M)
    assert re.search(r"^description:\s*.+", text, re.M)
    # invocable by default: must NOT be disabled
    assert not re.search(r"^user-invocable:\s*false", text, re.M)

def _load_config():
    return json.loads((SKILL / "feedback-config.json").read_text())

def _load_detect_friction():
    spec = importlib.util.spec_from_file_location(
        "detect_friction", ROOT / "plugins/rossum-sa/hooks/detect_friction.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def test_config_static_defaults():
    cfg = _load_config()
    assert cfg["target_repo"] == "rossumai/claude-marketplace"
    assert cfg["labels"] == ["tool-request", "agent-bug", "knowledge-gap"]
    assert "mailto" in cfg

def test_config_has_form_keys():
    cfg = _load_config()
    for key in ("form_url", "form_view_url", "form_fields"):
        assert key in cfg, f"feedback-config.json missing {key}"

def test_config_form_keys_all_set_or_all_empty():
    """The skill offers the anonymous channel iff the form is configured, so a
    half-configured form (URL without field map, or vice versa) is always a bug."""
    cfg = _load_config()
    configured = [bool(cfg["form_url"]), bool(cfg["form_view_url"]), bool(cfg["form_fields"])]
    assert all(configured) or not any(configured), (
        "form_url, form_view_url, form_fields must be set together or all empty"
    )

def test_config_form_shapes_when_set():
    cfg = _load_config()
    if not cfg["form_url"]:
        return  # unconfigured (pre-pinning / fork) — nothing to check
    assert re.fullmatch(
        r"https://docs\.google\.com/forms/d/e/[\w-]+/formResponse", cfg["form_url"])
    assert re.fullmatch(
        r"https://docs\.google\.com/forms/d/e/[\w-]+/viewform", cfg["form_view_url"])
    contract = set(_load_detect_friction().FEEDBACK_FIELDS)
    assert set(cfg["form_fields"]) == contract | {"contact_email"}, (
        "form_fields must map exactly the payload-contract fields + contact_email"
    )
    for field, entry in cfg["form_fields"].items():
        assert re.fullmatch(r"entry\.\d+", entry), f"{field}: bad entry id {entry!r}"

def test_contact_email_stays_outside_frozen_contract():
    assert "contact_email" not in _load_detect_friction().FEEDBACK_FIELDS

def test_contract_doc_matches_code():
    spec = importlib.util.spec_from_file_location(
        "detect_friction", ROOT / "plugins/rossum-sa/hooks/detect_friction.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    doc = (SKILL / "reference/payload-contract.md").read_text()
    for field in m.FEEDBACK_FIELDS:
        assert f"`{field}`" in doc, f"contract field {field} missing from doc"
