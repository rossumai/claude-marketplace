"""import_dataset: accounting, keyset resume, offset-era refusal, id_key
guards, and end-to-end dedup/coverage pins (real insert_batch vs FakeDS,
real keyset semantics vs FakeCoupa)."""
import json

import pytest

from bulk_helpers import FakeCoupa, FakeDS, make_records, run_import, write_config

import coupa_bulk_import as cbi


# ── accounting & state-file contracts ────────────────────────────────────────

def test_fresh_run_records_total_inserted_and_cursor(monkeypatch, tmp_path):
    saved, calls = run_import(monkeypatch, tmp_path,
                              [make_records(9, 8), make_records(7), []])
    st = saved["users"]
    assert st["completed"] is True
    assert st["total_processed"] == 3
    assert st["total_inserted"] == 3
    assert st["last_id"] == 7                      # cursor = min id flushed
    assert calls["cursors"] == [None, 8, 7]        # cursor advances per page


def test_duplicates_counted_processed_not_inserted(monkeypatch, tmp_path):
    results = [cbi.BatchResult(inserted=1, duplicates=2, failed=0)]
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(3, 2, 1), []],
                          batch_results=results)
    st = saved["users"]
    assert st["total_processed"] == 3
    assert st["total_inserted"] == 1


def test_resume_continues_from_last_id(monkeypatch, tmp_path):
    old = {"users": {"last_id": 50, "anchor_updated_at": "2026-07-10T00:00:00Z",
                     "last_updated_at": "x", "total_processed": 2,
                     "total_inserted": 2}}
    saved, calls = run_import(monkeypatch, tmp_path, [make_records(40), []],
                              resume=True, state=old)
    assert calls["cursors"][0] == 50                       # cursor honored
    st = saved["users"]
    assert st["total_processed"] == 3
    assert st["anchor_updated_at"] == "2026-07-10T00:00:00Z"  # anchor reused


def test_resume_refuses_offset_era_state(monkeypatch, tmp_path):
    old = {"users": {"offset": 200000, "anchor_updated_at": "t",
                     "total_processed": 200000, "total_inserted": 200000}}
    with pytest.raises(SystemExit) as exc:
        run_import(monkeypatch, tmp_path, [[]], resume=True, state=old)
    msg = str(exc.value)
    assert "last_id" in msg and "delete" in msg.lower()


def test_resume_completed_dataset_short_circuits(monkeypatch, tmp_path):
    # a finished offset-era dataset must NOT be refused — nothing left to do
    old = {"users": {"offset": 5, "total_processed": 5, "completed": True}}
    saved, calls = run_import(monkeypatch, tmp_path, [], resume=True, state=old)
    assert calls["cursors"] == []                  # no Coupa call at all
    assert saved == {}                             # returned early, wrote nothing


# ── misconfigured id_key fail-fast ───────────────────────────────────────────

def test_fresh_run_fails_fast_on_all_falsy_first_page(monkeypatch, tmp_path):
    # a typo'd id_key would otherwise blind-load the whole dataset with
    # dedup never engaging
    page = [{"id": 1, "identifier": 1, "updated_at": "t"},
            {"id": 2, "identifier": 2, "updated_at": "t"}]
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "number", "scope": "s", "fields": ["number"]}}
    with pytest.raises(SystemExit) as exc:
        run_import(monkeypatch, tmp_path, [page, []], datasets=datasets)
    assert "id_key" in str(exc.value)


def test_resumed_run_does_not_fail_fast_on_falsy_page(monkeypatch, tmp_path):
    # a resumed run aborting on the guard would strand supervision mid-load
    old = {"users": {"last_id": 7, "anchor_updated_at": "t",
                     "last_updated_at": "x", "total_processed": 5,
                     "total_inserted": 5}}
    page = [{"id": 6, "identifier": 6, "updated_at": "t"}]
    saved, _ = run_import(monkeypatch, tmp_path, [page, []],
                          resume=True, state=old)
    assert saved["users"]["completed"] is True     # no SystemExit


