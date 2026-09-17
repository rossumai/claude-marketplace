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


# --- _resource_url / _resource_urls (forward counterpart of _url_to_id) ---

def test_resource_url():
    base = "https://elis.rossum.ai"
    assert server._resource_url(base, "queues", 7) == "https://elis.rossum.ai/api/v1/queues/7"
    # round-trips with the reverse helper
    assert server._url_to_id(server._resource_url(base, "hooks", 12345)) == 12345


def test_resource_urls():
    base = "https://elis.rossum.ai"
    assert server._resource_urls(base, "queues", [7, 8]) == [
        "https://elis.rossum.ai/api/v1/queues/7",
        "https://elis.rossum.ai/api/v1/queues/8",
    ]
    assert server._resource_urls(base, "queues", []) == []


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


def test_http_request_empty_body_returns_empty_dict(monkeypatch):
    # A 2xx with an empty body (e.g. POST /annotations/purge_deleted -> 202 no content)
    # must not crash json.loads; _http_request returns {} instead.
    monkeypatch.setattr(server, "_cached_token", "t")

    class _Resp:
        status = 202
        def read(self): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda req, **k: _Resp())
    out = server._http_request(1, "https://x.rossum.ai/api/v1/annotations/purge_deleted",
                               method="POST", body={"annotations": []})
    assert out == {}


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

    monkeypatch.setattr(server, "_http_request_raw", fake_raw)

    def _req(rid, url, **k):
        if "/tasks/" in url:
            return {"status": "succeeded",
                    "content": {"upload": "https://x.rossum.ai/api/v1/uploads/9"}}
        return {"annotations": ["https://x.rossum.ai/api/v1/annotations/3"]}

    monkeypatch.setattr(server, "_http_request", _req)
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


def test_upload_to_queue_succeeded_no_url_emits_distinct_message(monkeypatch):
    _seed_upload(monkeypatch)
    monkeypatch.setattr(server, "_http_request_raw",
                        lambda *a, **k: {"url": "https://x.rossum.ai/api/v1/tasks/1"})
    # succeeded but no upload/result_url in response
    monkeypatch.setattr(server, "_http_request",
                        lambda rid, url, **k: {"status": "succeeded"})
    emitted = []
    monkeypatch.setattr(server, "write_message", lambda m: emitted.append(m))
    out = server._upload_to_queue(1, "https://x.rossum.ai", 5, b"d", "f.pdf")
    assert out is None
    assert emitted[-1]["result"].get("isError")
    msg = emitted[-1]["result"]["content"][0]["text"]
    assert "succeeded" in msg and "no upload URL" in msg, (
        f"expected 'succeeded but exposed no upload URL' message, got: {msg!r}"
    )
    assert "timeout" not in msg.lower(), "must not say 'timeout' when task actually succeeded"


def test_upload_to_queue_polls_task_with_no_redirect(monkeypatch):
    # The task GET must carry ?no_redirect=true so a succeeded task returns 200 with its
    # status (rather than a 303 to its result that urllib would auto-follow).
    _seed_upload(monkeypatch)
    monkeypatch.setattr(server, "_http_request_raw",
                        lambda *a, **k: {"url": "https://x.rossum.ai/api/v1/tasks/1"})
    seen = {}

    def _req(rid, url, **k):
        if "/tasks/" in url:
            seen["task_url"] = url
            return {"status": "succeeded",
                    "content": {"upload": "https://x.rossum.ai/api/v1/uploads/9"}}
        return {"annotations": ["https://x.rossum.ai/api/v1/annotations/3"]}

    monkeypatch.setattr(server, "_http_request", _req)
    monkeypatch.setattr(server, "write_message", lambda m: None)
    out = server._upload_to_queue(1, "https://x.rossum.ai", 5, b"d", "f.pdf")
    assert out == "https://x.rossum.ai/api/v1/annotations/3"
    assert seen["task_url"].endswith("/tasks/1?no_redirect=true")


# --- _walk_compact_content: fields-filter table semantics (review round) ---

