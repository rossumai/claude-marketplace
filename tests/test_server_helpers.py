"""Unit tests for the MCP server's pure helpers (Phase 2a).

These exercise the parsing/normalization/compaction logic directly — no token,
no network. server.py guards its main loop behind `if __name__ == "__main__"`,
so importing it is side-effect-free. _paginate is covered by monkeypatching the
HTTP boundary (server._http_request), the same approach the contract tests use.
"""
from __future__ import annotations

import json
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


def test_auth_headers_includes_ua_without_marker(monkeypatch):
    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server, "_current_tool", None)
    h = server._auth_headers()
    assert h["Authorization"] == "Bearer tok"
    assert h["User-Agent"] == f"rossum-sa-mcp/{server._SERVER_VERSION}"
    assert "X-Rossum-MCP-Tool" not in h


def test_auth_headers_adds_marker_and_extra(monkeypatch):
    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server, "_current_tool", "rossum_get")
    h = server._auth_headers({"Content-Type": "application/json"})
    assert h["X-Rossum-MCP-Tool"] == "rossum_get"
    assert h["Content-Type"] == "application/json"
    assert h["Authorization"] == "Bearer tok"
    assert h["User-Agent"] == f"rossum-sa-mcp/{server._SERVER_VERSION}"


# --- rossum_get (handle_rossum_get) ---

def _connect(monkeypatch):
    monkeypatch.setattr(server, "_cached_base_url", "https://acme.rossum.app")
    monkeypatch.setattr(server, "_cached_token", "tok")
    monkeypatch.setattr(server, "_token_validated", True)


def _capture_result(monkeypatch):
    out = {}
    monkeypatch.setattr(server, "tool_result",
                        lambda rid, text, is_error=False: out.update(text=text, is_error=is_error))
    return out


