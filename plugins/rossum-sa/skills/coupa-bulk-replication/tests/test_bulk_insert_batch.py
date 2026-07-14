import requests

import coupa_bulk_import as cbi


class StubResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class StubSession:
    """Yields queued responses; raises queued exceptions."""
    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(inserted_ids, write_errors=()):
    return StubResponse({"result": {"inserted_ids": list(inserted_ids),
                                    "write_errors": list(write_errors)}})


def test_all_inserted():
    s = StubSession([_resp([1, 2, 3])])
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=3, duplicates=0, failed=0)


def test_duplicates_by_code_are_informational(capsys):
    errs = [{"code": 11000, "errmsg": "duplicate key"}] * 2
    s = StubSession([_resp([1], errs)])
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=1, duplicates=2, failed=0)
    out = capsys.readouterr().out
    assert "duplicate(s) skipped" in out
    assert "[WARN]" not in out


def test_duplicates_by_errmsg_string():
    errs = [{"errmsg": "E11000 duplicate key error"}]
    s = StubSession([_resp([1], errs)])
    r = cbi.insert_batch(s, "c", [{"_id": 1}, {"_id": 2}])
    assert r.duplicates == 1 and r.failed == 0


def test_real_failures_keep_warn(capsys):
    errs = [{"code": 11000, "errmsg": "dup"}, {"code": 2, "errmsg": "too large"}]
    s = StubSession([_resp([1], errs)])
    r = cbi.insert_batch(s, "c", [{"_id": i} for i in (1, 2, 3)])
    assert r == cbi.BatchResult(inserted=1, duplicates=1, failed=1)
    out = capsys.readouterr().out
    assert "[WARN] 1 document(s) failed" in out
    assert "too large" in out


def test_transient_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)
    s = StubSession([requests.exceptions.ConnectionError("boom"), _resp([1])])
    r = cbi.insert_batch(s, "c", [{"_id": 1}])
    assert r.inserted == 1
    assert len(s.calls) == 2