_WALK_TREE = [{
    "category": "section",
    "children": [
        {"category": "datapoint", "id": 1, "schema_id": "invoice_id",
         "content": {"value": "INV-1"}, "validation_sources": ["human"]},
        {"category": "multivalue", "id": 30, "schema_id": "line_items", "children": [
            {"category": "tuple", "id": 31, "children": [
                {"category": "datapoint", "id": 32, "schema_id": "item_desc",
                 "content": {"value": "Widget"}, "validation_sources": ["human"]},
                {"category": "datapoint", "id": 33, "schema_id": "item_qty",
                 "content": {"value": "2"}, "validation_sources": ["human"]},
            ]},
            {"category": "tuple", "id": 41, "children": [
                {"category": "datapoint", "id": 42, "schema_id": "item_desc",
                 "content": {"value": "Bolt"}, "validation_sources": ["human"]},
            ]},
        ]},
    ],
}]


def test_walk_compact_fields_filter_skips_unmatched_tables():
    # header-only filter: no {} row skeletons for tables with zero matching cells
    fields, tables = server._walk_compact_content(_WALK_TREE, fields=["invoice_id"])
    assert "invoice_id" in fields
    assert tables == {}


def test_walk_compact_fields_filter_multivalue_id_selects_whole_table():
    fields, tables = server._walk_compact_content(_WALK_TREE, fields=["line_items"])
    assert fields == {}
    rows = tables["line_items"]["rows"]
    assert rows[0]["item_desc"]["value"] == "Widget"
    assert rows[0]["item_qty"]["value"] == "2"      # all cells, not filtered out


def test_walk_compact_include_ids():
    fields, tables = server._walk_compact_content(
        _WALK_TREE, fields=["item_desc"], include_ids=True)
    rows = tables["line_items"]["rows"]
    assert rows[0]["_row_id"] == 31 and rows[1]["_row_id"] == 41
    assert rows[0]["item_desc"]["id"] == 32


def test_walk_compact_default_shape_unchanged():
    # no filter, no ids: rossum_get_annotation's existing output must not change
    fields, tables = server._walk_compact_content(_WALK_TREE)
    assert fields["invoice_id"] == {"value": "INV-1", "src": "human"}
    assert tables["line_items"]["rows"][0]["item_desc"] == {"value": "Widget", "src": "human"}
    assert "_row_id" not in tables["line_items"]["rows"][0]


# --- schema content file-path I/O: canonical hash + structural integrity diff ---
# The API injects default keys on write (rir_field_names, default_value, section icon)
# and silently DROPS keys it does not know, so a write's integrity is "everything sent
# is present and equal in what landed" — extras are reported, not failed.

_MIN_DP = {"category": "datapoint", "id": "f", "label": "F", "type": "string"}
_SENT = [{"category": "section", "id": "s", "label": "S", "children": [dict(_MIN_DP)]}]


def _landed_like_api(sent):
    """What the API hands back for _SENT: same content plus the defaults it adds."""
    import copy
    landed = copy.deepcopy(sent)
    landed[0]["icon"] = None
    landed[0]["children"][0] = {"rir_field_names": [], "default_value": None, **landed[0]["children"][0]}
    return landed


def test_canonical_sha256_ignores_key_order_and_float_spelling():
    a = {"b": 1.0, "a": [1, {"y": 2, "x": 3}]}
    b = {"a": [1, {"x": 3, "y": 2}], "b": 1.0}
    assert server._canonical_sha256(a) == server._canonical_sha256(b)
    assert len(server._canonical_sha256(a)) == 64


def test_json_integrity_identical_is_verified_with_no_extras():
    out = server._json_integrity(_SENT, _SENT)
    assert out["verified"] is True
    assert out["sent_sha256"] == out["landed_sha256"] == server._canonical_sha256(_SENT)
    assert "injected_defaults" not in out and "dropped" not in out and "changed" not in out


