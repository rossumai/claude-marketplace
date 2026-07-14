from bulk_helpers import make_records, run_import

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
    monkeypatch.setattr(cbi, "DS_BATCH_SIZE", 2)
    results = [cbi.BatchResult(2, 0, 0), cbi.BatchResult(0, 2, 0)]
    run_import(monkeypatch, tmp_path, [make_records(1, 2), make_records(3, 4), []],
               batch_results=results)
    assert "[NOTICE]" not in capsys.readouterr().out
