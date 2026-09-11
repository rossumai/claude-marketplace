"""Unit tests for the pure reconciliation core, validated against live-conversion ground truth."""
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine_binding import (  # noqa: E402
    alias_warnings,
    clean_schema,
    derive_engine_fields,
    iter_datapoints,
    resolve_use_case,
    restore_rir,
    seed_type_overrides,
)


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


def test_reasoning_field_is_exempt():
    """A reasoning datapoint (LLM-prompt-populated) is not engine-extracted: no engine
    field is derived for it and clean_schema leaves its ui_configuration untouched."""
    content = [
        {"category": "section", "id": "header", "children": [
            {"category": "datapoint", "id": "document_id", "label": "Document ID",
             "type": "string", "rir_field_names": ["document_id"],
             "ui_configuration": {"type": "captured", "edit": "enabled"}},
            {"category": "datapoint", "id": "risk_summary", "label": "Risk Summary",
             "type": "string", "rir_field_names": [],
             "ui_configuration": {"type": "reasoning", "edit": "enabled"}},
        ]},
    ]
    catalog = _load("pre_trained_fields.json")
    names = {f["name"] for f in derive_engine_fields(content, catalog)}
    assert "risk_summary" not in names      # reasoning -> not engine-extracted
    assert "document_id" in names

    cleaned, _changes = clean_schema(content)
    by_id = {dp["id"]: dp for dp, _t in iter_datapoints(cleaned)}
    assert by_id["risk_summary"]["ui_configuration"]["type"] == "reasoning"


def test_derive_custom_field_branch():
    """A captured datapoint with no catalog match becomes a cold custom engine field."""
    content = [
        {"category": "section", "id": "header", "children": [
            {"category": "datapoint", "id": "contract_date", "label": "Contract Date",
             "type": "date", "rir_field_names": ["not_in_catalog"],
             "ui_configuration": {"type": "captured", "edit": "enabled"}},
        ]},
    ]
    catalog = [{"name": "date_issue", "label": "Issue Date", "type": "date",
                "subtype": "period_begin", "tabular": False, "multiline": "false"}]
    fields = derive_engine_fields(content, catalog)
    assert len(fields) == 1
    field = fields[0]
    assert field["pre_trained_field_id"] is None
    assert field["subtype"] is None
    assert field["multiline"] == "false"
    assert field["type"] == "date"          # schema type mapped via SCHEMA_TYPE_DEFAULTS
    assert field["tabular"] is False


def test_restore_rir():
    """Revert maps pre_trained_field_id back; custom and non-extracted fields stay put."""
    content = [
        {"category": "section", "id": "header", "children": [
            {"category": "datapoint", "id": "issue_date", "type": "date",
             "rir_field_names": [],
             "ui_configuration": {"type": "captured", "edit": "enabled"}},
            {"category": "datapoint", "id": "internal_ref", "type": "string",
             "rir_field_names": [],
             "ui_configuration": {"type": "captured", "edit": "enabled"}},
            {"category": "datapoint", "id": "vendor_match", "type": "string",
             "rir_field_names": [],
             "ui_configuration": {"type": "data"}},
        ]},
    ]
    engine_fields = [
        {"name": "issue_date", "pre_trained_field_id": "date_issue"},
        {"name": "internal_ref", "pre_trained_field_id": None},
        {"name": "vendor_match", "pre_trained_field_id": "sender_name"},
    ]
    restored = restore_rir(content, engine_fields)
    by_id = {dp["id"]: dp for dp, _ in iter_datapoints(restored)}
    assert by_id["issue_date"]["rir_field_names"] == ["date_issue"]
    assert by_id["internal_ref"]["rir_field_names"] == []   # custom field: unrestorable
    assert by_id["vendor_match"]["rir_field_names"] == []   # ui type "data": untouched


# --- use_case gate: an engine without one is header-only and silently drops line items ---

class _Args:
    def __init__(self, use_case=None):
        self.use_case = use_case


def test_use_case_required_when_schema_has_line_items():
    content = _load("pre_schema.json")["content"]
    fields = derive_engine_fields(content, _load("pre_trained_fields.json"))
    assert any(f["tabular"] for f in fields), "fixture must have tabular fields for this test"
    try:
        resolve_use_case(_Args(), fields)
    except SystemExit:
        pass          # fail() exits - refusing to guess is the point
    else:
        raise AssertionError("expected a refusal when line items exist and no use case is given")


def test_use_case_passed_through_and_omitted_for_header_only():
    content = _load("pre_schema.json")["content"]
    fields = derive_engine_fields(content, _load("pre_trained_fields.json"))
    assert resolve_use_case(_Args("coupa_line_level"), fields) == "coupa_line_level"
    header_only = [f for f in fields if not f["tabular"]]
    assert resolve_use_case(_Args(), header_only) is None


# --- the queue flip compares schema type to engine-field type, so the schema type must win ---

def test_schema_type_wins_over_seed_type():
    catalog = [{"name": "currency", "type": "enum", "subtype": "", "multiline": "false"}]
    content = [{"category": "section", "id": "s", "children": [
        {"category": "datapoint", "id": "currency", "label": "Currency", "type": "string",
         "rir_field_names": ["currency"], "ui_configuration": {"type": "captured"}}]}]
    field = derive_engine_fields(content, catalog)[0]
    assert field["type"] == "string", "schema type must win or the queue flip 400s"
    assert field["pre_trained_field_id"] == "currency", "the seed is kept regardless of type"
    assert seed_type_overrides(derive_engine_fields(content, catalog))


# --- leniency for a mis-authored table column, reported rather than silent ---

def test_item_vocabulary_column_is_aliased_and_reported():
    catalog = [{"name": "table_column_description", "type": "string", "subtype": "",
                "multiline": "false"}]
    content = [{"category": "section", "id": "s", "children": [
        {"category": "multivalue", "id": "line_items", "rir_field_names": ["line_items"],
         "children": {"category": "tuple", "id": "line_item", "children": [
             {"category": "datapoint", "id": "item_description", "label": "Description",
              "type": "string", "rir_field_names": ["item_description"],
              "ui_configuration": {"type": "captured"}}]}}]}]
    fields = derive_engine_fields(content, catalog)
    assert fields[0]["pre_trained_field_id"] == "table_column_description"
    warnings = alias_warnings(fields)
    assert warnings and "should bind 'table_column_description'" in warnings[0]