def test_json_integrity_injected_defaults_still_verify():
    out = server._json_integrity(_SENT, _landed_like_api(_SENT))
    assert out["verified"] is True
    assert out["injected_defaults"] == {"icon": 1, "rir_field_names": 1, "default_value": 1}
    assert out["sent_sha256"] != out["landed_sha256"]


def test_json_integrity_dropped_key_fails_with_path():
    import copy
    sent = copy.deepcopy(_SENT)
    sent[0]["children"][0]["x_unknown_key"] = 1
    out = server._json_integrity(sent, _landed_like_api(_SENT))
    assert out["verified"] is False
    assert out["dropped"] == ["content[0].children[0].x_unknown_key"]
    assert "changed" not in out


def test_json_integrity_changed_scalar_and_list_length():
    import copy
    landed = copy.deepcopy(_SENT)
    landed[0]["children"][0]["label"] = "G"
    landed[0]["children"].append(dict(_MIN_DP, id="extra"))
    out = server._json_integrity(_SENT, landed)
    assert out["verified"] is False
    assert {"path": "content[0].children", "sent_length": 1, "landed_length": 2} in out["changed"]
    # length mismatch stops recursion into that list, so the label change is NOT reported
    assert not any(c.get("path") == "content[0].children[0].label" for c in out["changed"])


def test_json_integrity_type_mismatch_is_a_change():
    # A container-vs-container type mismatch (list vs dict) is summarised, not inlined —
    # see test_json_integrity_container_mismatch_is_compact below for the reason why.
    out = server._json_integrity({"a": [1]}, {"a": {"x": 1}}, root="settings")
    assert out["verified"] is False
    assert out["changed"] == [
        {
            "path": "settings.a",
            "sent_type": "list",
            "sent_size": 1,
            "landed_type": "dict",
            "landed_size": 1,
        }
    ]


def test_json_integrity_scalar_mismatch_keeps_inline_values():
    out = server._json_integrity({"a": 1, "b": "x"}, {"a": 2, "b": "y"}, root="settings")
    assert out["verified"] is False
    assert {"path": "settings.a", "sent": 1, "landed": 2} in out["changed"]
    assert {"path": "settings.b", "sent": "x", "landed": "y"} in out["changed"]


def test_json_integrity_container_mismatch_is_compact():
    # A multivalue's `children` sent as a list but landed as a dict (or vice versa) used
    # to inline both whole sub-trees into `changed` — for a large multivalue that alone
    # can dwarf the rest of the response. The branch must instead summarise: type + size
    # on each side, no sub-tree content.
    big_list = [{"id": str(i), "value": i} for i in range(30)]
    big_dict = {"category": "tuple", "children": list(big_list)}
    out = server._json_integrity({"a": big_list}, {"a": big_dict}, root="settings")
    assert out["verified"] is False
    entry = out["changed"][0]
    assert entry["path"] == "settings.a"
    assert entry["sent_type"] == "list" and entry["sent_size"] == 30
    assert entry["landed_type"] == "dict" and entry["landed_size"] == 2
    assert "sent" not in entry and "landed" not in entry
    # the whole result — not just this entry — must stay compact
    assert len(json.dumps(out)) < 800


# --- _load_json_field: bare array or whole object, errors before any HTTP ---

def _write(tmp_path, name, obj_or_text):
    p = tmp_path / name
    if isinstance(obj_or_text, str):
        p.write_text(obj_or_text, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj_or_text), encoding="utf-8")
    return str(p)


def test_load_json_field_bare_array(tmp_path):
    value, ignored, file_id = server._load_json_field(_write(tmp_path, "c.json", _SENT), "content")
    assert value == _SENT and ignored == [] and file_id is None


def test_load_json_field_whole_object_reports_ignored_keys(tmp_path):
    whole = {"id": 1, "name": "S", "queues": [], "url": "u", "content": _SENT, "metadata": {}, "modified_by": "m"}
    value, ignored, file_id = server._load_json_field(_write(tmp_path, "s.json", whole), "content")
    assert value == _SENT
    assert ignored == ["id", "name", "queues", "url", "metadata", "modified_by"]  # file order, key excluded
    assert file_id == 1


