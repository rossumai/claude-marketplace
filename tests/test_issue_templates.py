"""Guards for the GitHub Issue Forms templates (string-level, stdlib-only).

The templates are the GitHub-without-gh rung of the plugin-feedback transport:
the skill prefills them via ?template=<route>.yml&<field-id>=<value>, so the
field ids MUST be payload-contract names and filenames MUST be <route>.yml.
"""
import importlib.util, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL_DIR = ROOT / ".github/ISSUE_TEMPLATE"
CFG = ROOT / "plugins/rossum-sa/skills/plugin-feedback/feedback-config.json"

def _contract_fields():
    spec = importlib.util.spec_from_file_location(
        "detect_friction", ROOT / "plugins/rossum-sa/hooks/detect_friction.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return set(m.FEEDBACK_FIELDS)

def _routes():
    return json.loads(CFG.read_text())["labels"]

def test_one_template_per_route():
    for route in _routes():
        assert (TPL_DIR / f"{route}.yml").is_file(), f"missing {route}.yml"

def test_templates_apply_their_route_label():
    for route in _routes():
        text = (TPL_DIR / f"{route}.yml").read_text(encoding="utf-8")
        assert re.search(rf'^labels:\s*\["{route}"\]\s*$', text, re.M), (
            f"{route}.yml must auto-apply the {route} label"
        )

def test_template_field_ids_are_contract_fields():
    contract = _contract_fields()
    for route in _routes():
        text = (TPL_DIR / f"{route}.yml").read_text(encoding="utf-8")
        ids = re.findall(r"^\s*id:\s*(\S+)\s*$", text, re.M)
        assert ids, f"{route}.yml has no field ids"
        rogue = [i for i in ids if i not in contract]
        assert not rogue, f"{route}.yml field ids outside the payload contract: {rogue}"

def test_templates_carry_privacy_note_and_description():
    for route in _routes():
        text = (TPL_DIR / f"{route}.yml").read_text(encoding="utf-8")
        assert "Metadata only" in text, f"{route}.yml missing the privacy note"
        assert re.search(r"^\s*id:\s*description\s*$", text, re.M), (
            f"{route}.yml missing the description field"
        )

def test_config_yml_keeps_blank_issues():
    text = (TPL_DIR / "config.yml").read_text(encoding="utf-8")
    assert "blank_issues_enabled: true" in text

def test_config_yml_links_form_when_pinned():
    view_url = json.loads(CFG.read_text())["form_view_url"]
    if not view_url:
        return  # link is added in the pinning task
    assert view_url in (TPL_DIR / "config.yml").read_text(encoding="utf-8"), (
        "config.yml contact link must point at form_view_url"
    )
