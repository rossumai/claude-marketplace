"""--dataset resolution and state-file path selection (resume-critical)."""
from pathlib import Path

import pytest

import coupa_bulk_import as cbi

DATASETS = {"users": {}, "suppliers": {}, "purchase_orders": {}}


def test_all_returns_every_key():
    assert cbi.resolve_dataset_keys("all", DATASETS) == list(DATASETS)


def test_duplicate_keys_are_deduped_order_preserving():
    # a repeated key must not spawn two racing supervised children
    assert cbi.resolve_dataset_keys("users,users,suppliers", DATASETS) == ["users", "suppliers"]


def test_unknown_key_exits_with_available_list():
    with pytest.raises(SystemExit) as exc:
        cbi.resolve_dataset_keys("users,bogus", DATASETS)
    assert "bogus" in str(exc.value)
    assert "purchase_orders" in str(exc.value)


def test_default_state_path_selection():
    # Wrong path = a resume that silently starts from scratch. The rules:
    # 'all' shares the file even with a one-dataset config (regression);
    # an explicit single dataset gets its own file (supervised children
    # rely on this); comma lists share; explicit --state-file always wins.
    assert cbi.default_state_path("all", ["users"], None) == Path("coupa_import_state.json")
    assert cbi.default_state_path("users", ["users"], None) == Path("coupa_import_state_users.json")
    assert cbi.default_state_path("users,suppliers", ["users", "suppliers"], None) \
        == Path("coupa_import_state.json")
    assert cbi.default_state_path("all", ["users"], "custom.json") == Path("custom.json")
