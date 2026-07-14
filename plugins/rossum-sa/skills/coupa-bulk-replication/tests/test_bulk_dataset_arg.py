import pytest

import coupa_bulk_import as cbi

DATASETS = {"users": {}, "suppliers": {}, "purchase_orders": {}}


def test_all_returns_every_key():
    assert cbi.resolve_dataset_keys("all", DATASETS) == list(DATASETS)


def test_single_key():
    assert cbi.resolve_dataset_keys("users", DATASETS) == ["users"]


def test_comma_list_with_spaces():
    assert cbi.resolve_dataset_keys("users, suppliers", DATASETS) == ["users", "suppliers"]


def test_unknown_key_exits_with_available_list():
    with pytest.raises(SystemExit) as exc:
        cbi.resolve_dataset_keys("users,bogus", DATASETS)
    assert "bogus" in str(exc.value)
    assert "purchase_orders" in str(exc.value)
