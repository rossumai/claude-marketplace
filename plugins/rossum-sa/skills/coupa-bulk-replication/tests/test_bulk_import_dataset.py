"""import_dataset: accounting, resume compatibility, id_key guards, and the
end-to-end dedup pins (real insert_batch against the stateful FakeDS)."""
import pytest

from bulk_helpers import FakeDS, make_records, run_import

import coupa_bulk_import as cbi


# ── accounting & state-file contracts ────────────────────────────────────────

def test_fresh_run_records_total_inserted(monkeypatch, tmp_path):
    saved, calls = run_import(monkeypatch, tmp_path,
                              [make_records(1, 2), make_records(3), []])
    st = saved["users"]
    assert st["completed"] is True
    assert st["total_processed"] == 3
    assert st["total_inserted"] == 3
    assert calls["fetch_offsets"] == [0, 2, 3]


def test_duplicates_counted_processed_not_inserted(monkeypatch, tmp_path):
    results = [cbi.BatchResult(inserted=1, duplicates=2, failed=0)]
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []],
                          batch_results=results)
    st = saved["users"]
    assert st["total_processed"] == 3
    assert st["total_inserted"] == 1


def test_resume_old_state_without_total_inserted(monkeypatch, tmp_path):
    old = {"users": {"offset": 2, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 2}}
    saved, calls = run_import(monkeypatch, tmp_path, [make_records(3), []],
                              resume=True, state=old, db_count=5)
    st = saved["users"]
    assert calls["fetch_offsets"][0] == 2                     # offset honored
    assert st["total_processed"] == 3
    assert st["total_inserted"] == 6   # seeded from live DB count (5) + 1 new, NOT
                                        # from the inflated old total_processed (2)
    assert st["anchor_updated_at"] == "2026-07-10T00:00:00Z"  # anchor reused


# ── misconfigured id_key fail-fast ───────────────────────────────────────────

def test_fresh_run_fails_fast_on_all_falsy_first_page(monkeypatch, tmp_path):
    # a typo'd id_key would otherwise blind-load the whole dataset with
    # dedup never engaging
    page = [{"identifier": 1, "updated_at": "t"},
            {"identifier": 2, "updated_at": "t"}]
    with pytest.raises(SystemExit) as exc:
        run_import(monkeypatch, tmp_path, [page, []])
    assert "id_key" in str(exc.value)
    assert "'id'" in str(exc.value)
    assert "users" in str(exc.value)


def test_resumed_run_does_not_fail_fast_on_falsy_page(monkeypatch, tmp_path):
    # a resumed run aborting on the guard would strand supervision mid-load
    old = {"users": {"offset": 5, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 5,
                     "total_inserted": 5}}
    page = [{"identifier": 6, "updated_at": "t"}]
    saved, _ = run_import(monkeypatch, tmp_path, [page, []],
                          resume=True, state=old)
    assert saved["users"]["completed"] is True     # no SystemExit


# ── end-to-end dedup pins (real insert_batch against a stateful DS stub) ────

def test_resume_overlap_absorbed_end_to_end(monkeypatch, tmp_path):
    """The resume-boundary re-fetch overlaps records the previous run already
    inserted — they must dedupe, never double-insert."""
    ds = FakeDS(preloaded=(1, 2))
    old = {"users": {"offset": 1, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 2,
                     "total_inserted": 2}}
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(2, 3), []],
                          resume=True, state=old, ds_session=ds)
    assert ds.value_counts() == {1: 1, 2: 1, 3: 1}
    assert saved["users"]["total_inserted"] == 3


def test_smoke_leftover_absorbed_by_full_run_any_batch(monkeypatch, tmp_path):
    """Leftovers of a hard-killed smoke run dedupe no matter which batch of
    a later full run re-fetches them — every batch is existence-checked
    (Coupa churn can push leftovers past the first batch)."""
    ds = FakeDS(preloaded=(7,))
    saved, _ = run_import(monkeypatch, tmp_path,
                          [make_records(1, 2), make_records(7, 3), []],
                          ds_session=ds, ds_batch_size=2)
    assert ds.value_counts() == {1: 1, 2: 1, 3: 1, 7: 1}
    assert saved["users"]["total_inserted"] == 3
    assert saved["users"]["completed"] is True


def test_custom_id_key_dedupes_end_to_end(monkeypatch, tmp_path):
    """The dataset's configured id_key — not a hardcoded 'id' — drives
    dedup all the way through import_dataset and insert_batch."""
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "number", "scope": "s", "fields": ["number"]}}
    ds = FakeDS(id_key="number", preloaded=(7,))
    pages = [[{"number": 7, "updated_at": "t"}, {"number": 8, "updated_at": "t"}], []]
    saved, _ = run_import(monkeypatch, tmp_path, pages,
                          datasets=datasets, ds_session=ds)
    assert ds.value_counts() == {7: 1, 8: 1}       # 7 deduped on "number"
    assert saved["users"]["total_inserted"] == 1
