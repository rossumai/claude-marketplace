import requests

from bulk_helpers import FakeDS, make_records, run_import

import coupa_bulk_import as cbi


# ── _index_covers_unique_id ──────────────────────────────────────────────────

def test_unique_index_detected_top_level():
    ix = {"name": "__id_unique_idx", "key": {"id": 1}, "unique": True,
          "partialFilterExpression": {"id": {"$exists": True}}}
    assert cbi._index_covers_unique_id(ix, "id")


def test_unique_index_detected_in_options():
    ix = {"name": "x", "keys": {"id": 1},
          "options": {"unique": True,
                      "partialFilterExpression": {"id": {"$exists": True}}}}
    assert cbi._index_covers_unique_id(ix, "id")


def test_non_unique_index_not_accepted():
    assert not cbi._index_covers_unique_id({"name": "i", "key": {"id": 1}}, "id")


def test_unique_index_on_other_field_not_accepted():
    ix = {"name": "i", "key": {"number": 1}, "unique": True}
    assert not cbi._index_covers_unique_id(ix, "id")


# ── verify_unique_index ──────────────────────────────────────────────────────

def test_verify_passes_quietly_when_index_present(capsys):
    ds = FakeDS()  # default indexes include the unique partial index
    assert cbi.verify_unique_index(ds, "users", "id") is True
    assert "[WARN]" not in capsys.readouterr().out
    assert ds.calls[0][1] == {"collectionName": "users", "nameOnly": False}


def test_verify_warns_when_index_missing(capsys):
    ds = FakeDS()
    ds.indexes = [{"name": "_id_", "key": {"_id": 1}}]
    assert cbi.verify_unique_index(ds, "users", "id") is False
    out = capsys.readouterr().out
    assert "NO unique index on 'id'" in out
    assert "__id_unique_idx" in out
    assert "partialFilterExpression" in out
    assert "NOT auto-created" in out     # pre-existing dups would fail the build


def test_verify_soft_warns_when_listing_fails(capsys):
    class Broken:
        def post(self, url, json=None, timeout=None):
            raise requests.exceptions.ConnectionError("boom")

    assert cbi.verify_unique_index(Broken(), "users", "id") is False
    out = capsys.readouterr().out
    assert "could not verify indexes" in out


# ── wired into every full (non-smoke) run ────────────────────────────────────

def test_full_run_warns_on_missing_unique_index(monkeypatch, tmp_path, capsys):
    ds = FakeDS()
    ds.indexes = [{"name": "_id_", "key": {"_id": 1}}]
    run_import(monkeypatch, tmp_path, [make_records(1), []], ds_session=ds)
    assert "NO unique index on 'id'" in capsys.readouterr().out


def test_full_run_quiet_when_unique_index_present(monkeypatch, tmp_path, capsys):
    run_import(monkeypatch, tmp_path, [make_records(1), []], ds_session=FakeDS())
    assert "NO unique index" not in capsys.readouterr().out


def test_resumed_run_also_verifies_index(monkeypatch, tmp_path, capsys):
    ds = FakeDS()
    ds.indexes = []
    old = {"users": {"offset": 0, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 0,
                     "total_inserted": 0}}
    run_import(monkeypatch, tmp_path, [make_records(1), []],
               resume=True, state=old, ds_session=ds)
    assert "NO unique index on 'id'" in capsys.readouterr().out
