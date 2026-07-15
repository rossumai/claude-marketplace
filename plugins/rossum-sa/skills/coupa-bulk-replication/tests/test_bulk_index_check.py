import sys

import pytest
import requests

from bulk_helpers import FakeDS, make_records, run_import, write_config

import coupa_bulk_import as cbi


# ── _index_status ────────────────────────────────────────────────────────────

def test_unique_partial_index_top_level_is_ok():
    ix = {"name": "__id_unique_idx", "key": {"id": 1}, "unique": True,
          "partialFilterExpression": {"id": {"$exists": True}}}
    assert cbi._index_status(ix, "id") == "ok"


def test_unique_partial_index_in_options_is_ok():
    ix = {"name": "x", "keys": {"id": 1},
          "options": {"unique": True,
                      "partialFilterExpression": {"id": {"$exists": True}}}}
    assert cbi._index_status(ix, "id") == "ok"


def test_any_partial_filter_mentioning_id_key_is_ok():
    ix = {"name": "x", "key": {"id": 1}, "unique": True,
          "partialFilterExpression": {"id": {"$gt": 0}, "active": True}}
    assert cbi._index_status(ix, "id") == "ok"


def test_unique_without_partial_filter_is_non_partial():
    ix = {"name": "x", "key": {"id": 1}, "unique": True}
    assert cbi._index_status(ix, "id") == "non_partial"


def test_partial_filter_on_other_field_is_non_partial():
    ix = {"name": "x", "key": {"id": 1}, "unique": True,
          "partialFilterExpression": {"active": True}}
    assert cbi._index_status(ix, "id") == "non_partial"


def test_non_unique_index_is_irrelevant():
    assert cbi._index_status({"name": "i", "key": {"id": 1}}, "id") is None


def test_unique_index_on_other_field_is_irrelevant():
    ix = {"name": "i", "key": {"number": 1}, "unique": True}
    assert cbi._index_status(ix, "id") is None


# ── verify_unique_index ──────────────────────────────────────────────────────

def test_verify_passes_quietly_when_index_present(capsys):
    ds = FakeDS()  # default indexes include the unique partial index
    assert cbi.verify_unique_index(ds, "users", "id") == "ok"
    assert "[WARN]" not in capsys.readouterr().out
    assert ds.calls[0][1] == {"collectionName": "users", "nameOnly": False}


def test_verify_warns_when_index_missing(capsys):
    ds = FakeDS()
    ds.indexes = [{"name": "_id_", "key": {"_id": 1}}]
    assert cbi.verify_unique_index(ds, "users", "id") == "missing"
    out = capsys.readouterr().out
    assert "NO unique index on 'id'" in out
    assert "__id_unique_idx" in out
    assert "partialFilterExpression" in out
    assert "NOT auto-created" in out     # pre-existing dups would fail the build


def test_verify_flags_non_partial_index_distinctly(capsys):
    ds = FakeDS()
    ds.indexes = [{"name": "id_unique", "key": {"id": 1}, "unique": True}]
    assert cbi.verify_unique_index(ds, "users", "id") == "non_partial"
    out = capsys.readouterr().out
    assert "WITHOUT a partial filter" in out
    assert "duplicate null" in out
    assert "poison" in out
    assert "Drop it and recreate" in out


def test_verify_soft_warns_when_listing_fails(capsys):
    class Broken:
        def post(self, url, json=None, timeout=None):
            raise requests.exceptions.ConnectionError("boom")

    assert cbi.verify_unique_index(Broken(), "users", "id") == "unknown"
    out = capsys.readouterr().out
    assert "could not verify indexes" in out


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


def test_full_run_aborts_on_non_partial_index(monkeypatch, tmp_path, capsys):
    ds = FakeDS()
    ds.indexes = [{"name": "id_unique", "key": {"id": 1}, "unique": True}]
    with pytest.raises(SystemExit):
        run_import(monkeypatch, tmp_path, [make_records(1), []], ds_session=ds)
    assert "WITHOUT a partial filter" in capsys.readouterr().out


def test_full_run_proceeds_with_override_flag(monkeypatch, tmp_path, capsys):
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          ds_session=_missing_index_ds(),
                          no_unique_index_ok=True)
    assert saved["users"]["completed"] is True
    assert "NO unique index on 'id'" in capsys.readouterr().out  # still warned


def test_full_run_proceeds_when_listing_fails(monkeypatch, tmp_path, capsys):
    """'unknown' is not confirmed-absent — soft warn, no abort."""

    class ListingBrokenDS(FakeDS):
        def post(self, url, json=None, timeout=None):
            if url.endswith("/indexes/list"):
                raise requests.exceptions.ConnectionError("boom")
            return super().post(url, json=json, timeout=timeout)

    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          ds_session=ListingBrokenDS())
    assert saved["users"]["completed"] is True
    assert "could not verify indexes" in capsys.readouterr().out


def test_full_run_quiet_when_unique_index_present(monkeypatch, tmp_path, capsys):
    run_import(monkeypatch, tmp_path, [make_records(1), []], ds_session=FakeDS())
    assert "NO unique index" not in capsys.readouterr().out


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
