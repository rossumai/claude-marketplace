import requests

from bulk_helpers import StubResponse

import coupa_bulk_import as cbi


class StubSession:
    """Routes POSTs by URL: the existence check (/data/aggregate) is
    answered from `existing` (or a dedicated check_queue for exercising
    retries/exceptions on the check itself); /data/insert_many is answered
    from the insert queue. Yields queued responses; raises queued
    exceptions."""

    def __init__(self, insert_queue=(), existing=None, check_queue=None):
        self.insert_queue = list(insert_queue)
        self.check_queue = list(check_queue) if check_queue is not None else None
        self.existing = existing or set()
        self.calls = []  # list of (url, json) for every POST made

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if url.endswith("/data/aggregate"):
            if self.check_queue is not None:
                item = self.check_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return StubResponse({"code": "ok",
                                 "result": [{"_id": i} for i in self.existing]})
        item = self.insert_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _check_found(*ids):
    """Existence-check response: these id values are present."""
    return StubResponse({"code": "ok", "result": [{"_id": i} for i in ids]})


def _resp(inserted_ids, write_errors=()):
    return StubResponse({"result": {"inserted_ids": list(inserted_ids),
                                    "write_errors": list(write_errors)}})


def _recs(*ids):
    return [{"id": i} for i in ids]


def _insert_calls(session):
    return [c for c in session.calls if c[0].endswith("/data/insert_many")]


def _check_calls(session):
    return [c for c in session.calls if c[0].endswith("/data/aggregate")]


# ── BatchResult hygiene ──────────────────────────────────────────────────────────

def test_batchresult_default_inserted_values_not_shared():
    r1 = cbi.BatchResult(1, 0, 0)
    r2 = cbi.BatchResult(1, 0, 0)
    r1.inserted_values.append(9)
    assert r2.inserted_values == []      # fresh list per instance


def test_batchresult_copies_caller_iterable():
    src = [1]
    r = cbi.BatchResult(1, 0, 0, src)
    src.append(2)
    assert r.inserted_values == [1]      # no aliasing of the caller's list


# ── check-then-insert dedup keyed on the Coupa id FIELD ──────────────────────

def test_all_new_batch_inserts_everything():
    s = StubSession([_resp([1, 2, 3])], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0,
                                inserted_values=[1, 2, 3])
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert len(inserts[0][1]["documents"]) == 3


def test_existence_check_uses_distinct_aggregate_on_id_field():
    # aggregate $match+$group (distinct values), NOT find+limit — duplicate
    # copies of one id must not truncate the answer.
    s = StubSession([_resp([2])], existing={1})
    cbi.insert_batch(s, "c", _recs(1, 2))
    pipeline = _check_calls(s)[0][1]["pipeline"]
    assert pipeline == [{"$match": {"id": {"$in": [1, 2]}}},
                        {"$group": {"_id": "$id"}}]


def test_custom_id_key_used_in_query():
    s = StubSession([_resp([7])], existing=set())
    cbi.insert_batch(s, "c", [{"number": 7}], id_key="number")
    pipeline = _check_calls(s)[0][1]["pipeline"]
    assert pipeline == [{"$match": {"number": {"$in": [7]}}},
                        {"$group": {"_id": "$number"}}]


def test_existence_check_runs_on_every_batch_call():
    # The check is unconditional — no caller can opt out (a boundary-scoped
    # variant was rejected in review: mid-run anchor-window entries and
    # 401-heal retries land in "provably fresh" batches).
    s = StubSession([_resp([1])], existing=set())
    cbi.insert_batch(s, "c", _recs(1))
    assert len(_check_calls(s)) == 1


def test_documents_inserted_without_underscore_id():
    s = StubSession([_resp([1, 2])], existing=set())
    records = _recs(1, 2)
    cbi.insert_batch(s, "c", records)
    docs = _insert_calls(s)[0][1]["documents"]
    assert docs == records
    assert all("_id" not in d for d in docs)  # auto ObjectId, like the extension


def test_partial_duplicate_only_inserts_new_records(capsys):
    s = StubSession([_resp([2])], existing={1})
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[2])
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert inserts[0][1]["documents"] == [{"id": 2}]
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out
    assert "[WARN]" not in out


