"""Shared helpers for the coupa-bulk-replication test suite."""
import json


def write_config(tmp_path, **kw):
    """Write a minimal valid config JSON; keyword args override defaults."""
    cfg = {
        "coupa": {
            "base_url": kw.get("base_url", "https://x.coupahost.com"),
            "client_id": kw.get("client_id", "cid"),
            "client_secret": kw.get("client_secret", "sec"),
        },
        "rossum": {
            "api_url": "https://o.rossum.app/api/v1",
            "ds_url": "https://o.rossum.app/svc/data-storage/api/v1",
            "token": kw.get("token", "tok"),
        },
        "ds_batch_size": kw.get("ds_batch_size", 5000),
        "datasets": kw.get("datasets", {
            "users": {"endpoint": "api/users", "collection": "users",
                      "id_key": "id", "scope": "s", "fields": ["id"]},
        }),
    }
    path = tmp_path / "coupa_bulk_import.config.json"
    path.write_text(json.dumps(cfg))
    return path


def make_records(*ids):
    """Coupa-shaped records with sequential updated_at stamps."""
    return [{"id": i, "updated_at": f"2026-07-01T00:00:{i % 60:02d}Z"} for i in ids]


def run_import(monkeypatch, tmp_path, pages, batch_results=None, resume=False,
               state=None, username=None, password=None, insert_stub=None,
               db_count=0):
    """Drive import_dataset with canned Coupa pages and stubbed DS inserts.

    pages: list of record-lists; MUST end with [] (Coupa's empty page).
    batch_results: optional queue of BatchResult returned per flush.
    insert_stub: full replacement for insert_batch (overrides batch_results).
    Returns (saved_state_dict, calls) where calls records fetch offsets and
    inserted batches.
    """
    import coupa_bulk_import as cbi

    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "coupa-token")

    calls = {"fetch_offsets": [], "inserted_batches": []}
    pages_iter = iter(pages)

    def fake_fetch(session, endpoint, fields, offset, anchor_ts):
        calls["fetch_offsets"].append(offset)
        return next(pages_iter)

    def fake_insert(session, collection, records, _retries=5):
        calls["inserted_batches"].append(list(records))
        if batch_results:
            return batch_results.pop(0)
        return cbi.BatchResult(len(records), 0, 0)

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch)
    monkeypatch.setattr(cbi, "insert_batch", insert_stub or fake_insert)
    monkeypatch.setattr(cbi, "_collection_count", lambda session, collection: db_count)

    ds_session = type("S", (), {"headers": {}})()
    state = dict(state or {})
    state_path = tmp_path / "state.json"
    cbi.import_dataset("users", None, resume, state, ds_session,
                       state_path=state_path, username=username, password=password)
    return json.loads(state_path.read_text()), calls
