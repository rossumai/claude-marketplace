"""insert_batch: dedup accounting, retry recovery, and 400 classification.

Pins BEHAVIOR (what lands in the store, how it is counted, what smoke may
later delete) — not request shapes or log wording. The aggregate-vs-find
choice and per-batch check scope are pinned end-to-end by the FakeDS tests
in test_bulk_import_dataset.py / test_bulk_token_reread.py.
"""
import requests

from bulk_helpers import FakeDS, StubResponse

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


# ── BatchResult hygiene ──────────────────────────────────────────────────────

def test_batchresult_inserted_values_never_shared():
    r1 = cbi.BatchResult(1, 0, 0)
    r2 = cbi.BatchResult(1, 0, 0)
    r1.inserted_values.append(9)
    assert r2.inserted_values == []      # fresh list per instance
    src = [1]
    r3 = cbi.BatchResult(1, 0, 0, src)
    src.append(2)
    assert r3.inserted_values == [1]     # no aliasing of the caller's list


# ── dedup keyed on the Coupa id FIELD ────────────────────────────────────────

def test_all_new_batch_inserts_everything():
    s = StubSession([_resp([1, 2, 3])], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0,
                                inserted_values=[1, 2, 3])
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert len(inserts[0][1]["documents"]) == 3


def test_documents_inserted_without_underscore_id():
    # Structural invariant from review: records land exactly as received
    # (auto ObjectId) — identical to what the Coupa import extension writes.
    s = StubSession([_resp([1, 2])], existing=set())
    records = _recs(1, 2)
    cbi.insert_batch(s, "c", records)
    docs = _insert_calls(s)[0][1]["documents"]
    assert docs == records
    assert all("_id" not in d for d in docs)


def test_partial_duplicate_only_inserts_new_records():
    s = StubSession([_resp([2])], existing={1})
    r = cbi.insert_batch(s, "c", _recs(1, 2))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[2])
    assert _insert_calls(s)[0][1]["documents"] == [{"id": 2}]


def test_falsy_ids_never_enter_dedup_queries():
    # None, "" and 0 all count as missing — a shared falsy value would
    # collapse distinct records; such records always insert.
    s = StubSession([_resp([1, 2, 3, 4])], existing=set())
    records = [{"id": 1}, {"id": None}, {"id": ""}, {"id": 0}]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=4, duplicates=0, failed=0,
                                inserted_values=[1])
    checks = [c for c in s.calls if c[0].endswith("/data/aggregate")]
    assert checks[0][1]["pipeline"][0]["$match"]["id"]["$in"] == [1]
    assert _insert_calls(s)[0][1]["documents"] == records


def test_repeated_id_within_batch_counts_as_duplicate():
    s = StubSession([_resp([1])], existing=set())
    r = cbi.insert_batch(s, "c", [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0,
                                inserted_values=[1])
    assert _insert_calls(s)[0][1]["documents"] == [{"id": 1, "v": "a"}]


# ── belt: 200-with-write_errors accounting + inserted_values safety ─────────

def test_write_errors_without_indices_stay_conservative():
    # unattributable errors → inserted_values must be EMPTY, or smoke could
    # delete a record this call never landed
    errs = [{"code": 11000, "errmsg": "duplicate key"}] * 2
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=1, duplicates=2, failed=0,
                                inserted_values=[])


def test_real_write_errors_counted_as_failed():
    errs = [{"code": 11000, "errmsg": "dup"}, {"code": 2, "errmsg": "too large"}]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=1,
                                inserted_values=[])


def test_write_errors_with_indices_attribute_inserted_values():
    # every error carries a usable index → the failed doc (idx 1) is
    # excluded from inserted_values, the rest are safe to smoke-delete
    errs = [{"code": 2, "errmsg": "too large", "index": 1}]
    s = StubSession([_resp([1, 3], errs)], existing=set())
    r = cbi.insert_batch(s, "c", _recs(1, 2, 3))
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=1,
                                inserted_values=[1, 3])


# ── opaque batch 400 → per-record isolation + classification ────────────────

def test_batch_400_isolates_poison_doc():
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


def test_batch_400_single_400_with_existing_id_is_racing_duplicate():
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


def test_batch_400_fallback_401_raises():
    # a 401 mid-fallback must escape to the caller's token heal (which
    # re-invokes the checked path) — never be swallowed as a poison doc
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    unauthorized = StubResponse({"code": "error", "message": "unauthorized"}, status=401)
    s = StubSession([batch_400, unauthorized], existing=set())
    try:
        cbi.insert_batch(s, "c", _recs(1, 2))
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass


# ── transient-error retries: recovery without miscounting ────────────────────

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
    # the retry loop must wrap the existence check too — a transient blip
    # on the check must not kill an hours-long run
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession([_resp([1])],
                    check_queue=[requests.exceptions.ConnectionError("boom"),
                                 _check_found()])
    r = cbi.insert_batch(s, "c", _recs(1))
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=0,
                                inserted_values=[1])
    assert len(_insert_calls(s)) == 1


def test_insert_batch_retries_ds_429_then_succeeds(monkeypatch):
    # DS-side 429/503 join the retryable set — a rate blip on any DS call
    # inside the loop (check or insert) must not kill an hours-long run
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    ds = FakeDS()
    real_post = ds.post
    calls = {"n": 0}

    def flaky_post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return StubResponse({}, status=429)
        return real_post(url, json=json, timeout=timeout)

    ds.post = flaky_post
    result = cbi.insert_batch(ds, "users", [{"id": 1}], "id")
    assert result.inserted == 1
    assert ds.value_counts() == {1: 1}
