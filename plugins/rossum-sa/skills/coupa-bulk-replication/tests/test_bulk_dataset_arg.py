from pathlib import Path

import pytest

import coupa_bulk_import as cbi

DATASETS = {"users": {}, "suppliers": {}, "purchase_orders": {}}


def test_all_returns_every_key():
    assert cbi.resolve_dataset_keys("all", DATASETS) == list(DATASETS)


def test_single_key():
    assert cbi.resolve_dataset_keys("users", DATASETS) == ["users"]


def test_comma_list_with_spaces():
    assert cbi.resolve_dataset_keys("users, suppliers", DATASETS) == ["users", "suppliers"]


def test_duplicate_keys_are_deduped_order_preserving():
    assert cbi.resolve_dataset_keys("users,users,suppliers", DATASETS) == ["users", "suppliers"]


def test_unknown_key_exits_with_available_list():
    with pytest.raises(SystemExit) as exc:
        cbi.resolve_dataset_keys("users,bogus", DATASETS)
    assert "bogus" in str(exc.value)
    assert "purchase_orders" in str(exc.value)


def test_state_path_all_with_single_dataset_config_stays_shared():
    # regression: 'all' must use the shared file even when the config has one dataset
    assert cbi.default_state_path("all", ["users"], None) == Path("coupa_import_state.json")


def test_state_path_explicit_single_key_gets_own_file():
    assert cbi.default_state_path("users", ["users"], None) == Path("coupa_import_state_users.json")


def test_state_path_comma_list_stays_shared():
    assert cbi.default_state_path("users,suppliers", ["users", "suppliers"], None) == Path("coupa_import_state.json")


def test_state_path_explicit_flag_wins():
    assert cbi.default_state_path("all", ["users"], "custom.json") == Path("custom.json")
