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

def test_config_defaults():
    cfg = json.loads((SKILL / "feedback-config.json").read_text())
    assert cfg["target_repo"] == "rossumai/claude-marketplace"
    assert cfg["form_url"] == ""          # unset until Plan 1
    assert cfg["form_fields"] == {}

def test_contract_doc_matches_code():
    spec = importlib.util.spec_from_file_location(
        "detect_friction", ROOT / "plugins/rossum-sa/hooks/detect_friction.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    doc = (SKILL / "reference/payload-contract.md").read_text()
    for field in m.FEEDBACK_FIELDS:
        assert f"`{field}`" in doc, f"contract field {field} missing from doc"
