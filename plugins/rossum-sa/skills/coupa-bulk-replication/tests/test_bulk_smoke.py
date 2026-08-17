"""--smoke: self-cleaning smoke test — data safety and exit-code contracts.

Flag-combination refusals live in test_bulk_cli_guards.py.
"""
import sys

import pytest

from bulk_helpers import FakeDS, StubResponse, make_records, write_config

import coupa_bulk_import as cbi


def _delete_calls(ds):
    return [c for c in ds.calls if c[0].endswith("/data/delete_many")]


def _setup(monkeypatch, tmp_path, pages, **cfg_kw):
    """Load a config in tmp_path and stub the Coupa side with canned pages."""
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path, **cfg_kw))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "coupa-token")
    pages_iter = iter(pages)

    def fake_fetch(session, endpoint, fields, anchor_ts, *,
                   before_id=None, id_gt=None, limit=None, extra_params=None):
        return next(pages_iter)

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch)


# ── happy path: insert → verify → delete → clean ─────────────────────────────

def test_smoke_inserts_then_deletes_its_own_records(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1, 2, 3)])
    ds = FakeDS()
    assert cbi.smoke_dataset("users", 3, ds) is True
    assert ds.ids == set()               # self-cleaned
    deletes = _delete_calls(ds)
    assert len(deletes) == 1
    payload = deletes[0][1]
    # DS API contract: delete endpoints take "filter", NOT "query"
    # (live-verified asymmetry — the wrong key 422s)
    assert "query" not in payload
    assert payload["filter"] == {"id": {"$in": [1, 2, 3]}}


def test_smoke_never_touches_state_files(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1)])
    cbi.smoke_dataset("users", 1, FakeDS())
    assert list(tmp_path.glob("coupa_import_state*")) == []


def test_smoke_leaves_preexisting_records_alone(monkeypatch, tmp_path):
    # Record 1 already lives in the collection (e.g. real loaded data):
    # the smoke's delete filter must only carry the ids it actually added.
    _setup(monkeypatch, tmp_path, [make_records(1, 2)])
    ds = FakeDS(preloaded={1})
    cbi.smoke_dataset("users", 2, ds)
    assert ds.ids == {1}
    assert _delete_calls(ds)[0][1]["filter"] == {"id": {"$in": [2]}}


def test_smoke_falsy_ids_excluded_from_delete_filter(monkeypatch, tmp_path):
    page = [{"id": 1, "updated_at": "t"}, {"id": 0, "updated_at": "t"},
            {"id": "", "updated_at": "t"}]
    _setup(monkeypatch, tmp_path, [page])
    ds = FakeDS()
    cbi.smoke_dataset("users", 3, ds)
    assert _delete_calls(ds)[0][1]["filter"] == {"id": {"$in": [1]}}
    assert ds.anon == 2                  # falsy-id residue stays behind


def test_smoke_no_records_is_a_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [[]])
    ds = FakeDS()
    assert cbi.smoke_dataset("users", 1, ds) is True   # empty ≠ failure
    assert _delete_calls(ds) == []


def test_smoke_never_deletes_concurrent_writers_record(monkeypatch, tmp_path):
    """A concurrent writer (re-enabled hook, parallel run) lands record 1
    between the smoke's snapshot and its insert. The smoke's delete filter
    must carry only what ITS insert landed — the writer's record survives."""
    _setup(monkeypatch, tmp_path, [make_records(1, 2)])

    class RacingDS(FakeDS):
        def __init__(self):
            super().__init__()
            self.raced = False

        def post(self, url, json=None, timeout=None):
            resp = super().post(url, json=json, timeout=timeout)
            if url.endswith("/data/aggregate") and not self.raced:
                self.raced = True                 # writer lands 1 right
                self.docs.append({"id": 1})       # after our snapshot
            return resp

    ds = RacingDS()
    assert cbi.smoke_dataset("users", 2, ds) is True
    assert ds.ids == {1}                          # writer's record survived
    assert _delete_calls(ds)[0][1]["filter"] == {"id": {"$in": [2]}}


# ── exit-code contract: any failure → False ──────────────────────────────────

