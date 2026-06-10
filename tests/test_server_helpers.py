"""Unit tests for the MCP server's pure helpers (Phase 2a).

These exercise the parsing/normalization/compaction logic directly — no token,
no network. server.py guards its main loop behind `if __name__ == "__main__"`,
so importing it is side-effect-free. _paginate is covered by monkeypatching the
HTTP boundary (server._http_request), the same approach the contract tests use.
"""
from __future__ import annotations

import sys

import pytest

import repo_lib as R

sys.path.insert(0, str(R.SERVER_PY.parent))
import server  # noqa: E402  (path must be set up before import)


# --- _parse_connection_string ---

def test_parse_connection_string_empty():
    assert server._parse_connection_string("") == (None, None)
    assert server._parse_connection_string(None) == (None, None)


def test_parse_connection_string_curl_snippet():
    text = "curl -H 'Authorization: Bearer abc123' https://elis.rossum.ai/api/v1/queues"
    token, base = server._parse_connection_string(text)
    assert token == "abc123"
    assert base.startswith("https://elis.rossum.ai")


def test_parse_connection_string_token_only_and_url_only():
    assert server._parse_connection_string("Bearer xyz") == ("xyz", None)
    assert server._parse_connection_string("see https://eu.rossum.ai here") == (
        None, "https://eu.rossum.ai")


# --- _validate_base_url ---

@pytest.mark.parametrize("url,expected", [
    ("https://elis.rossum.ai", "https://elis.rossum.ai"),
    ("https://elis.rossum.ai/api/v1", "https://elis.rossum.ai"),  # path stripped to origin
    ("https://us.api.rossum.ai", "https://us.app.rossum.ai"),     # api -> app rewrite
    ("https://host:8443", "https://host:8443"),                   # non-443 port kept
    ("https://host:443", "https://host"),                         # default port dropped
    ("http://elis.rossum.ai", None),                              # non-https rejected
    ("ftp://x", None),
    ("not a url", None),
    ("https://", None),                                           # no hostname
])
def test_validate_base_url(url, expected):
    assert server._validate_base_url(url) == expected


# --- _url_to_id vs _id_from_url (note the trailing-slash difference) ---

def test_url_to_id():
    assert server._url_to_id("https://elis.rossum.ai/api/v1/hooks/12345") == 12345
    assert server._url_to_id("plainstring") == "plainstring"   # no slash -> unchanged
    assert server._url_to_id(999) == 999                       # non-str -> unchanged
    assert server._url_to_id("https://x/hooks/abc") == "https://x/hooks/abc"  # non-int tail
    # _url_to_id does NOT strip a trailing slash -> empty tail -> returns original
    assert server._url_to_id("https://x/hooks/123/") == "https://x/hooks/123/"


def test_id_from_url():
    assert server._id_from_url("https://elis.rossum.ai/api/v1/hooks/123") == 123
    assert server._id_from_url("https://x/hooks/123/") == 123   # trailing slash IS stripped
    assert server._id_from_url("https://x/hooks/abc") is None
    assert server._id_from_url(None) is None
    assert server._id_from_url(123) is None
    assert server._id_from_url("") is None


# --- _compact_item ---

def test_compact_item_single_list_none_and_missing():
    item = {
        "queue": "https://x/api/v1/queues/5",
        "hooks": ["https://x/api/v1/hooks/1", "https://x/api/v1/hooks/2"],
        "workspace": None,
        "name": "keep me",
    }
    out = server._compact_item(item, {"queue", "hooks", "workspace", "absent"})
    assert out["queue"] == 5
    assert out["hooks"] == [1, 2]
    assert out["workspace"] is None       # None preserved
    assert out["name"] == "keep me"       # non-url field untouched


# --- _paginate (HTTP boundary monkeypatched) ---

def _patch_http(monkeypatch, pages_by_url):
    monkeypatch.setattr(server, "_http_request", lambda rid, url, **kw: pages_by_url.get(url))


def test_paginate_single_page_compacts_and_returns_total(monkeypatch):
    url = "https://elis.rossum.ai/api/v1/queues?page=1"
    _patch_http(monkeypatch, {url: {
        "results": [{"id": 1, "queue": "https://elis.rossum.ai/api/v1/queues/9"}],
        "pagination": {"total": 1, "next": None},
    }})
    results, total = server._paginate(1, url)
    assert total == 1
    assert results == [{"id": 1, "queue": 9}]   # url ref compacted to bare id


def test_paginate_pick_fields_projects(monkeypatch):
    url = "https://elis.rossum.ai/api/v1/queues?page=1"
    _patch_http(monkeypatch, {url: {
        "results": [{"id": 1, "name": "Q", "secret": "drop me"}],
        "pagination": {"total": 1, "next": None},
    }})
    results, _ = server._paginate(1, url, pick_fields=("id", "name"))
    assert results == [{"id": 1, "name": "Q"}]


def test_paginate_follows_next_same_origin(monkeypatch):
    p1 = "https://elis.rossum.ai/api/v1/queues?page=1"
    p2 = "https://elis.rossum.ai/api/v1/queues?page=2"
    _patch_http(monkeypatch, {
        p1: {"results": [{"id": 1}], "pagination": {"total": 2, "next": p2}},
        p2: {"results": [{"id": 2}], "pagination": {"total": 2, "next": None}},
    })
    results, total = server._paginate(1, p1)
    assert [r["id"] for r in results] == [1, 2]
    assert total == 2


def test_paginate_stops_on_cross_origin_next(monkeypatch):
    p1 = "https://elis.rossum.ai/api/v1/queues?page=1"
    evil = "https://evil.example.com/api/v1/queues?page=2"
    _patch_http(monkeypatch, {
        p1: {"results": [{"id": 1}], "pagination": {"total": 2, "next": evil}},
    })
    results, _ = server._paginate(1, p1)
    assert [r["id"] for r in results] == [1]   # did not follow the foreign origin


def test_paginate_respects_max_results(monkeypatch):
    p1 = "https://elis.rossum.ai/api/v1/queues?page=1"
    p2 = "https://elis.rossum.ai/api/v1/queues?page=2"
    _patch_http(monkeypatch, {
        p1: {"results": [{"id": 1}, {"id": 2}], "pagination": {"total": 5, "next": p2}},
    })
    results, _ = server._paginate(1, p1, max_results=1)
    assert [r["id"] for r in results] == [1]


def test_paginate_returns_none_on_error(monkeypatch):
    url = "https://elis.rossum.ai/api/v1/queues?page=1"
    _patch_http(monkeypatch, {url: None})   # _http_request signals error with None
    assert server._paginate(1, url) is None
