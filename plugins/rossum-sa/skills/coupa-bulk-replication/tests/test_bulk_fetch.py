"""Keyset fetch primitives. The query shapes ARE the Coupa API contract this
skill runs on (order_by=id + id[lt]/id[gt] + updated-at anchor — see spec
§4.1/§9, live-validated before publish), so they are pinned ONCE here, per
the testing-bar exception for live-verified API contracts."""
import json

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import StubResponse, write_config


class ParamCapture:
    def __init__(self):
        self.params = None

    def get(self, url, params=None, verify=None, timeout=None):
        self.params = params
        return StubResponse([])


def _cfg(tmp_path):
    cbi.load_config(write_config(tmp_path))


def test_fetch_page_keyset_query_shape(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    cbi.fetch_page(session, "api/users", ["id", "name"], "2026-07-15T00:00:00Z",
                   before_id=500, id_gt=100, limit=50)
    p = session.params
    assert p["order_by"] == "id"
    assert p["dir"] == "desc"
    assert p["offset"] == 0
    assert p["id[lt]"] == 500
    assert p["id[gt]"] == 100
    assert p["updated-at[lt_or_eq]"] == "2026-07-15T00:00:00Z"
    assert p["limit"] == 50
    assert json.loads(p["fields"]) == ["id", "name"]


def test_fetch_page_first_page_has_no_cursor_filters(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    cbi.fetch_page(session, "api/users", ["id"], "2026-07-15T00:00:00Z")
    p = session.params
    assert "id[lt]" not in p and "id[gt]" not in p and "limit" not in p


def test_fetch_at_rank_query_shape(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    cbi.fetch_at_rank(session, "api/users", "2026-07-15T00:00:00Z", 12345)
    p = session.params
    assert p["order_by"] == "id"
    assert p["dir"] == "asc"
    assert p["offset"] == 12345
    assert p["limit"] == 1
    assert p["updated-at[lt_or_eq]"] == "2026-07-15T00:00:00Z"
    assert json.loads(p["fields"]) == ["id"]


def test_fetch_page_goes_through_coupa_call(monkeypatch, tmp_path):
    # every Coupa request must pass the throttle/backoff wrapper
    _cfg(tmp_path)
    seen = []
    real = cbi.coupa_call
    monkeypatch.setattr(cbi, "coupa_call",
                        lambda fn, **kw: (seen.append(1), real(fn, **kw))[1])
    cbi.fetch_page(ParamCapture(), "api/users", ["id"], "t")
    cbi.fetch_at_rank(ParamCapture(), "api/users", "t", 0)
    assert seen == [1, 1]


def test_ensure_id_field():
    assert cbi.ensure_id_field(["name"]) == ["id", "name"]
    assert cbi.ensure_id_field(["id", "name"]) == ["id", "name"]
    assert cbi.ensure_id_field([]) == ["id"]


# ── extra_params: validator (pure) ───────────────────────────────────────────
#
# extra_params lets a dataset config be a FILTERED SLICE of its Coupa
# endpoint (e.g. a lookup slice needs lookup[name][in] + active; an invoice
# load needs a created-at[gt_or_eq] floor). The reserved keys are the ones
# the script itself manages for cursor/anchor/projection — letting a dataset
# override them would corrupt keyset pagination or re-slice partitions
# mid-run, so validate_extra_params rejects them loudly at config-load time.

@pytest.mark.parametrize("key", sorted(cbi.RESERVED_PARAM_KEYS))
def test_validate_extra_params_rejects_each_reserved_key(key):
    with pytest.raises(ValueError) as exc:
        cbi.validate_extra_params({key: "x"}, dataset="users")
    msg = str(exc.value)
    assert "users" in msg
    assert key in msg


def test_validate_extra_params_rejects_non_dict():
    with pytest.raises(ValueError) as exc:
        cbi.validate_extra_params(["not", "a", "dict"], dataset="users")
    assert "users" in str(exc.value)


def test_validate_extra_params_none_becomes_empty_dict():
    assert cbi.validate_extra_params(None, dataset="users") == {}


def test_validate_extra_params_valid_dict_passes_through():
    extra = {"created-at[gt_or_eq]": "2026-01-01T00:00:00Z", "active": "true"}
    assert cbi.validate_extra_params(extra, dataset="users") == extra


# ── extra_params: fetch_page / fetch_at_rank wiring ──────────────────────────

def test_fetch_page_extra_params_none_is_byte_identical_to_no_kwarg(tmp_path):
    # the no-op guarantee: a dataset without extra_params must produce
    # EXACTLY today's params dict — construction order unchanged
    _cfg(tmp_path)
    session = ParamCapture()
    cbi.fetch_page(session, "api/users", ["id", "name"], "2026-07-15T00:00:00Z",
                   before_id=500, id_gt=100, limit=50, extra_params=None)
    assert session.params == {
        "fields":               json.dumps(["id", "name"]),
        "order_by":             "id",
        "dir":                  "desc",
        "offset":               0,
        "updated-at[lt_or_eq]": "2026-07-15T00:00:00Z",
        "id[lt]":               500,
        "id[gt]":               100,
        "limit":                50,
    }


def test_fetch_at_rank_extra_params_none_is_byte_identical_to_no_kwarg(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    cbi.fetch_at_rank(session, "api/users", "2026-07-15T00:00:00Z", 12345,
                      extra_params=None)
    assert session.params == {
        "fields":               json.dumps(["id"]),
        "order_by":             "id",
        "dir":                  "asc",
        "offset":               12345,
        "limit":                1,
        "updated-at[lt_or_eq]": "2026-07-15T00:00:00Z",
    }


def test_fetch_page_merges_extra_params_without_disturbing_base_keys(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    extra = {"created-at[gt_or_eq]": "2026-01-01T00:00:00Z", "active": "true"}
    cbi.fetch_page(session, "api/users", ["id"], "2026-07-15T00:00:00Z",
                   before_id=500, extra_params=extra)
    p = session.params
    assert p["created-at[gt_or_eq]"] == "2026-01-01T00:00:00Z"
    assert p["active"] == "true"
    assert p["order_by"] == "id" and p["dir"] == "desc" and p["offset"] == 0
    assert p["id[lt]"] == 500


def test_fetch_at_rank_merges_extra_params_without_disturbing_base_keys(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    extra = {"created-at[gt_or_eq]": "2026-01-01T00:00:00Z", "active": "true"}
    cbi.fetch_at_rank(session, "api/users", "2026-07-15T00:00:00Z", 5,
                      extra_params=extra)
    p = session.params
    assert p["created-at[gt_or_eq]"] == "2026-01-01T00:00:00Z"
    assert p["active"] == "true"
    assert p["order_by"] == "id" and p["dir"] == "asc" and p["offset"] == 5


def test_fetch_page_raises_when_reserved_key_sneaks_past(tmp_path):
    # belt-and-suspenders: load_config already rejects this, but fetch_page
    # itself must never silently let a reserved key through if some other
    # caller (or a future code path) skips validate_extra_params
    _cfg(tmp_path)
    session = ParamCapture()
    with pytest.raises(ValueError):
        cbi.fetch_page(session, "api/users", ["id"], "t",
                       extra_params={"order_by": "name"})


def test_fetch_at_rank_raises_when_reserved_key_sneaks_past(tmp_path):
    _cfg(tmp_path)
    session = ParamCapture()
    with pytest.raises(ValueError):
        cbi.fetch_at_rank(session, "api/users", "t", 0,
                          extra_params={"limit": 5})
