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
    """Write a minimal valid config JSON; keyword args override defaults.

    extra_params: merged into the default "users" dataset block (ignored
    when an explicit `datasets` override is also given) — a convenience for
    tests that only need to add extra_params to the single default dataset.
    min_partition: top-level config key (sits beside ds_batch_size); omitted
    from the JSON entirely unless explicitly passed, so tests can exercise
    load_config's own "key absent -> defaults to 50_000" fallback.
    """
    default_users = {"endpoint": "api/users", "collection": "users",
                     "id_key": "id", "scope": "s", "fields": ["id"]}
    if kw.get("extra_params") is not None:
        default_users = {**default_users, "extra_params": kw["extra_params"]}
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
        "datasets": kw.get("datasets", {"users": default_users}),
    }
    if kw.get("min_partition") is not None:
        cfg["min_partition"] = kw["min_partition"]
    path = tmp_path / "coupa_bulk_import.config.json"
    path.write_text(json.dumps(cfg))
    return path


def make_records(*ids):
    """Coupa-shaped records with sequential updated_at stamps."""
    return [{"id": i, "updated_at": f"2026-07-01T00:00:{i % 60:02d}Z"} for i in ids]


class FakeCoupa:
    """Stateful Coupa stub over a fixed id set — serves keyset pages and
    rank probes by actually interpreting the id AND anchor filters, so tests
    assert outcomes (which records landed) instead of request shapes.

    updated: optional {id: updated_at} overrides (default stamp is ancient,
    so records are in-anchor for any realistic anchor) — ids stamped after
    a run's anchor are excluded, like the real `updated-at[lt_or_eq]`
    filter."""

    def __init__(self, ids, page_size=50, updated=None):
        self.ids = sorted(ids)
        self.page_size = page_size
        self.updated = dict(updated or {})
        self.page_calls = 0
        self.rank_calls = 0

    def _stamp(self, i):
        return self.updated.get(i, "2020-01-01T00:00:00Z")

    def _rec(self, i):
        return {"id": i, "updated_at": self._stamp(i)}

    def _anchored(self, anchor_ts):
        return [i for i in self.ids if self._stamp(i) <= anchor_ts]

    def fetch_page(self, session, endpoint, fields, anchor_ts, *,
                   before_id=None, id_gt=None, limit=None, extra_params=None):
        self.page_calls += 1
        sel = [i for i in self._anchored(anchor_ts)
               if (before_id is None or i < before_id)
               and (id_gt is None or i > id_gt)]
        sel = sorted(sel, reverse=True)[: (limit or self.page_size)]
        return [self._rec(i) for i in sel]

    def fetch_at_rank(self, session, endpoint, anchor_ts, rank, *,
                      extra_params=None):
        self.rank_calls += 1
        asc = self._anchored(anchor_ts)
        return [{"id": asc[rank]}] if rank < len(asc) else []

    def install(self, monkeypatch):
        import coupa_bulk_import as cbi
        monkeypatch.setattr(cbi, "fetch_page", self.fetch_page)
        monkeypatch.setattr(cbi, "fetch_at_rank", self.fetch_at_rank)


def run_import(monkeypatch, tmp_path, pages=None, batch_results=None, resume=False,
               state=None, username=None, password=None, insert_stub=None,
               db_count=0, datasets=None, ds_batch_size=None, ds_session=None,
               no_unique_index_ok=False, fake_coupa=None, id_range=None):
    """Drive import_dataset with canned keyset pages (or a FakeCoupa) and
    stubbed DS inserts.

    pages: list of record-lists; MUST end with [] (Coupa's empty page).
    fake_coupa: a FakeCoupa instance — overrides `pages` and serves real
    keyset semantics (cursor/range filters honored).
    batch_results: optional queue of BatchResult returned per flush.
    insert_stub: full replacement for insert_batch (overrides batch_results).
    ds_session: a stateful DS stub (e.g. FakeDS) — when given, the REAL
    insert_batch/_collection_count run against it end-to-end.
    Returns (saved_state_dict, calls) where calls records cursor values,
    extra_params, per fetch and inserted batches.
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

    calls = {"cursors": [], "inserted_batches": [], "id_keys": [], "extra_params": []}

    if fake_coupa is not None:
        fake_coupa.install(monkeypatch)
    else:
        pages_iter = iter(pages)

        def fake_fetch(session, endpoint, fields, anchor_ts, *,
                       before_id=None, id_gt=None, limit=None, extra_params=None):
            calls["cursors"].append(before_id)
            calls["extra_params"].append(extra_params)
            return next(pages_iter)

        monkeypatch.setattr(cbi, "fetch_page", fake_fetch)

    def fake_insert(session, collection, records, id_key="id", _retries=5):
        calls["inserted_batches"].append(list(records))
        calls["id_keys"].append(id_key)
        if batch_results:
            return batch_results.pop(0)
        return cbi.BatchResult(len(records), 0, 0)

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
                       no_unique_index_ok=no_unique_index_ok, id_range=id_range)
    saved = json.loads(state_path.read_text()) if state_path.exists() else {}
    return saved, calls
