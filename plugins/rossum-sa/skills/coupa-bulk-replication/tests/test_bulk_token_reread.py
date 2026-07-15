import json

import pytest
import requests

from bulk_helpers import FakeDS, StubResponse, make_records, run_import, write_config

import coupa_bulk_import as cbi


def _http_401():
    resp = type("R", (), {"status_code": 401})()
    return requests.HTTPError(response=resp)


def test_reload_config_token_reads_current_file(tmp_path):
    path = write_config(tmp_path, token="fresh-token ")
    cbi.load_config(path)
    cfg = json.loads(path.read_text())
    cfg["rossum"]["token"] = " swapped-token\n"
    path.write_text(json.dumps(cfg))
    assert cbi.reload_config_token() == "swapped-token"


def test_401_without_creds_rereads_config_and_retries(monkeypatch, tmp_path):
    attempts = []

    def insert_stub(session, collection, records, id_key="id", _retries=5):
        attempts.append(session.headers.get("Authorization"))
        if len(attempts) == 1:
            raise _http_401()
        return cbi.BatchResult(len(records), 0, 0)

    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          insert_stub=insert_stub)
    assert attempts[1] == "Bearer tok"           # re-read from the config file
    assert saved["users"]["total_inserted"] == 1


def test_401_with_stale_config_token_raises(monkeypatch, tmp_path):
    def insert_stub(session, collection, records, id_key="id", _retries=5):
        raise _http_401()

    with pytest.raises(requests.HTTPError):
        run_import(monkeypatch, tmp_path, [make_records(1), []],
                   insert_stub=insert_stub)


def test_401_heal_retry_goes_through_checked_path(monkeypatch, tmp_path):
    """Records persisted before a mid-batch 401 (e.g. during the poison-doc
    fallback) must dedupe on the healed retry — never double-insert. Pins
    the invariant that the heal retry re-runs the existence check; a
    refactor reintroducing an unchecked heal path must fail here."""

    class PersistThen401(FakeDS):
        """First insert_many: the server applies the write but the client
        sees a 401 (worst case — response lost after persistence)."""

        def __init__(self):
            super().__init__()
            self.failed_once = False

        def post(self, url, json=None, timeout=None):
            if url.endswith("/data/insert_many") and not self.failed_once:
                self.failed_once = True
                super().post(url, json=json, timeout=timeout)
                return StubResponse({"message": "unauthorized"}, status=401)
            return super().post(url, json=json, timeout=timeout)

    ds = PersistThen401()
    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1, 2), []],
                          ds_session=ds)
    assert ds.failed_once
    assert ds.value_counts() == {1: 1, 2: 1}     # deduped on the healed retry
    assert saved["users"]["completed"] is True