@pytest.mark.parametrize("doc,fragment", [
    ({"id": 1, "name": "S"}, "without a 'content' key"),
    ({"content": {"not": "a list"}}, "must be a JSON array"),
    ("42", "JSON array or an object"),
    ('{"content": [', "not valid JSON"),
])
def test_load_json_field_rejects_wrong_shapes(tmp_path, doc, fragment):
    path = _write(tmp_path, "bad.json", doc)
    with pytest.raises(server._FileInputError) as exc:
        server._load_json_field(path, "content")
    assert fragment in str(exc.value)
    assert path in str(exc.value)


def test_load_json_field_missing_file(tmp_path):
    with pytest.raises(server._FileInputError) as exc:
        server._load_json_field(str(tmp_path / "nope.json"), "content")
    assert "not found" in str(exc.value).lower()


def test_load_json_field_rejects_duplicate_key_in_a_nested_object(tmp_path):
    # A copy-paste slip inside a datapoint: json.loads would silently keep only the LAST
    # "id", so the duplicate never reaches the caller — reject it instead.
    text = (
        '{"content": [{"category": "section", "id": "s", "children": ['
        '{"category": "datapoint", "id": "f", "id": "g", "type": "string"}]}]}'
    )
    path = _write(tmp_path, "dup.json", text)
    with pytest.raises(server._FileInputError) as exc:
        server._load_json_field(path, "content")
    assert "duplicate key" in str(exc.value)
    assert "'id'" in str(exc.value)


def test_load_json_field_strips_utf8_bom(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_SENT).encode("utf-8"))
    value, ignored, file_id = server._load_json_field(str(path), "content")
    assert value == _SENT and ignored == [] and file_id is None


# --- _load_json_dict_field: bare object or whole-hook wrapper, ambiguity refused ---
# `settings` is a dict, so both shapes are JSON objects. The wrapper is recognised by
# structure: a file with `settings` AND another hook field is a hook; a file with
# neither `settings` nor any hook marker is the bare settings object. The two mixed
# cases are both refused rather than guessed: `settings` alone (no markers) could be a
# wrapper stripped to one field or a bare object that happens to be named `settings`;
# hook markers with no `settings` key at all is a whole hook that simply has none.

_SETTINGS = {"configurations": [{"source": {"queries": [{"$match": {"a": 1}}]}}], "n": 2}
_HOOK = {"id": 9, "type": "function", "name": "H", "settings": _SETTINGS,
         "config": {"runtime": "python3.12", "code": "x"}, "events": ["invocation.manual"]}


def _load_settings(path):
    return server._load_json_dict_field(path, "settings", wrapper_markers=server._HOOK_OBJECT_KEYS)


def test_load_json_dict_field_bare_object(tmp_path):
    value, ignored, file_id = _load_settings(_write(tmp_path, "s.json", _SETTINGS))
    assert value == _SETTINGS and ignored == [] and file_id is None


def test_load_json_dict_field_wrapper_reports_ignored_keys_and_id(tmp_path):
    value, ignored, file_id = _load_settings(_write(tmp_path, "h.json", _HOOK))
    assert value == _SETTINGS
    assert ignored == ["id", "type", "name", "config", "events"]   # file order, minus settings
    assert file_id == 9


def test_load_json_dict_field_wrapper_needs_only_one_marker(tmp_path):
    value, ignored, file_id = _load_settings(_write(tmp_path, "h.json", {"type": "webhook", "settings": {"a": 1}}))
    assert value == {"a": 1} and ignored == ["type"] and file_id is None