def test_all_duplicate_skips_insert_entirely(capsys):
    s = StubSession([], existing={1, 2, 3})
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=0, duplicates=3, failed=0,
                                inserted_values=[])
    assert len(s.calls) == 1  # only the check call — no insert_many at all
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out


def test_records_without_id_are_never_filtered():
    s = StubSession([_resp([1, 2])], existing=set())
    records = [{"id": 1}, {"no_id": True}]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=0,
                                inserted_values=[1])
    checks = _check_calls(s)
    assert len(checks) == 1
    assert checks[0][1]["pipeline"][0]["$match"]["id"]["$in"] == [1]
    inserts = _insert_calls(s)
    assert inserts[0][1]["documents"] == records


def test_falsy_ids_never_enter_dedup_queries():
    # None, "" and 0 all count as missing — a shared falsy value would
    # collapse distinct records; such records always insert.
    s = StubSession([_resp([1, 2, 3, 4])], existing=set())
    records = [{"id": 1}, {"id": None}, {"id": ""}, {"id": 0}]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=4, duplicates=0, failed=0,
                                inserted_values=[1])
    assert _check_calls(s)[0][1]["pipeline"][0]["$match"]["id"]["$in"] == [1]
    assert _insert_calls(s)[0][1]["documents"] == records


def test_all_falsy_batch_skips_check_entirely():
    s = StubSession([_resp([1, 2])], existing=set())
    records = [{"id": None}, {"id": 0}]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=0,
                                inserted_values=[])
    assert _check_calls(s) == []      # nothing usable to query on


def test_repeated_id_within_batch_counts_as_duplicate():
    s = StubSession([_resp([1])], existing=set())
    r = cbi.insert_batch(s, "c", [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[1])
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert inserts[0][1]["documents"] == [{"id": 1, "v": "a"}]


# ── belt: 200-with-write_errors still handled ────────────────────────────────

def test_duplicates_by_code_are_informational(capsys):
    errs = [{"code": 11000, "errmsg": "duplicate key"}] * 2
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    # write_errors carry no usable index -> conservative empty inserted_values
    assert r == cbi.BatchResult(inserted=1, duplicates=2, failed=0,
                                inserted_values=[])
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out
    assert "[WARN]" not in out


def test_duplicates_by_errmsg_string():
    errs = [{"errmsg": "E11000 duplicate key error"}]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r.duplicates == 1 and r.failed == 0


def test_real_failures_keep_warn(capsys):
    errs = [{"code": 11000, "errmsg": "dup"}, {"code": 2, "errmsg": "too large"}]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=1,
                                inserted_values=[])
    out = capsys.readouterr().out
    assert "[WARN] 1 document(s) failed" in out
    assert "too large" in out


def test_write_errors_with_indices_attribute_inserted_values():
    # every error carries a usable index → the failed doc (idx 1) is
    # excluded from inserted_values, the rest are safe to smoke-delete
    errs = [{"code": 2, "errmsg": "too large", "index": 1}]
    s = StubSession([_resp([1, 3], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=1,
                                inserted_values=[1, 3])


def test_bare_string_write_error_duplicate():
    errs = ["E11000 duplicate key error"]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[])


def test_bare_string_write_error_non_duplicate():
    errs = ["boom"]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=1,
                                inserted_values=[])


# ── opaque batch 400 after dedupe → per-record poison-doc isolation ─────────

def test_batch_400_isolates_poison_doc(capsys):
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    poison = StubResponse({"code": "error", "message": "bad value for doc 2"}, status=400)
    s = StubSession(
        [batch_400, _resp([1]), poison, _resp([3])],
        existing=set(),
    )
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=1,
                                inserted_values=[1, 3])
    out = capsys.readouterr().out
    assert "poison document skipped (id=2)" in out
    assert "[WARN] 1 document(s) failed in this batch (isolated per-record)" in out


def test_batch_400_all_singles_succeed():
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    s = StubSession([batch_400, _resp([1]), _resp([2]), _resp([3])], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0,
                                inserted_values=[1, 2, 3])


