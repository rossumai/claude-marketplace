"""Unique-partial-index verification — the root duplicate guarantee.

Full runs ABORT on a confirmed-missing/non-partial index unless
--no-unique-index-ok; a failed listing only warns (unknown ≠ absent).
"""
import sys

import pytest
import requests

from bulk_helpers import FakeDS, make_records, run_import, write_config

import coupa_bulk_import as cbi


# ── _index_status: one representative per outcome ────────────────────────────

def test_unique_partial_index_is_ok():
    ix = {"name": "__id_unique_idx", "key": {"id": 1}, "unique": True,
          "partialFilterExpression": {"id": {"$exists": True}}}
    assert cbi._index_status(ix, "id") == "ok"


def test_unique_without_partial_filter_is_non_partial():
    # would reject the second id-less document as a duplicate null
    ix = {"name": "x", "key": {"id": 1}, "unique": True}
    assert cbi._index_status(ix, "id") == "non_partial"


def test_non_unique_index_is_irrelevant():
    assert cbi._index_status({"name": "i", "key": {"id": 1}}, "id") is None


# ── consequential on full runs: abort unless --no-unique-index-ok ────────────

def _missing_index_ds():
    ds = FakeDS()
    ds.indexes = [{"name": "_id_", "key": {"_id": 1}}]
    return ds


def test_full_run_aborts_on_missing_unique_index(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        run_import(monkeypatch, tmp_path, [make_records(1), []],
                   ds_session=_missing_index_ds())
    assert "--no-unique-index-ok" in str(exc.value)
    assert "unique partial index" in str(exc.value)


def test_full_run_aborts_on_non_partial_index(monkeypatch, tmp_path):
    ds = FakeDS()
    ds.indexes = [{"name": "id_unique", "key": {"id": 1}, "unique": True}]
    with pytest.raises(SystemExit):
        run_import(monkeypatch, tmp_path, [make_records(1), []], ds_session=ds)


def test_full_run_proceeds_with_override_flag(monkeypatch, tmp_path):
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          ds_session=_missing_index_ds(),
                          no_unique_index_ok=True)
    assert saved["users"]["completed"] is True


def test_full_run_proceeds_when_listing_fails(monkeypatch, tmp_path):
    """'unknown' is not confirmed-absent — soft warn, no abort."""

    class ListingBrokenDS(FakeDS):
        def post(self, url, json=None, timeout=None):
            if url.endswith("/indexes/list"):
                raise requests.exceptions.ConnectionError("boom")
            return super().post(url, json=json, timeout=timeout)

    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          ds_session=ListingBrokenDS())
    assert saved["users"]["completed"] is True


def test_resumed_run_also_verifies_index(monkeypatch, tmp_path):
    ds = _missing_index_ds()
    old = {"users": {"offset": 0, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 0,
                     "total_inserted": 0}}
    with pytest.raises(SystemExit):
        run_import(monkeypatch, tmp_path, [make_records(1), []],
                   resume=True, state=old, ds_session=ds)


# ── CLI wiring ───────────────────────────────────────────────────────────────

def test_main_threads_no_unique_index_ok_to_import(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    seen = {}

    def fake_import(key, limit, resume, state, ds_session, state_path,
                    username=None, password=None, no_unique_index_ok=False):
        seen[key] = no_unique_index_ok

    monkeypatch.setattr(cbi, "import_dataset", fake_import)
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--dataset", "users",
                         "--config", str(path), "--no-unique-index-ok"])
    cbi.main()
    assert seen == {"users": True}