def test_load_json_dict_field_refuses_hook_without_settings_key(tmp_path):
    # Mirror case of the ambiguity refusal below: a file that is plainly a whole hook
    # object (it carries several _HOOK_OBJECT_KEYS markers) but has no `settings` key at
    # all must be refused, not silently returned AS the settings object — that would
    # send the hook's id/type/config/events wholesale as the new settings.
    hookish = {"id": 9, "type": "function", "config": {"runtime": "python3.12", "code": "x"},
               "events": ["invocation.manual"]}
    with pytest.raises(server._FileInputError, match="cannot be the settings object either"):
        _load_settings(_write(tmp_path, "hook_no_settings.json", hookish))


def test_load_json_dict_field_bare_settings_with_no_markers_still_loads(tmp_path):
    # Regression guard for the fix above: a genuine bare settings object that happens to
    # carry NONE of the hook markers must still load — the new refusal is keyed off the
    # markers actually being present, not merely off `settings` being absent.
    bare = {"base_url": "https://example.com", "queries": [{"field": "vendor_id"}], "n": 3}
    value, ignored, file_id = _load_settings(_write(tmp_path, "bare_settings.json", bare))
    assert value == bare and ignored == [] and file_id is None


def test_load_json_dict_field_refuses_settings_key_without_markers(tmp_path):
    with pytest.raises(server._FileInputError, match="cannot be told apart"):
        _load_settings(_write(tmp_path, "amb.json", {"settings": {"a": 1}}))
    # a settings object that itself carries a 'settings' key looks identical — same refusal
    with pytest.raises(server._FileInputError, match="cannot be told apart"):
        _load_settings(_write(tmp_path, "amb2.json", {"settings": {"nested": True}, "other": 1}))


@pytest.mark.parametrize("doc,fragment", [
    ([1, 2], "must contain a JSON object"),
    ("\"just a string\"", "must contain a JSON object"),
    ({"id": 9, "settings": [1, 2]}, "'settings' must be a JSON object"),
    ({"id": 9, "settings": None}, "'settings' must be a JSON object"),
], ids=["array", "string", "settings-is-list", "settings-is-null"])
def test_load_json_dict_field_rejects_wrong_shapes(tmp_path, doc, fragment):
    with pytest.raises(server._FileInputError, match=fragment):
        _load_settings(_write(tmp_path, "bad.json", doc))


def test_load_json_dict_field_shares_the_strict_reader(tmp_path):
    with pytest.raises(server._FileInputError, match="File not found"):
        _load_settings(str(tmp_path / "nope.json"))
    with pytest.raises(server._FileInputError, match="not valid JSON"):
        _load_settings(_write(tmp_path, "bad.json", "{not json"))
    with pytest.raises(server._FileInputError, match="duplicate key 'a'"):
        _load_settings(_write(tmp_path, "dup.json", '{"x": {"a": 1, "a": 2}}'))
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(_SETTINGS).encode("utf-8"))
    assert _load_settings(str(bom))[0] == _SETTINGS


def test_load_json_dict_field_empty_object_is_returned_not_refused(tmp_path):
    # The {} guard belongs to the resolver (it knows {} wipes the field); the loader is shape-only.
    assert _load_settings(_write(tmp_path, "e.json", {})) == ({}, [], None)


# --- _count_datapoints + _write_json_file ---

def test_count_datapoints_handles_multivalue_tuple_children():
    content = [{"category": "section", "id": "s", "children": [
        {"category": "datapoint", "id": "a", "type": "string"},
        {"category": "multivalue", "id": "mv", "children": {          # a dict, not a list
            "category": "tuple", "id": "t", "children": [
                {"category": "datapoint", "id": "b", "type": "string"},
                {"category": "datapoint", "id": "c", "type": "number"}]}},
    ]}]
    assert server._count_datapoints(content) == 3
    assert server._count_datapoints([]) == 0
    assert server._count_datapoints(None) == 0


def test_count_datapoints_scalar_children_never_raises():
    # A caller-supplied file can have malformed `children`; a scalar must contribute 0
    # rather than raising (previously: `for node in nodes or ()` blew up with
    # "'int' object is not iterable").
    content = [{"category": "datapoint", "id": "f", "children": 5}]
    assert server._count_datapoints(content) == 1  # the datapoint itself still counts
    assert server._count_datapoints(5) == 0
    assert server._count_datapoints(5.5) == 0
    assert server._count_datapoints(True) == 0