# ── end-to-end pins: real keyset semantics + real insert_batch ───────────────

def test_full_crawl_lands_every_record_exactly_once(monkeypatch, tmp_path):
    """Gap-heavy id space, small pages: every record lands once, none missed."""
    ids = [1, 2, 50, 51, 52, 900, 1000, 5000]
    ds = FakeDS()
    saved, _ = run_import(monkeypatch, tmp_path,
                          fake_coupa=FakeCoupa(ids, page_size=3),
                          ds_session=ds, ds_batch_size=3)
    assert ds.value_counts() == {i: 1 for i in ids}
    assert saved["users"]["total_inserted"] == len(ids)
    assert saved["users"]["completed"] is True


def test_kill_and_resume_double_inserts_nothing(monkeypatch, tmp_path):
    """Kill after the first flush; resume from the persisted cursor. The
    overlap of buffered-but-unflushed pages re-fetches; dedup absorbs it —
    zero duplicates, zero missing records (the core integrity invariant)."""
    ids = [10, 20, 30, 40, 50, 60]
    ds = FakeDS()
    coupa = FakeCoupa(ids, page_size=2)

    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path, ds_batch_size=4))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "coupa-token")
    coupa.install(monkeypatch)

    real_insert = cbi.insert_batch
    calls = {"n": 0}

    def killing_insert(session, collection, records, id_key="id", _retries=5):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt          # kill mid-run, after state save #1
        return real_insert(session, collection, records, id_key)

    state_path = tmp_path / "state.json"

    # Run 1: batch size 4 -> flush #1 covers ids 60,50,40,30; killed on #2.
    monkeypatch.setattr(cbi, "insert_batch", killing_insert)
    with pytest.raises(KeyboardInterrupt):
        cbi.import_dataset("users", None, False, {}, ds, state_path=state_path)

    # Run 2: resume from the state the first flush persisted.
    monkeypatch.setattr(cbi, "insert_batch", real_insert)
    state = json.loads(state_path.read_text())
    cbi.import_dataset("users", None, True, state, ds, state_path=state_path)

    saved = json.loads(state_path.read_text())["users"]
    assert ds.value_counts() == {i: 1 for i in ids}    # no dupes, none missing
    assert saved["completed"] is True


def test_partition_range_crawls_only_its_slice(monkeypatch, tmp_path):
    """A pre-seeded partition state must fetch only (id_gt, id_lte] — ids
    outside the slice stay untouched."""
    ids = [1, 2, 3, 10, 11, 12, 20, 21, 22]
    ds = FakeDS()
    seeded = {"users": {"anchor_updated_at": "2026-07-10T00:00:00Z",
                        "last_id": 13,                       # id_lte + 1
                        "partition": {"index": 2, "of": 3,
                                      "id_gt": 3, "id_lte": 12},
                        "total_processed": 0, "total_inserted": 0}}
    saved, _ = run_import(monkeypatch, tmp_path,
                          fake_coupa=FakeCoupa(ids, page_size=2),
                          ds_session=ds, resume=True, state=seeded)
    assert ds.value_counts() == {10: 1, 11: 1, 12: 1}
    st = saved["users"]
    assert st["completed"] is True
    assert st["partition"]["id_gt"] == 3           # partition block persisted


def test_custom_id_key_dedupes_end_to_end(monkeypatch, tmp_path):
    """The dataset's configured id_key — not the pagination id — drives dedup."""
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "number", "scope": "s", "fields": ["number"]}}
    ds = FakeDS(id_key="number", preloaded=(107,))
    coupa = FakeCoupa([7, 8])
    coupa._rec = lambda i: {"id": i, "number": 100 + i, "updated_at": "t"}
    saved, _ = run_import(monkeypatch, tmp_path, fake_coupa=coupa,
                          datasets=datasets, ds_session=ds)
    assert ds.value_counts() == {107: 1, 108: 1}   # 107 deduped on "number"
    assert saved["users"]["total_inserted"] == 1
