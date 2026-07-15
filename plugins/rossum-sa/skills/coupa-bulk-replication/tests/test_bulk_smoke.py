import sys

import pytest
import requests

from bulk_helpers import make_records, write_config

import coupa_bulk_import as cbi


class StubResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeDS:
    """Stateful DS stub simulating one collection keyed by the id field."""

    def __init__(self, id_key="id", preloaded=()):
        self.id_key = id_key
        self.ids = set(preloaded)   # truthy id values present in the collection
        self.anon = 0               # documents without a usable id value
        self.calls = []             # (url, payload) of every POST
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if url.endswith("/data/find"):
            key = next(iter(json["query"]))
            wanted = json["query"][key]["$in"]
            return StubResponse({"result": [{key: v} for v in wanted
                                            if v in self.ids]})
        if url.endswith("/data/insert_many"):
            docs = json["documents"]
            for d in docs:
                v = d.get(self.id_key)
                if v:
                    self.ids.add(v)
                else:
                    self.anon += 1
            return StubResponse({"result": {"inserted_ids": ["x"] * len(docs)}})
        if url.endswith("/data/delete_many"):
            flt = json["filter"]
            key = next(iter(flt))
            vals = set(flt[key]["$in"])
            deleted = len(self.ids & vals)
            self.ids -= vals
            return StubResponse({"result": {"deleted_count": deleted}})
        if url.endswith("/data/aggregate"):
            total = len(self.ids) + self.anon
            return StubResponse({"result": [{"total": total}] if total else []})
        raise AssertionError(f"unexpected POST {url}")


def _delete_calls(ds):
    return [c for c in ds.calls if c[0].endswith("/data/delete_many")]


def _setup(monkeypatch, tmp_path, pages, **cfg_kw):
    """Load a config in tmp_path and stub the Coupa side with canned pages."""
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path, **cfg_kw))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "coupa-token")
    pages_iter = iter(pages)
    fetches = []

    def fake_fetch(session, endpoint, fields, offset, anchor_ts, limit=None):
        fetches.append((offset, limit))
        return next(pages_iter)

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch)
    return fetches


# ── happy path: insert → verify → delete → clean ─────────────────────────────

def test_smoke_inserts_then_deletes_its_own_records(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, [make_records(1, 2, 3)])
    ds = FakeDS()
    cbi.smoke_dataset("users", 3, ds)
    assert ds.ids == set()               # self-cleaned
    deletes = _delete_calls(ds)
    assert len(deletes) == 1
    payload = deletes[0][1]
    # DS delete endpoints take "filter", NOT "query" (find/aggregate quirk)
    assert "query" not in payload
    assert payload["filter"] == {"id": {"$in": [1, 2, 3]}}
    out = capsys.readouterr().out
    assert "deleted 3" in out
    assert "now holds 0" in out


def test_smoke_never_touches_state_files(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1)])
    cbi.smoke_dataset("users", 1, FakeDS())
    assert list(tmp_path.glob("coupa_import_state*")) == []


def test_smoke_insert_uses_check_existing_true(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1, 2)])
    ds = FakeDS()
    seen = {}

    def fake_insert(session, collection, records, id_key="id",
                    check_existing=None, _retries=5):
        seen["check_existing"] = check_existing
        return cbi.BatchResult(len(records), 0, 0)

    monkeypatch.setattr(cbi, "insert_batch", fake_insert)
    cbi.smoke_dataset("users", 2, ds)
    assert seen["check_existing"] is True


def test_smoke_leaves_preexisting_records_alone(monkeypatch, tmp_path):
    # Record 1 already lives in the collection (e.g. real loaded data):
    # the smoke's delete filter must only carry the ids it actually added.
    _setup(monkeypatch, tmp_path, [make_records(1, 2)])
    ds = FakeDS(preloaded={1})
    cbi.smoke_dataset("users", 2, ds)
    assert ds.ids == {1}
    assert _delete_calls(ds)[0][1]["filter"] == {"id": {"$in": [2]}}


def test_smoke_falsy_ids_excluded_from_delete_filter(monkeypatch, tmp_path, capsys):
    page = [{"id": 1, "updated_at": "t"}, {"id": 0, "updated_at": "t"},
            {"id": "", "updated_at": "t"}]
    _setup(monkeypatch, tmp_path, [page])
    ds = FakeDS()
    cbi.smoke_dataset("users", 3, ds)
    assert _delete_calls(ds)[0][1]["filter"] == {"id": {"$in": [1]}}
    out = capsys.readouterr().out
    assert "[WARN]" in out and "excluded from the smoke delete" in out
    assert "now holds 2" in out          # the falsy-id residue stays behind


def test_smoke_paginates_until_n_records(monkeypatch, tmp_path):
    fetches = _setup(monkeypatch, tmp_path,
                     [make_records(1, 2), make_records(3, 4), []])
    ds = FakeDS()
    cbi.smoke_dataset("users", 3, ds)
    assert fetches == [(0, 3), (2, 1)]   # asks Coupa only for what is missing
    assert ds.ids == set()


def test_smoke_no_records_is_a_noop(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, [[]])
    ds = FakeDS()
    cbi.smoke_dataset("users", 1, ds)
    assert _delete_calls(ds) == []
    assert "nothing to smoke-test" in capsys.readouterr().out


# ── CLI wiring and refusals ──────────────────────────────────────────────────

def test_main_smoke_defaults_to_one_and_writes_no_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    called = []
    monkeypatch.setattr(cbi, "smoke_dataset",
                        lambda key, n, session: called.append((key, n)))
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke",
                         "--dataset", "users", "--config", str(path)])
    cbi.main()
    assert called == [("users", 1)]
    assert list(tmp_path.glob("coupa_import_state*")) == []


def test_main_smoke_explicit_n(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    called = []
    monkeypatch.setattr(cbi, "smoke_dataset",
                        lambda key, n, session: called.append((key, n)))
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke", "3",
                         "--dataset", "users", "--config", str(path)])
    cbi.main()
    assert called == [("users", 3)]


def test_main_rejects_smoke_with_supervise(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke", "--supervise"])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--smoke" in str(exc.value)
    assert "--supervise" in str(exc.value)


def test_main_rejects_smoke_with_resume(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke", "--resume"])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--smoke" in str(exc.value)
    assert "--resume" in str(exc.value)


def test_main_rejects_smoke_larger_than_batch_size(monkeypatch, tmp_path):
    path = write_config(tmp_path, ds_batch_size=2)
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke", "5",
                         "--config", str(path)])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--smoke" in str(exc.value)
    assert "ds_batch_size" in str(exc.value)