def test_batch_400_fallback_401_raises():
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    unauthorized = StubResponse({"code": "error", "message": "unauthorized"}, status=401)
    s = StubSession([batch_400, unauthorized], existing=set())
    try:
        cbi.insert_batch(s, "c", _recs(1, 2))
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass


def test_batch_400_single_400_with_existing_id_is_racing_duplicate(capsys):
    """The unique-index layer rejects a racing duplicate with the same
    opaque 400 a poison doc gets — _insert_singly classifies via a post-hoc
    existence check: present → duplicate, not failed."""
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    dup_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                           status=400)
    s = StubSession(
        insert_queue=[batch_400, _resp([1]), dup_400, _resp([3])],
        # initial check: nothing exists; post-400 classification: 2 exists
        check_queue=[_check_found(), _check_found(2)],
    )
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=2, duplicates=1, failed=0,
                                inserted_values=[1, 3])
    out = capsys.readouterr().out
    assert "poison" not in out
    assert "1 duplicate(s) skipped" in out


def test_batch_400_single_400_with_falsy_id_stays_poison(capsys):
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    poison = StubResponse({"code": "error", "message": "bad doc"}, status=400)
    s = StubSession(
        insert_queue=[batch_400, _resp([1]), poison],
        check_queue=[_check_found()],   # no classification check for a falsy id
    )
    r = cbi.insert_batch(s, "c", [{"id": 1}, {"id": None}])
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=1,
                                inserted_values=[1])
    assert "poison document skipped (id=None)" in capsys.readouterr().out


def test_batch_400_with_duplicates_still_reports_them(capsys):
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    s = StubSession([batch_400, _resp([2])], existing={1})
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[2])
    out = capsys.readouterr().out
    assert "1 duplicate(s) skipped" in out


# ── retry wraps the whole check+insert flow ──────────────────────────────────

def test_transient_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession([requests.exceptions.ConnectionError("boom"), _resp([1])],
                    existing=set())
    r = cbi.insert_batch(s, "c", _recs(1))
    assert r.inserted == 1
    assert len(_insert_calls(s)) == 2


def test_retry_recovers_partially_applied_insert(monkeypatch):
    """A transient error strikes mid insert_many, but the server had already
    persisted the first few documents. The re-run existence check on retry
    sees them as 'existing' — they must be counted as inserted (recovered),
    not misclassified as pre-existing duplicates."""
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession(
        insert_queue=[requests.exceptions.ConnectionError("boom"), _resp([4, 5])],
        check_queue=[_check_found(), _check_found(1, 2, 3)],
    )
    records = _recs(1, 2, 3, 4, 5)
    r = cbi.insert_batch(s, "c", records)
    # recovered records (1-3) are NOT in inserted_values: only the final
    # successful attempt's landings are safe for smoke deletion
    assert r == cbi.BatchResult(inserted=5, duplicates=0, failed=0,
                                inserted_values=[4, 5])
    inserts = _insert_calls(s)
    assert len(inserts) == 2  # attempt 1 (raised) + attempt 2 (succeeded)
    assert inserts[-1][1]["documents"] == [{"id": 4}, {"id": 5}]


def test_retry_does_not_miscount_preexisting_as_recovered(monkeypatch):
    """Records already present on attempt 1 stay duplicates on the retry —
    only records that APPEARED between attempts count as recovered."""
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession(
        insert_queue=[requests.exceptions.ConnectionError("boom"), _resp([3])],
        check_queue=[_check_found(1), _check_found(1, 2)],
    )
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    # 1 = pre-existing duplicate; 2 = recovered (persisted by attempt 1); 3 = inserted
    assert r == cbi.BatchResult(inserted=2, duplicates=1, failed=0,
                                inserted_values=[3])


def test_transient_error_on_check_retries_whole_flow(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession([_resp([1])],
                    check_queue=[requests.exceptions.ConnectionError("boom"),
                                 _check_found()])
    r = cbi.insert_batch(s, "c", _recs(1))
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=0,
                                inserted_values=[1])
    assert len(_check_calls(s)) == 2
    assert len(_insert_calls(s)) == 1