def test_smoke_returns_false_on_failed_inserts(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1)])
    monkeypatch.setattr(cbi, "insert_batch",
                        lambda *a, **kw: cbi.BatchResult(0, 0, 1, []))
    assert cbi.smoke_dataset("users", 1, FakeDS()) is False


def test_smoke_returns_false_on_verification_shortfall(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1)])
    # insert_batch claims it landed id 9, but the store never got it
    monkeypatch.setattr(cbi, "insert_batch",
                        lambda *a, **kw: cbi.BatchResult(1, 0, 0, [9]))
    assert cbi.smoke_dataset("users", 1, FakeDS()) is False


def test_smoke_returns_false_on_cleanup_shortfall(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [make_records(1)])

    class NoDeleteDS(FakeDS):
        def post(self, url, json=None, timeout=None):
            if url.endswith("/data/delete_many"):
                self.calls.append((url, json))
                return StubResponse({"result": {"deleted_count": 0}})
            return super().post(url, json=json, timeout=timeout)

    assert cbi.smoke_dataset("users", 1, NoDeleteDS()) is False


# ── auth: shared 401 heal, no residue ────────────────────────────────────────

def test_smoke_heals_ds_401_and_leaves_no_residue(monkeypatch, tmp_path):
    """A token expiring mid-smoke (here: on the final delete) must heal via
    the config token re-read instead of leaving residue behind."""

    class Expire401OnceDS(FakeDS):
        def __init__(self):
            super().__init__()
            self.expired_once = False

        def post(self, url, json=None, timeout=None):
            if url.endswith("/data/delete_many") and not self.expired_once:
                self.expired_once = True
                self.calls.append((url, json))
                return StubResponse({"message": "unauthorized"}, status=401)
            return super().post(url, json=json, timeout=timeout)

    _setup(monkeypatch, tmp_path, [make_records(1, 2)])
    ds = Expire401OnceDS()
    assert cbi.smoke_dataset("users", 2, ds) is True
    assert ds.expired_once
    assert ds.headers["Authorization"] == "Bearer tok"  # re-read from config
    assert ds.ids == set()                              # no residue


# ── CLI wiring ───────────────────────────────────────────────────────────────

def _smoke_stub(called, ok=True):
    def stub(key, n, session, username=None, password=None):
        called.append((key, n))
        return ok
    return stub


def test_main_smoke_defaults_to_one_and_writes_no_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    called = []
    monkeypatch.setattr(cbi, "smoke_dataset", _smoke_stub(called))
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke",
                         "--dataset", "users", "--config", str(path)])
    cbi.main()
    assert called == [("users", 1)]
    assert list(tmp_path.glob("coupa_import_state*")) == []


def test_main_smoke_exits_1_when_any_dataset_fails(monkeypatch, tmp_path):
    """All datasets still run (no short-circuit) and the exit code is 1 —
    `--smoke && full-run` is a real gate."""
    two = {
        "users": {"endpoint": "api/users", "collection": "users",
                  "id_key": "id", "scope": "s", "fields": ["id"]},
        "suppliers": {"endpoint": "api/suppliers", "collection": "suppliers",
                      "id_key": "id", "scope": "s", "fields": ["id"]},
    }
    path = write_config(tmp_path, datasets=two)
    called = []

    def stub(key, n, session, username=None, password=None):
        called.append(key)
        return key != "users"          # first dataset fails

    monkeypatch.setattr(cbi, "smoke_dataset", stub)
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke",
                         "--dataset", "users,suppliers", "--config", str(path)])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert called == ["users", "suppliers"]   # both ran despite the failure
    assert exc.value.code != 0


def test_main_smoke_mints_token_from_credentials(monkeypatch, tmp_path):
    """A credentials-only config (empty rossum.token) must mint a token up
    front instead of sending 'Bearer ' and dying on the first DS call."""
    path = write_config(tmp_path, token="")
    sessions = []
    monkeypatch.setattr(cbi, "refresh_rossum_token",
                        lambda u, p: "minted-token")
    monkeypatch.setattr(cbi, "smoke_dataset",
                        lambda key, n, session, username=None, password=None:
                        sessions.append(session.headers["Authorization"]) or True)
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--smoke", "--dataset", "users",
                         "--config", str(path),
                         "--username", "u@x.com", "--password", "pw"])
    cbi.main()
    assert sessions == ["Bearer minted-token"]
