from bulk_helpers import FakeDS, make_records, run_import

import coupa_bulk_import as cbi


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


def test_fresh_run_on_nonempty_collection_warns(monkeypatch, tmp_path, capsys):
    run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []], db_count=7)
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "already holds 7" in out


def test_fresh_run_on_empty_collection_no_warning(monkeypatch, tmp_path, capsys):
    run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []], db_count=0)
    out = capsys.readouterr().out
    assert "[WARN]" not in out


def test_resume_run_never_warns_about_preexisting(monkeypatch, tmp_path, capsys):
    old = {"users": {"offset": 0, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 0,
                     "total_inserted": 0}}
    run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []],
               resume=True, state=old, db_count=7)
    out = capsys.readouterr().out
    assert "[WARN]" not in out


def test_fresh_run_first_batch_all_dups_prints_notice(monkeypatch, tmp_path, capsys):
    results = [cbi.BatchResult(inserted=0, duplicates=3, failed=0)]
    run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []],
               batch_results=results)
    assert "[NOTICE]" in capsys.readouterr().out


def test_resume_run_all_dups_no_notice(monkeypatch, tmp_path, capsys):
    old = {"users": {"offset": 0, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 0}}
    results = [cbi.BatchResult(inserted=0, duplicates=3, failed=0)]
    run_import(monkeypatch, tmp_path, [make_records(1, 2, 3), []],
               batch_results=results, resume=True, state=old)
    assert "[NOTICE]" not in capsys.readouterr().out


def test_fresh_run_few_dups_no_notice(monkeypatch, tmp_path, capsys):
    results = [cbi.BatchResult(inserted=9, duplicates=1, failed=0)]
    run_import(monkeypatch, tmp_path, [make_records(*range(1, 11)), []],
               batch_results=results)
    assert "[NOTICE]" not in capsys.readouterr().out


def test_notice_only_checked_on_first_flush(monkeypatch, tmp_path, capsys):
    results = [cbi.BatchResult(2, 0, 0), cbi.BatchResult(0, 2, 0)]
    run_import(monkeypatch, tmp_path, [make_records(1, 2), make_records(3, 4), []],
               batch_results=results, ds_batch_size=2)
    assert "[NOTICE]" not in capsys.readouterr().out


def test_id_key_from_config_threaded_to_insert_batch(monkeypatch, tmp_path):
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "number", "scope": "s", "fields": ["number"]}}
    _, calls = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          datasets=datasets)
    assert calls["id_keys"] == ["number"]


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
