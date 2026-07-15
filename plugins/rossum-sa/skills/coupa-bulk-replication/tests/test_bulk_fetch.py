"""Keyset fetch primitives. The query shapes ARE the Coupa API contract this
skill runs on (order_by=id + id[lt]/id[gt] + updated-at anchor — see spec
§4.1/§9, live-validated before publish), so they are pinned ONCE here, per
the testing-bar exception for live-verified API contracts."""
import json

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