def test_count_datapoints_string_children_not_iterated_as_chars():
    # A string is iterable in Python, so `children: "abc"` used to silently walk its
    # characters (each skipped since a str isn't a dict) instead of being rejected as
    # the wrong shape outright — contributes 0, and a top-level string never counts
    # characters as nodes.
    content = [{"category": "datapoint", "id": "f", "children": "abc"}]
    assert server._count_datapoints(content) == 1
    assert server._count_datapoints("abc") == 0
    assert server._count_datapoints("") == 0


def test_write_json_file_preserves_key_order_and_unicode(tmp_path):
    obj = {"id": 1, "name": "Číslo ✓", "content": [{"category": "section", "id": "s"}]}
    path = str(tmp_path / "out" / "schema.json")          # parent dir does not exist yet
    n = server._write_json_file(path, obj)
    text = open(path, encoding="utf-8").read()
    assert n == len(text)
    assert text == json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    assert text.index('"id"') < text.index('"name"') < text.index('"content"')   # API order, not sorted
    assert "Číslo ✓" in text                                                       # not \u-escaped


def test_write_json_file_rewrites_identical_file(tmp_path):
    obj = {"a": 1, "b": [1, 2]}
    path = str(tmp_path / "s.json")
    (tmp_path / "s.json").write_text('{"b": [1, 2], "a": 1}', encoding="utf-8")   # same object, other order
    server._write_json_file(path, obj)
    assert json.loads(open(path, encoding="utf-8").read()) == obj


def test_write_json_file_bom_in_existing_file_does_not_look_different(tmp_path):
    # A file saved by a Windows editor carries a UTF-8 BOM. Without utf-8-sig on the
    # existing-file check, a byte-identical object reads as "different" and the write is
    # refused — even though nothing would actually change on disk.
    obj = {"a": 1, "b": [1, 2]}
    path = tmp_path / "s.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(obj).encode("utf-8"))
    server._write_json_file(str(path), obj)                # must NOT raise
    assert json.loads(open(path, encoding="utf-8-sig").read()) == obj


@pytest.mark.parametrize("existing", ['{"a": 2}', "not json at all"])
def test_write_json_file_refuses_to_overwrite_a_differing_file(tmp_path, existing):
    path = tmp_path / "s.json"
    path.write_text(existing, encoding="utf-8")
    with pytest.raises(server._FileInputError) as exc:
        server._write_json_file(str(path), {"a": 1})
    assert "Refusing to overwrite" in str(exc.value)
    assert path.read_text(encoding="utf-8") == existing, "must not touch the file"


def test_load_json_dict_field_settings_with_single_incidental_metadata_key(tmp_path):
    # Regression guard: a legitimate settings object carrying one incidental hook-named key
    # (metadata) must load without refusal. This shape was measured in real production data
    # (2 out of 833 settings objects). With the old "any marker" rule, these would have
    # been wrongly refused. A genuine whole-hook object carries ~13 markers, so the
    # threshold of "two or more markers" cleanly separates the two shapes.
    settings_with_metadata = {
        "configurations": [{"source": "x"}],
        "metadata": {"owner": "team"}
    }
    value, ignored, file_id = _load_settings(_write(tmp_path, "settings_meta.json", settings_with_metadata))
    assert value == settings_with_metadata
    assert ignored == []
    assert file_id is None


def test_load_json_dict_field_refuses_two_markers_without_settings_key(tmp_path):
    # Pins the refusal threshold from the other side: two or more markers without the
    # settings key must be refused as a whole hook object that simply has no settings.
    hookish = {
        "type": "function",
        "config": {"runtime": "python3.12", "code": "x"},
        "active": True
    }
    with pytest.raises(server._FileInputError, match="cannot be the settings object either"):
        _load_settings(_write(tmp_path, "two_markers_no_settings.json", hookish))
