import requests

import coupa_bulk_import as cbi


class StubResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class StubSession:
    """Routes POSTs by URL: /data/find answered from `existing` (or a
    dedicated find_queue for exercising retries/exceptions on the find
    call itself); /data/insert_many answered from the insert queue.
    Yields queued responses; raises queued exceptions."""

    def __init__(self, insert_queue=(), existing=None, find_queue=None):
        self.insert_queue = list(insert_queue)
        self.find_queue = list(find_queue) if find_queue is not None else None
        self.existing = existing or set()
        self.calls = []  # list of (url, json) for every POST made

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if url.endswith("/data/find"):
            if self.find_queue is not None:
                item = self.find_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return StubResponse({"code": "ok",
                                 "result": [{"_id": i} for i in self.existing]})
        item = self.insert_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(inserted_ids, write_errors=()):
    return StubResponse({"result": {"inserted_ids": list(inserted_ids),
                                    "write_errors": list(write_errors)}})


def _insert_calls(session):
    return [c for c in session.calls if c[0].endswith("/data/insert_many")]


def _find_calls(session):
    return [c for c in session.calls if c[0].endswith("/data/find")]


# ── check-then-insert dedup ──────────────────────────────────────────────────

def test_all_new_batch_inserts_everything():
    s = StubSession([_resp([1, 2, 3])], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0)
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert len(inserts[0][1]["documents"]) == 3


def test_partial_duplicate_only_inserts_new_records(capsys):
    s = StubSession([_resp([2])], existing={1})
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0)
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert inserts[0][1]["documents"] == [{"_id": 2}]
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out
    assert "[WARN]" not in out


def test_all_duplicate_skips_insert_entirely(capsys):
    s = StubSession([], existing={1, 2, 3})
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=0, duplicates=3, failed=0)
    assert len(s.calls) == 1  # only the find call — no insert_many at all
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out


def test_records_without_id_are_never_filtered():
    s = StubSession([_resp([1, 2])], existing=set())
    records = [{"_id": 1}, {"no_id": True}]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=0)
    find_calls = _find_calls(s)
    assert len(find_calls) == 1
    assert find_calls[0][1]["query"]["_id"]["$in"] == [1]
    inserts = _insert_calls(s)
    assert inserts[0][1]["documents"] == records


def test_repeated_id_within_batch_counts_as_duplicate():
    s = StubSession([_resp([1])], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1, "v": "a"}, {"_id": 1, "v": "b"}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0)
    inserts = _insert_calls(s)
    assert len(inserts) == 1
    assert inserts[0][1]["documents"] == [{"_id": 1, "v": "a"}]


# ── belt: 200-with-write_errors still handled ────────────────────────────────

def test_duplicates_by_code_are_informational(capsys):
    errs = [{"code": 11000, "errmsg": "duplicate key"}] * 2
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=1, duplicates=2, failed=0)
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out
    assert "[WARN]" not in out


def test_duplicates_by_errmsg_string():
    errs = [{"errmsg": "E11000 duplicate key error"}]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r.duplicates == 1 and r.failed == 0


def test_real_failures_keep_warn(capsys):
    errs = [{"code": 11000, "errmsg": "dup"}, {"code": 2, "errmsg": "too large"}]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=1)
    out = capsys.readouterr().out
    assert "[WARN] 1 document(s) failed" in out
    assert "too large" in out


def test_bare_string_write_error_duplicate():
    errs = ["E11000 duplicate key error"]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0)


def test_bare_string_write_error_non_duplicate():
    errs = ["boom"]
    s = StubSession([_resp([1], errs)], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=1)


# ── opaque batch 400 after dedupe → per-record poison-doc isolation ─────────

def test_batch_400_isolates_poison_doc(capsys):
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    poison = StubResponse({"code": "error", "message": "bad value for doc 2"}, status=400)
    s = StubSession(
        [batch_400, _resp([1]), poison, _resp([3])],
        existing=set(),
    )
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}, {"_id": 3}])
    assert r == cbi.BatchResult(inserted=2, duplicates=0, failed=1)
    out = capsys.readouterr().out
    assert "poison document skipped (_id=2)" in out
    assert "[WARN] 1 document(s) failed in this batch (isolated per-record)" in out


def test_batch_400_all_singles_succeed():
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    s = StubSession([batch_400, _resp([1]), _resp([2]), _resp([3])], existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}, {"_id": 3}])
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0)


def test_batch_400_fallback_401_raises():
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    unauthorized = StubResponse({"code": "error", "message": "unauthorized"}, status=401)
    s = StubSession([batch_400, unauthorized], existing=set())
    try:
        cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass


def test_batch_400_with_duplicates_still_reports_them(capsys):
    batch_400 = StubResponse({"code": "error", "message": "batch op errors occurred"},
                             status=400)
    s = StubSession([batch_400, _resp([2])], existing={1})
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=0)
    out = capsys.readouterr().out
    assert "1 duplicate(s) skipped" in out


# ── retry wraps the whole check+insert flow ──────────────────────────────────

def test_transient_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession([requests.exceptions.ConnectionError("boom"), _resp([1])],
                    existing=set())
    r = cbi.insert_batch(s, "c", [{"_id": 1}])
    assert r.inserted == 1
    assert len(_insert_calls(s)) == 2


def test_retry_recovers_partially_applied_insert(monkeypatch):
    """A transient error strikes mid insert_many, but the server had already
    persisted the first few documents. The re-run existence check on retry
    sees them as 'existing' — they must be counted as inserted (recovered),
    not misclassified as pre-existing duplicates."""
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    find_none = StubResponse({"code": "ok", "result": []})
    find_recovered = StubResponse({"code": "ok",
                                   "result": [{"_id": i} for i in (1, 2, 3)]})
    s = StubSession(
        insert_queue=[requests.exceptions.ConnectionError("boom"), _resp([4, 5])],
        find_queue=[find_none, find_recovered],
    )
    records = [{"_id": i} for i in range(1, 6)]
    r = cbi.insert_batch(s, "c", records)
    assert r == cbi.BatchResult(inserted=5, duplicates=0, failed=0)
    inserts = _insert_calls(s)
    assert len(inserts) == 2  # attempt 1 (raised) + attempt 2 (succeeded)
    assert inserts[-1][1]["documents"] == [{"_id": 4}, {"_id": 5}]


def test_transient_error_on_find_retries_whole_flow(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    find_ok = StubResponse({"code": "ok", "result": []})
    s = StubSession([_resp([1])],
                    find_queue=[requests.exceptions.ConnectionError("boom"), find_ok])
    r = cbi.insert_batch(s, "c", [{"_id": 1}])
    assert r == cbi.BatchResult(inserted=1, duplicates=0, failed=0)
    assert len(_find_calls(s)) == 2
    assert len(_insert_calls(s)) == 1
