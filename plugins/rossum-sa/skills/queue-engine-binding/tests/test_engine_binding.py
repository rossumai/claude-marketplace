"""Unit tests for the pure reconciliation core, validated against live-conversion ground truth."""
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine_binding import clean_schema, derive_engine_fields  # noqa: E402


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_derive_engine_fields_matches_ground_truth():
    content = _load("pre_schema.json")["content"]
    catalog = _load("pre_trained_fields.json")
    derived = derive_engine_fields(content, catalog)
    expected = _load("expected_engine_fields.json")
    by_name = {f["name"]: f for f in derived}
    assert sorted(by_name) == sorted(f["name"] for f in expected)
    for exp in expected:
        got = by_name[exp["name"]]
        for key in ("label", "type", "subtype", "pre_trained_field_id", "tabular", "multiline"):
            assert got[key] == exp[key], f"{exp['name']}.{key}: {got[key]!r} != {exp[key]!r}"


def test_derive_skips_non_captured_ui_types():
    content = _load("pre_schema.json")["content"]
    names = {f["name"] for f in derive_engine_fields(content, _load("pre_trained_fields.json"))}
    assert "amount_check" not in names      # formula
    assert "vendor_match" not in names      # data
    assert "item_note" not in names         # data, inside table


def test_clean_schema_matches_ground_truth_rules():
    content = _load("pre_schema.json")["content"]
    cleaned, changes = clean_schema(content)

    def walk(nodes):
        for n in nodes:
            yield n
            ch = n.get("children")
            if isinstance(ch, list):
                yield from walk(ch)
            elif isinstance(ch, dict):
                yield from walk([ch])

    by_id = {n["id"]: n for n in walk(cleaned)}
    # Engine-extracted datapoints: rir emptied, ui normalized
    assert by_id["document_id"]["rir_field_names"] == []
    assert by_id["document_id"]["ui_configuration"] == {"type": "captured", "edit": "enabled"}
    assert by_id["item_code"]["rir_field_names"] == []
    # disable_prediction removed everywhere
    assert all("disable_prediction" not in n for n in walk(cleaned) if n.get("category") == "datapoint")
    # multivalue container keeps its rir_field_names
    assert by_id["line_items"]["rir_field_names"] == ["line_items"]
    # non-captured fields keep their ui type
    assert by_id["amount_check"]["ui_configuration"]["type"] == "formula"
    assert by_id["vendor_match"]["ui_configuration"]["type"] == "data"
    # change log mentions every touched field
    assert any("document_id" in c for c in changes)