def test_rossum_get_rejects_foreign_host(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    server.handle_rossum_get("1", {"path": "https://evil.example.com/api/v1/queues"})
    assert out["is_error"] and "connected org" in out["text"]


def test_rossum_get_rejects_non_api_path(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    server.handle_rossum_get("1", {"path": "/svc/data-storage/api/x"})
    assert out["is_error"]


def test_rossum_get_single_object(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    monkeypatch.setattr(server, "_http_get_typed",
                        lambda rid, url: ("application/json", {"id": 5, "name": "q"}))
    server.handle_rossum_get("1", {"path": "/api/v1/engines/5"})
    assert json.loads(out["text"]) == {"id": 5, "name": "q"}
    assert not out.get("is_error")


def test_rossum_get_paginates_list(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    monkeypatch.setattr(server, "_http_get_typed",
                        lambda rid, url: ("application/json",
                                          {"pagination": {"total": 2}, "results": [{"id": 1}]}))
    monkeypatch.setattr(server, "_paginate", lambda rid, url, **kw: ([{"id": 1}, {"id": 2}], 2))
    server.handle_rossum_get("1", {"path": "/api/v1/engines"})
    body = json.loads(out["text"])
    assert body == {"total": 2, "returned": 2, "results": [{"id": 1}, {"id": 2}]}


def test_rossum_get_non_json_returns_pointer(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    monkeypatch.setattr(server, "_http_get_typed", lambda rid, url: ("application/pdf", None))
    server.handle_rossum_get("1", {"path": "/api/v1/documents/9/content"})
    body = json.loads(out["text"])
    assert body["content_type"] == "application/pdf" and body["url"].endswith("/documents/9/content")


def test_rossum_get_paginate_error_is_silent(monkeypatch):
    _connect(monkeypatch)
    out = _capture_result(monkeypatch)
    monkeypatch.setattr(server, "_http_get_typed",
                        lambda rid, url: ("application/json",
                                          {"pagination": {}, "results": []}))
    monkeypatch.setattr(server, "_paginate", lambda rid, url, **kw: None)
    server.handle_rossum_get("1", {"path": "/api/v1/engines"})
    assert not out  # no duplicate tool_result on pagination error


def test_paginate_uses_initial_page_without_refetch(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_http_request",
                        lambda rid, u, **kw: calls.append(u) or None)
    first = {"pagination": {"total": 1, "next": None}, "results": [{"id": 1}]}
    results, total = server._paginate("1", "https://acme.rossum.app/api/v1/engines",
                                      initial_page=first)
    assert results == [{"id": 1}] and total == 1
    assert calls == []  # page 1 came from initial_page, no fetch


# --- _build_search_query (POST /annotations/search body builder) ---

def test_build_search_query_wraps_query_string():
    body = server._build_search_query(base="https://x.rossum.ai", query=None,
                                      query_string="acme", queue=None, queues=None)
    assert body == {"query_string": {"string": "acme"}}


def test_build_search_query_injects_queue_scope_into_and():
    body = server._build_search_query(base="https://x.rossum.ai", query=None,
                                      query_string=None, queue=7, queues=None)
    assert body == {"query": {"$and": [
        {"queue": {"$in": ["https://x.rossum.ai/api/v1/queues/7"]}}]}}


def test_build_search_query_merges_user_and_clause():
    user_q = {"$and": [{"field.vendor.string": {"$eq": "ACME"}}]}
    body = server._build_search_query(base="https://x.rossum.ai", query=user_q,
                                      query_string=None, queue=None, queues=[7, 8])
    assert body["query"]["$and"] == [
        {"queue": {"$in": ["https://x.rossum.ai/api/v1/queues/7",
                           "https://x.rossum.ai/api/v1/queues/8"]}},
        {"field.vendor.string": {"$eq": "ACME"}},
    ]


def test_build_search_query_wraps_bare_user_query_without_and():
    # A user query that is not already in $and form is wrapped into the $and list.
    body = server._build_search_query(base="https://x.rossum.ai",
                                      query={"status": {"$eq": "to_review"}},
                                      query_string=None, queue=None, queues=None)
    assert body["query"]["$and"] == [{"status": {"$eq": "to_review"}}]


def test_build_search_query_empty_is_empty_body():
    assert server._build_search_query(base="https://x.rossum.ai", query=None,
                                      query_string=None, queue=None, queues=None) == {}


# --- _content_type_for ---

def test_content_type_for_known_and_unknown():
    assert server._content_type_for("invoice.pdf") == "application/pdf"
    assert server._content_type_for("scan.PNG") == "image/png"
    assert server._content_type_for("a.jpeg") == "image/jpeg"
    assert server._content_type_for("a.tif") == "image/tiff"
    assert server._content_type_for("noextension") == "application/octet-stream"
    assert server._content_type_for("weird.xyz") == "application/octet-stream"


# --- _upload_to_queue ---

def _seed_upload(monkeypatch):
    monkeypatch.setattr(server, "_cached_token", "t")
    monkeypatch.setattr(server, "_cached_base_url", "https://x.rossum.ai")
    monkeypatch.setattr(server.time, "sleep", lambda s: None)


def test_upload_to_queue_happy_path_builds_multipart_and_returns_annotation(monkeypatch):
    _seed_upload(monkeypatch)
    captured = {}

    def fake_raw(request_id, url, *, method="POST", raw_body=b"", content_type=None):
        captured["url"] = url
        captured["body"] = raw_body
        captured["ct"] = content_type
        return {"url": "https://x.rossum.ai/api/v1/tasks/1"}

    def responder(request_id, url, **k):
        if "tasks/1" in url:
            return {"status": "succeeded",
                    "content": {"upload": "https://x.rossum.ai/api/v1/uploads/9"}}
        return {"annotations": ["https://x.rossum.ai/api/v1/annotations/3"]}

    monkeypatch.setattr(server, "_http_request_raw", fake_raw)
    monkeypatch.setattr(server, "_http_request", responder)
    monkeypatch.setattr(server, "write_message", lambda m: None)

    out = server._upload_to_queue(
        1, "https://x.rossum.ai", 5, b"PDFDATA", "f.pdf",
        metadata='{"k":1}', reject_identical=True,
    )
    assert out == "https://x.rossum.ai/api/v1/annotations/3"
    assert "queue=5" in captured["url"]
    assert "reject_identical=true" in captured["url"]
    assert captured["ct"].startswith("multipart/form-data; boundary=")
    assert b'name="content"; filename="f.pdf"' in captured["body"]
    assert b"application/pdf" in captured["body"]
    assert b"PDFDATA" in captured["body"]
    assert b'name="metadata"' in captured["body"]
    assert b'{"k":1}' in captured["body"]


def test_upload_to_queue_task_failed_emits_error(monkeypatch):
    _seed_upload(monkeypatch)
    monkeypatch.setattr(server, "_http_request_raw",
                        lambda *a, **k: {"url": "https://x.rossum.ai/api/v1/tasks/1"})
    monkeypatch.setattr(server, "_http_request",
                        lambda rid, url, **k: {"status": "failed", "detail": "boom"})
    emitted = []
    monkeypatch.setattr(server, "write_message", lambda m: emitted.append(m))
    out = server._upload_to_queue(1, "https://x.rossum.ai", 5, b"d", "f.pdf")
    assert out is None
    assert emitted[-1]["result"].get("isError")
    assert "boom" in emitted[-1]["result"]["content"][0]["text"]
