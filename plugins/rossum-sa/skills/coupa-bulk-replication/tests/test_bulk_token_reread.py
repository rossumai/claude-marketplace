import json

import pytest
import requests

from bulk_helpers import make_records, run_import, write_config

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

    def insert_stub(session, collection, records, id_key="id",
                    check_existing=True, _retries=5):
        attempts.append(session.headers.get("Authorization"))
        if len(attempts) == 1:
            raise _http_401()
        return cbi.BatchResult(len(records), 0, 0)

    saved, _ = run_import(monkeypatch, tmp_path, [make_records(1), []],
                          insert_stub=insert_stub)
    assert attempts[1] == "Bearer tok"           # re-read from the config file
    assert saved["users"]["total_inserted"] == 1


def test_401_with_stale_config_token_raises(monkeypatch, tmp_path):
    def insert_stub(session, collection, records, id_key="id",
                    check_existing=True, _retries=5):
        raise _http_401()

    with pytest.raises(requests.HTTPError):
        run_import(monkeypatch, tmp_path, [make_records(1), []],
                   insert_stub=insert_stub)
