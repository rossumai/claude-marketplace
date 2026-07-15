"""Shared helpers for the coupa-bulk-replication test suite."""
import json
from collections import Counter

import requests


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


class FakeDS:
    """Stateful DS stub simulating one collection keyed by the id field.

    Stores every inserted document verbatim (i.e. NO unique index — the
    worst case), so tests can assert that the script's own dedup prevented
    double-inserts. Handles the aggregate existence pipeline, $count,
    insert_many, delete_many, and indexes/list.
    """

    def __init__(self, id_key="id", preloaded=()):
        self.id_key = id_key
        self.docs = [{id_key: v} for v in preloaded]
        self.calls = []             # (url, payload) of every POST
        self.headers = {}
        # Default: the recommended unique partial index exists.
        self.indexes = [
            {"name": "_id_", "key": {"_id": 1}},
            {"name": f"__{id_key}_unique_idx", "key": {id_key: 1},
             "unique": True,
             "partialFilterExpression": {id_key: {"$exists": True}}},
        ]

    # ── inspection helpers ────────────────────────────────────────────────
    @property
    def ids(self):
        """Distinct truthy id values present."""
        return {d.get(self.id_key) for d in self.docs if d.get(self.id_key)}

    @property
    def anon(self):
        """Documents without a usable (truthy) id value."""
        return sum(1 for d in self.docs if not d.get(self.id_key))

    def value_counts(self):
        """Counter of truthy id values — >1 means a real duplicate landed."""
        return Counter(d.get(self.id_key) for d in self.docs
                       if d.get(self.id_key))

    # ── request routing ───────────────────────────────────────────────────
    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if url.endswith("/data/aggregate"):
            pipeline = json["pipeline"]
            if "$match" in pipeline[0]:      # existence check: $match + $group
                key = next(iter(pipeline[0]["$match"]))
                wanted = set(pipeline[0]["$match"][key]["$in"])
                present = {d.get(key) for d in self.docs} & wanted
                return StubResponse({"result": [{"_id": v} for v in present]})
            total = len(self.docs)           # [{"$count": "total"}]
            return StubResponse({"result": [{"total": total}] if total else []})
        if url.endswith("/data/insert_many"):
            docs = json["documents"]
            self.docs.extend(dict(d) for d in docs)
            return StubResponse({"result": {"inserted_ids": ["x"] * len(docs)}})
        if url.endswith("/data/delete_many"):
            flt = json["filter"]
            key = next(iter(flt))
            vals = set(flt[key]["$in"])
            before = len(self.docs)
            self.docs = [d for d in self.docs if d.get(key) not in vals]
            return StubResponse({"result": {"deleted_count": before - len(self.docs)}})
        if url.endswith("/indexes/list"):
            return StubResponse({"result": list(self.indexes)})
        raise AssertionError(f"unexpected POST {url}")


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
               db_count=0, datasets=None, ds_batch_size=None, ds_session=None,
               no_unique_index_ok=False):
    """Drive import_dataset with canned Coupa pages and stubbed DS inserts.

    pages: list of record-lists; MUST end with [] (Coupa's empty page).
    batch_results: optional queue of BatchResult returned per flush.
    insert_stub: full replacement for insert_batch (overrides batch_results).
    ds_session: a stateful DS stub (e.g. FakeDS) — when given, the REAL
    insert_batch/_collection_count run against it end-to-end.
    Returns (saved_state_dict, calls) where calls records fetch offsets and
    inserted batches.
    """
    import coupa_bulk_import as cbi

    monkeypatch.chdir(tmp_path)
    kw = {}
    if datasets is not None:
        kw["datasets"] = datasets
    if ds_batch_size is not None:
        kw["ds_batch_size"] = ds_batch_size
    cbi.load_config(write_config(tmp_path, **kw))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "coupa-token")

    calls = {"fetch_offsets": [], "inserted_batches": [], "id_keys": []}
    pages_iter = iter(pages)

    def fake_fetch(session, endpoint, fields, offset, anchor_ts):
        calls["fetch_offsets"].append(offset)
        return next(pages_iter)

    def fake_insert(session, collection, records, id_key="id", _retries=5):
        calls["inserted_batches"].append(list(records))
        calls["id_keys"].append(id_key)
        if batch_results:
            return batch_results.pop(0)
        return cbi.BatchResult(len(records), 0, 0)

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch)
    if ds_session is None:
        monkeypatch.setattr(cbi, "insert_batch", insert_stub or fake_insert)
        monkeypatch.setattr(cbi, "_collection_count", lambda session, collection: db_count)
        monkeypatch.setattr(cbi, "verify_unique_index",
                            lambda session, collection, id_key: "ok")
        ds_session = type("S", (), {"headers": {}})()

    state = dict(state or {})
    state_path = tmp_path / "state.json"
    cbi.import_dataset("users", None, resume, state, ds_session,
                       state_path=state_path, username=username, password=password,
                       no_unique_index_ok=no_unique_index_ok)
    return json.loads(state_path.read_text()), calls
