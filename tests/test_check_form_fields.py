# tests/test_check_form_fields.py
"""Unit test for the form-ID drift checker's pure helper (no network).

Modern Google Forms render questions from the FB_PUBLIC_LOAD_DATA_ bootstrap
array, where entry IDs appear as bare integers — so presence is checked as a
digit-substring, not as a name="entry.N" attribute.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _load_script():
    spec = importlib.util.spec_from_file_location(
        "check_form_fields", ROOT / "scripts/check_form_fields.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

CANNED_HTML = (
    '<script>var FB_PUBLIC_LOAD_DATA_ = [null,[null,[[1111111111,"Route",null,0,'
    '[[2049151602,null,1]]],[2222222222,"Description",null,1,[[1045781291,null,0]]]'
    ']]];</script>'
)

def test_missing_entries_flags_only_absent_ids():
    m = _load_script()
    fields = {
        "route": "entry.2049151602",        # present in canned HTML
        "description": "entry.1045781291",  # present
        "tool_name": "entry.9999999999",    # absent
    }
    missing = m.missing_entries(CANNED_HTML, fields)
    assert missing == {"tool_name": "entry.9999999999"}

def test_missing_entries_empty_config_is_clean():
    m = _load_script()
    assert m.missing_entries(CANNED_HTML, {}) == {}

def test_main_treats_empty_form_url_as_unconfigured_even_with_fields(tmp_path, monkeypatch):
    """FINDING 5 regression: a fork that clears form_url but leaves
    form_fields populated must exit 0 as "unconfigured", exactly as the
    module docstring promises ("Exit 0: form unconfigured") — not fall
    through to validating a URL shape that was never set. No network call
    may happen on this branch."""
    m = _load_script()
    cfg_path = tmp_path / "feedback-config.json"
    cfg_path.write_text(json.dumps({
        "form_url": "",
        "form_view_url": "",
        "form_fields": {"payload": "entry.1"},
    }), encoding="utf-8")
    monkeypatch.setattr(m, "CONFIG", cfg_path)
    assert m.main() == 0
