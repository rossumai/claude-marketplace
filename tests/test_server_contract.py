"""Contract tests for MCP handlers (Phase 2f) — representative subset + harness.

Each test drives a real handler with the HTTP boundary monkeypatched to return a
canned API response, captures what the handler emits, and asserts on the request
it built and the output it shaped. No token, no network.

This catches OUR handler regressions (URL/param building, field projection,
response shaping, request-body construction). It does NOT detect live API drift —
that needs real recorded responses against a sandbox (Tier-2 live canary). The
fixtures here are hand-authored, representative shapes.

The harness (FakeHTTP + run_handler) is the reusable part; extend coverage by
adding more (handler, args, responder, assertions) cases.
"""
from __future__ import annotations

import json
import sys

import repo_lib as R

sys.path.insert(0, str(R.SERVER_PY.parent))
import server  # noqa: E402  (path must be set up before import)

BASE = "https://elis.rossum.ai"


class FakeHTTP:
    """Stand-in for server._http_request that records calls and returns canned data."""

    def __init__(self, responder):
        self.calls = []
        self._responder = responder  # (url, method, body) -> response | None

    def __call__(self, request_id, url, *, method="GET", body=None, parse_json=True):
        # parse_json is recorded so tests can assert on it (e.g. a handler that
        # asks for raw text), even though the responder still returns whatever
        # shape it wants — letting it canonical-shape per-test.
        self.calls.append({"url": url, "method": method, "body": body, "parse_json": parse_json})
        return self._responder(url, method, body)


def run_handler(monkeypatch, name, args, responder):
    """Run HANDLERS[name](args) with HTTP mocked + connection seeded; return (fake, emitted)."""
    monkeypatch.setattr(server, "_cached_base_url", BASE)
    monkeypatch.setattr(server, "_cached_token", "test-token")
    monkeypatch.setattr(server, "_token_validated", True)
    monkeypatch.setattr(server, "_cache_full_payload", lambda *a, **k: None)  # no FS writes
    fake = FakeHTTP(responder)
    monkeypatch.setattr(server, "_http_request", fake)
    emitted = []
    monkeypatch.setattr(server, "write_message", lambda msg: emitted.append(msg))
    server.HANDLERS[name](1, args)
    return fake, emitted


def emitted_payload(emitted):
    """Parse the JSON text out of the last emitted tool_result message."""
    assert emitted, "handler emitted nothing"
    text = emitted[-1]["result"]["content"][0]["text"]
    return json.loads(text)


# --- list pattern: param building + field projection + url-ref compaction ---

def test_list_queues_projects_and_compacts(monkeypatch):
    page = {
        "results": [{
            "id": 7, "name": "Q",
            "workspace": f"{BASE}/api/v1/workspaces/3",
            "schema": f"{BASE}/api/v1/schemas/4",
            "engine": f"{BASE}/api/v1/engines/99",
            "dedicated_engine": None,
            "generic_engine": None,
            "secret": "should be dropped",
        }],
        "pagination": {"total": 1, "next": None},
    }
    fake, emitted = run_handler(
        monkeypatch, "rossum_list_queues", {"workspace": 3},
        lambda url, method, body: page if "/api/v1/queues" in url else None,
    )
    # request: built the right URL with params
    assert "/api/v1/queues" in fake.calls[0]["url"]
    assert "page_size=100" in fake.calls[0]["url"]
    assert "workspace=3" in fake.calls[0]["url"]
    # output: only _QUEUE_FIELDS kept, URL refs compacted to bare ids, secret dropped
    out = emitted_payload(emitted)
    assert out["total"] == 1
    # workspace/schema and the engine triple are all in _URL_REF_FIELDS → non-null
    # URLs compacted to bare integer IDs; None engine bindings stay None
    assert out["results"] == [{
        "id": 7, "name": "Q",
        "workspace": 3, "schema": 4,
        "engine": 99,
        "dedicated_engine": None,
        "generic_engine": None,
    }]


# --- get pattern: passthrough of a single resource ---

def test_get_queue_passthrough(monkeypatch):
    resource = {"id": 7, "name": "Q", "inbox": f"{BASE}/api/v1/inboxes/2"}
    fake, emitted = run_handler(
        monkeypatch, "rossum_get_queue", {"queue_id": 7},
        lambda url, method, body: resource if url.endswith("/api/v1/queues/7") else None,
    )
    assert fake.calls[0]["url"].endswith("/api/v1/queues/7")
    assert fake.calls[0]["method"] == "GET"
    assert emitted_payload(emitted) == resource  # get does not project/compact


# --- data_storage pattern: body construction + limit capping ---

def test_data_storage_find_builds_body_and_caps_limit(monkeypatch):
    response = {"documents": [{"_id": 1}]}
    fake, emitted = run_handler(
        monkeypatch, "data_storage_find",
        {"collectionName": "vendors", "query": {"x": 1}, "limit": 5000},
        lambda url, method, body: response if "/svc/data-storage/api/v1/data/find" in url else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/svc/data-storage/api/v1/data/find")
    assert call["body"] == {"collectionName": "vendors", "query": {"x": 1}, "limit": 1000}  # capped
    assert emitted_payload(emitted) == response


# --- write pattern: request-body shaping, no real write ---

def test_create_rule_shapes_request_body(monkeypatch):
    created = {"id": 99, "name": "R", "enabled": True}
    fake, emitted = run_handler(
        monkeypatch, "rossum_create_rule",
        {"name": "R", "queue_ids": [7, 8], "trigger_condition": "1 == 1"},
        lambda url, method, body: created if url.endswith("/api/v1/rules") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/rules")
    assert call["body"] == {
        "name": "R",
        "enabled": True,  # defaulted
        "queues": [f"{BASE}/api/v1/queues/7", f"{BASE}/api/v1/queues/8"],  # ids -> URLs
        "trigger_condition": "1 == 1",
    }
    assert emitted_payload(emitted) == created


# --- compact merged view: the richest projection (meta + fields + tables + hooks) ---

def test_get_annotation_compact_merged_view(monkeypatch):
    meta = {
        "id": 55, "status": "to_review",
        "queue": f"{BASE}/api/v1/queues/7",
        "document": f"{BASE}/api/v1/documents/9",
        "modifier": None, "automation_blocker": None,
        "modified_at": "2026-06-10T00:00:00Z", "labels": [], "metadata": {},
    }
    content = {"results": [{
        "category": "section",
        "children": [
            {"category": "datapoint", "schema_id": "invoice_id",
             "content": {"value": "INV-1"}, "validation_sources": ["human"]},
            {"category": "multivalue", "schema_id": "line_items", "children": [
                {"category": "tuple", "children": [
                    {"category": "datapoint", "schema_id": "item_desc",
                     "content": {"value": "Widget"}, "validation_sources": ["score"]},
                ]},
            ]},
        ],
    }]}
    hook_logs = {"results": [{
        "hook_id": 12, "event": "annotation_content", "action": "started",
        "status": "success", "log_level": "INFO", "timestamp": "2026-06-10T00:00:01Z",
        "message": "ok",
    }]}

    def responder(url, method, body):
        if url.endswith("/content"):
            return content
        if "/hooks/logs" in url:
            return hook_logs
        if "/annotations/55" in url:
            return meta
        return None

    _, emitted = run_handler(monkeypatch, "rossum_get_annotation", {"annotation_id": 55}, responder)
    out = emitted_payload(emitted)
    assert out["annotation_id"] == 55 and out["status"] == "to_review"
    assert out["queue_id"] == 7 and out["document_id"] == 9   # url -> id
    assert out["fields"]["invoice_id"] == {"value": "INV-1", "src": "human"}
    assert out["tables"]["line_items"]["count"] == 1
    assert out["tables"]["line_items"]["rows"][0]["item_desc"]["value"] == "Widget"
    assert out["blocker"] is None                            # no automation_blocker
    assert len(out["recent_hooks"]) == 1 and out["recent_hooks"][0]["hook_id"] == 12
    assert out["_meta"]["view"] == "compact"


# --- error path: _http_request returns None (signaling it already emitted the error) ---

def test_handler_short_circuits_on_http_error(monkeypatch):
    """When _http_request returns None — the convention server-wide for "I already
    emitted the error tool_result, you bail out" — the handler must not try to
    add its own success response on top. The mock returns None without emitting,
    so a correctly-wired handler results in zero emitted messages here.

    Picks rossum_get_queue as a representative single-resource handler; the
    same contract applies to every other handler that calls _http_request.
    """
    fake, emitted = run_handler(
        monkeypatch, "rossum_get_queue", {"queue_id": 7},
        lambda url, method, body: None,   # simulate API failure / 404 / auth error
    )
    assert fake.calls, "handler should have at least tried the request"
    assert emitted == [], (
        f"handler should bail out silently when _http_request signals an error; "
        f"got unexpected emissions: {emitted}"
    )


# --- confirm annotation: POST /confirm, side-effecting write ---

def test_confirm_annotation_posts_and_reports(monkeypatch):
    fake, emitted = run_handler(
        monkeypatch, "rossum_confirm_annotation", {"annotation_id": 55},
        lambda url, method, body: 204 if url.endswith("/annotations/55/confirm") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/annotations/55/confirm")
    assert call["body"] is None            # no skip_workflows -> empty body
    assert call["parse_json"] is False     # 204 No Content -> status code, not JSON
    text = emitted[-1]["result"]["content"][0]["text"]
    assert "55" in text and "confirm" in text.lower()


def test_confirm_annotation_includes_skip_workflows(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_confirm_annotation",
        {"annotation_id": 55, "skip_workflows": True},
        lambda url, method, body: 204,
    )
    assert fake.calls[0]["body"] == {"skip_workflows": True}


def test_confirm_annotation_surfaces_http_error(monkeypatch):
    fake, emitted = run_handler(
        monkeypatch, "rossum_confirm_annotation", {"annotation_id": 55},
        lambda url, method, body: 409,
    )
    assert emitted[-1]["result"].get("isError") is True


# --- advanced annotation search: body shaping + POST-cursor pagination ---

def test_paginate_search_follows_next_reposting_body(monkeypatch):
    base = BASE
    page1 = {"pagination": {"total": 3, "next": f"{base}/api/v1/annotations/search?cursor=ABC"},
             "results": [{"id": 1, "queue": f"{base}/api/v1/queues/7", "status": "exported"}]}
    page2 = {"pagination": {"total": 3, "next": None},
             "results": [{"id": 2, "queue": f"{base}/api/v1/queues/7", "status": "exported"},
                         {"id": 3, "queue": f"{base}/api/v1/queues/7", "status": "exported"}]}
    def responder(url, method, body):
        return page1 if "cursor" not in url else page2
    fake, emitted = run_handler(
        monkeypatch, "rossum_search_annotations_advanced",
        {"queue": 7, "query_string": "acme", "max_results": 10},
        responder,
    )
    # Both calls are POST and carry the same body (cursor re-posts the query).
    assert all(c["method"] == "POST" for c in fake.calls)
    assert fake.calls[0]["body"] == {
        "query": {"$and": [{"queue": {"$in": [f"{base}/api/v1/queues/7"]}}]},
        "query_string": {"string": "acme"},
    }
    assert fake.calls[1]["body"] == fake.calls[0]["body"]   # body re-posted to next URL
    assert "page_size=" in fake.calls[0]["url"]
    out = emitted_payload(emitted)
    assert out["total"] == 3 and out["returned"] == 3
    assert [r["id"] for r in out["results"]] == [1, 2, 3]
    # url-ref compaction: queue URL -> bare id, only _ANNOTATION_FIELDS kept
    assert out["results"][0]["queue"] == 7


def test_paginate_search_respects_max_results(monkeypatch):
    base = BASE
    page1 = {"pagination": {"total": 99, "next": f"{base}/api/v1/annotations/search?cursor=X"},
             "results": [{"id": 1, "status": "exported"}, {"id": 2, "status": "exported"}]}
    fake, emitted = run_handler(
        monkeypatch, "rossum_search_annotations_advanced",
        {"query_string": "acme", "max_results": 1},
        lambda url, method, body: page1,
    )
    out = emitted_payload(emitted)
    assert out["returned"] == 1 and out["total"] == 99
    assert len(fake.calls) == 1   # stopped before following next


def test_upload_document_happy_path(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    raw_calls = []

    def fake_raw(request_id, url, *, method="POST", raw_body=b"", content_type=None):
        raw_calls.append({"url": url, "body": raw_body, "ct": content_type})
        return {"url": f"{BASE}/api/v1/tasks/555"}

    monkeypatch.setattr(server, "_http_request_raw", fake_raw)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)

    def responder(url, method, body):
        if "/tasks/555" in url:
            return {"status": "succeeded", "content": {"upload": f"{BASE}/api/v1/uploads/77"}}
        if "/uploads/77" in url:
            return {"annotations": [f"{BASE}/api/v1/annotations/900"]}
        if url.endswith("/annotations/900"):
            return {"id": 900, "status": "to_review"}
        return None

    fake, emitted = run_handler(
        monkeypatch, "rossum_upload_document",
        {"file_path": str(f), "queue_id": 8199}, responder,
    )
    out = emitted_payload(emitted)
    assert out["annotation_id"] == 900
    assert out["status"] == "to_review"
    assert out["queue_id"] == 8199
    assert raw_calls and "/api/v1/uploads?queue=8199" in raw_calls[0]["url"]
    assert raw_calls[0]["ct"].startswith("multipart/form-data; boundary=")
    assert b'filename="doc.pdf"' in raw_calls[0]["body"]
    assert b"application/pdf" in raw_calls[0]["body"]


def test_upload_document_missing_file(monkeypatch):
    fake, emitted = run_handler(
        monkeypatch, "rossum_upload_document",
        {"file_path": "/no/such/file.pdf", "queue_id": 1},
        lambda url, method, body: None,
    )
    res = emitted[-1]["result"]
    assert res.get("isError")
    assert "not found" in res["content"][0]["text"].lower()


def test_upload_document_too_large(monkeypatch, tmp_path):
    import os as _os
    f = tmp_path / "big.pdf"
    f.write_bytes(b"x")
    monkeypatch.setattr(_os.path, "getsize", lambda p: 41 * 1024 * 1024)
    fake, emitted = run_handler(
        monkeypatch, "rossum_upload_document",
        {"file_path": str(f), "queue_id": 1},
        lambda url, method, body: None,
    )
    res = emitted[-1]["result"]
    assert res.get("isError")
    assert "40" in res["content"][0]["text"]


def test_refire_reupload_uses_modern_uploads_endpoint(monkeypatch):
    monkeypatch.setattr(server, "_http_get_bytes", lambda rid, url: b"PDFBYTES")
    raw = {}

    def fake_raw(request_id, url, *, method="POST", raw_body=b"", content_type=None):
        raw["url"] = url
        return {"url": f"{BASE}/api/v1/tasks/42"}

    monkeypatch.setattr(server, "_http_request_raw", fake_raw)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    state = {"new_status": "importing"}

    def responder(url, method, body):
        if "/tasks/42" in url:
            return {"status": "succeeded", "content": {"upload": f"{BASE}/api/v1/uploads/9"}}
        if url.endswith("/annotations/100"):
            return {"document": f"{BASE}/api/v1/documents/7",
                    "queue": f"{BASE}/api/v1/queues/5"}
        if "/documents/7" in url and "/content" not in url:
            return {"content": f"{BASE}/api/v1/documents/7/content",
                    "original_file_name": "src.pdf"}
        if url.endswith("/uploads/9"):
            return {"annotations": [f"{BASE}/api/v1/annotations/200"]}
        if url.endswith("/annotations/200"):
            s = state["new_status"]
            state["new_status"] = "to_review"  # next GET reports settled
            return {"id": 200, "status": s,
                    "document": f"{BASE}/api/v1/documents/7",
                    "queue": f"{BASE}/api/v1/queues/5",
                    "automation_blocker": None}
        if "/annotations/200/content" in url:
            return {"results": []}
        if "/hooks/logs" in url:
            return {"results": []}
        return None

    fake, emitted = run_handler(
        monkeypatch, "rossum_refire_annotation",
        {"annotation_id": 100, "mode": "reupload"}, responder,
    )
    out = emitted_payload(emitted)
    # routed through the modern endpoint, NOT /queues/5/upload
    assert "/api/v1/uploads?queue=5" in raw["url"]
    assert "/queues/5/upload" not in raw["url"]
    assert out["_refire"]["target_annotation_id"] == 200


def test_get_task_returns_task_object(monkeypatch):
    fake, emitted = run_handler(
        monkeypatch, "rossum_get_task", {"task_id": 5},
        lambda url, method, body: {"id": 5, "status": "succeeded",
                                   "result_url": f"{BASE}/api/v1/uploads/9"})
    out = emitted_payload(emitted)
    assert out["id"] == 5 and out["status"] == "succeeded"
    # must use the documented ?no_redirect=true so the task's own status is returned
    assert fake.calls[0]["url"].endswith("/tasks/5?no_redirect=true")


def test_delete_annotation_soft_delete_batch(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_http_request_silent",
                        lambda url, method="GET": calls.append((url, method)) or 204)
    fake, emitted = run_handler(monkeypatch, "rossum_delete_annotation",
                                {"annotation_ids": [11, 22]},
                                lambda url, method, body: None)
    out = emitted_payload(emitted)
    assert out["soft_deleted"] == [11, 22]
    assert out["errors"] == []
    assert all(m == "POST" and u.endswith("/delete") for u, m in calls)
    assert "purged" not in out  # purge not requested


def test_delete_annotation_purge_polls_until_purged(monkeypatch):
    monkeypatch.setattr(server, "_http_request_silent", lambda url, method="GET": 204)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    state = {11: ["deleted", "purged"]}

    def responder(url, method, body):
        if url.endswith("/annotations/purge_deleted"):
            return {}  # 202-ish
        if url.endswith("/annotations/11"):
            return {"id": 11, "status": state[11].pop(0) if len(state[11]) > 1 else state[11][0]}
        return None

    fake, emitted = run_handler(monkeypatch, "rossum_delete_annotation",
                                {"annotation_ids": [11], "purge": True}, responder)
    out = emitted_payload(emitted)
    assert out["purged"] == [11]
    assert out["not_purged_in_time"] == []


def test_delete_annotation_purge_mid_poll_404_treated_as_purged(monkeypatch):
    # _http_request_silent: 204 for the soft-delete POST (/delete), 404 for the poll GET
    def silent(url, method="GET"):
        if url.endswith("/delete"):
            return 204  # soft-delete POST succeeds
        return 404      # poll GET: annotation already gone

    monkeypatch.setattr(server, "_http_request_silent", silent)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)

    def responder(url, method, body):
        if url.endswith("/annotations/purge_deleted"):
            return {}  # 202-ish trigger
        return None  # poll GET should never reach _http_request

    fake, emitted = run_handler(monkeypatch, "rossum_delete_annotation",
                                {"annotation_ids": [11], "purge": True}, responder)
    out = emitted_payload(emitted)
    assert out["purged"] == [11], "mid-poll 404 must be counted as purged, not an error"
    assert out["not_purged_in_time"] == []
    # Confirm no error was surfaced
    assert not emitted[-1]["result"].get("isError")


def test_delete_annotation_records_per_id_errors(monkeypatch):
    monkeypatch.setattr(server, "_http_request_silent",
                        lambda url, method="GET": 404 if "/99/" in url else 204)
    fake, emitted = run_handler(monkeypatch, "rossum_delete_annotation",
                                {"annotation_ids": [11, 99]},
                                lambda url, method, body: None)
    out = emitted_payload(emitted)
    assert out["soft_deleted"] == [11]
    assert out["errors"] and out["errors"][0]["id"] == 99


# --- hook write tools (create-from-template / duplicate / invoke) ---

def test_create_hook_from_template_builds_body(monkeypatch):
    created = {"id": 50, "name": "From Tmpl", "type": "function"}
    fake, emitted = run_handler(
        monkeypatch, "rossum_create_hook_from_template",
        {"hook_template": 998877, "name": "From Tmpl", "queue_ids": [7], "token_owner": 3},
        lambda url, method, body: created
        if method == "POST" and url.endswith("/api/v1/hooks/create") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/hooks/create")
    assert call["body"]["hook_template"] == f"{BASE}/api/v1/hook_templates/998877"
    assert call["body"]["name"] == "From Tmpl"
    assert call["body"]["queues"] == [f"{BASE}/api/v1/queues/7"]
    assert call["body"]["token_owner"] == f"{BASE}/api/v1/users/3"
    assert emitted_payload(emitted) == created
    # optional pass-through fields not supplied must be absent
    for absent in ("events", "active", "settings", "config"):
        assert absent not in call["body"]


def test_duplicate_hook_builds_body(monkeypatch):
    dup = {"id": 99, "name": "Clone", "active": False}
    # Exercise all three real copy_* flags reaching the body (the spec accepts
    # name + copy_secrets + copy_dependencies + copy_queues).
    fake, emitted = run_handler(
        monkeypatch, "rossum_duplicate_hook",
        {"hook_id": 42, "name": "Clone", "copy_secrets": True,
         "copy_dependencies": True, "copy_queues": True},
        lambda url, method, body: dup
        if method == "POST" and url.endswith("/api/v1/hooks/42/duplicate") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/hooks/42/duplicate")
    assert call["body"] == {
        "name": "Clone", "copy_secrets": True,
        "copy_dependencies": True, "copy_queues": True,
    }
    assert emitted_payload(emitted) == dup


def test_duplicate_hook_omits_unset_flags(monkeypatch):
    # With only name supplied, the optional copy_* flags must be absent (not
    # defaulted in our handler) so the API applies its own defaults.
    fake, _ = run_handler(
        monkeypatch, "rossum_duplicate_hook", {"hook_id": 42, "name": "Clone"},
        lambda url, method, body: {"id": 99, "name": "Clone", "active": False},
    )
    assert fake.calls[0]["body"] == {"name": "Clone"}


def test_invoke_hook_posts_payload(monkeypatch):
    resp = {"results": [{"id": "req-1", "operations": []}]}
    fake, emitted = run_handler(
        monkeypatch, "rossum_invoke_hook",
        {"hook_id": 42, "payload": {"SAP_ID": "1234"}},
        lambda url, method, body: resp
        if method == "POST" and url.endswith("/api/v1/hooks/42/invoke") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/hooks/42/invoke")
    assert call["body"] == {"SAP_ID": "1234"}
    assert emitted_payload(emitted) == resp


def test_invoke_hook_defaults_empty_payload(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_invoke_hook", {"hook_id": 42},
        lambda url, method, body: {"ok": True},
    )
    assert fake.calls[0]["body"] == {}


def test_create_hook_from_template_always_sends_queues(monkeypatch):
    """Regression: POST /api/v1/hooks/create requires 'queues' field even when
    queue_ids is omitted. Empty list [] means unattached — still accepted by the
    API. Previously the key was absent, causing HTTP 400 from the real API."""
    created = {"id": 51, "name": "X", "type": "function"}
    fake, emitted = run_handler(
        monkeypatch, "rossum_create_hook_from_template",
        {"hook_template": 998877, "name": "X"},   # NO queue_ids supplied
        lambda url, method, body: created
        if method == "POST" and url.endswith("/api/v1/hooks/create") else None,
    )
    call = fake.calls[0]
    assert "queues" in call["body"], (
        "queues key must always be present in the request body — "
        "the API returns HTTP 400 if it is missing"
    )
    assert call["body"]["queues"] == [], (
        f"expected empty list for unattached hook, got {call['body']['queues']!r}"
    )


def test_create_hook_from_template_passes_through_optional_fields(monkeypatch):
    # When events/active/settings/config ARE supplied, they must reach the body
    # unmodified (objects/booleans untouched, lists not URL-ified like queues).
    events = ["annotation_content.initialize", "annotation_content.export"]
    settings = {"threshold": 0.9, "nested": {"a": [1, 2]}}
    config = {"runtime": "python3.12", "timeout_s": 20}
    fake, _ = run_handler(
        monkeypatch, "rossum_create_hook_from_template",
        {"hook_template": 998877, "name": "X", "events": events,
         "active": False, "settings": settings, "config": config},
        lambda url, method, body: {"id": 52, "name": "X"}
        if method == "POST" and url.endswith("/api/v1/hooks/create") else None,
    )
    body = fake.calls[0]["body"]
    assert body["events"] == events
    assert body["active"] is False
    assert body["settings"] == settings
    assert body["config"] == config


# --- email template tools: body shaping, render-as-read, delete, annotations ---

def test_create_email_template_shapes_request_body(monkeypatch):
    created = {"id": 9, "name": "Rejection"}
    fake, _ = run_handler(
        monkeypatch, "rossum_create_email_template",
        {"name": "Rejection", "queue_id": 7, "type": "rejection",
         "subject": "Re: your invoice", "message": "<p>Rejected</p>",
         "to": [{"email": "a@b.com", "name": "A"}], "automate": False},
        lambda url, method, body: created if url.endswith("/api/v1/email_templates") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/email_templates")
    assert call["body"] == {
        "name": "Rejection",
        "queue": f"{BASE}/api/v1/queues/7",
        "type": "rejection",
        "subject": "Re: your invoice",
        "message": "<p>Rejected</p>",
        "to": [{"email": "a@b.com", "name": "A"}],
        "automate": False,
    }


def test_create_email_template_omits_unset_optionals(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_create_email_template",
        {"name": "Minimal", "queue_id": 7},
        lambda url, method, body: {"id": 1} if url.endswith("/api/v1/email_templates") else None,
    )
    body = fake.calls[0]["body"]
    assert body == {"name": "Minimal", "queue": f"{BASE}/api/v1/queues/7"}
    for absent in ("type", "subject", "message", "to", "cc", "bcc", "automate", "triggers"):
        assert absent not in body


def test_create_email_template_maps_trigger_ids_to_urls(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_create_email_template",
        {"name": "T", "queue_id": 7, "triggers": [5, 6]},
        lambda url, method, body: {"id": 1},
    )
    assert fake.calls[0]["body"]["triggers"] == [
        f"{BASE}/api/v1/triggers/5", f"{BASE}/api/v1/triggers/6",
    ]


def test_patch_email_template_shapes_request_body(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_patch_email_template",
        {"email_template_id": 9, "subject": "New subject", "automate": True},
        lambda url, method, body: {"id": 9} if url.endswith("/api/v1/email_templates/9") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/api/v1/email_templates/9")
    assert call["body"] == {"subject": "New subject", "automate": True}


def test_patch_email_template_remaps_queue_id(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_patch_email_template",
        {"email_template_id": 9, "queue_id": 12},
        lambda url, method, body: {"id": 9},
    )
    assert fake.calls[0]["body"] == {"queue": f"{BASE}/api/v1/queues/12"}


def test_patch_email_template_maps_trigger_ids_to_urls(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_patch_email_template",
        {"email_template_id": 9, "triggers": [5, 6]},
        lambda url, method, body: {"id": 9},
    )
    assert fake.calls[0]["body"] == {
        "triggers": [f"{BASE}/api/v1/triggers/5", f"{BASE}/api/v1/triggers/6"],
    }


def test_delete_email_template_calls_delete(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_delete_email_template",
        {"email_template_id": 9},
        lambda url, method, body: 204 if url.endswith("/api/v1/email_templates/9") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/api/v1/email_templates/9")


def test_render_email_template_is_post_with_resolved_urls(monkeypatch):
    rendered = {"subject": "Hi", "message": "<p>body</p>", "to": [{"email": "x@y.com"}]}
    fake, _ = run_handler(
        monkeypatch, "rossum_render_email_template",
        {"email_template_id": 9,
         "annotation_list": [55],
         "document_list": [3],
         "parent_email": f"{BASE}/api/v1/emails/3",
         "template_values": {"foo": "bar"}},
        lambda url, method, body: rendered if url.endswith("/api/v1/email_templates/9/render") else None,
    )
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/email_templates/9/render")
    assert call["body"] == {
        "template_values": {"foo": "bar"},
        "parent_email": f"{BASE}/api/v1/emails/3",
        "annotation_list": [f"{BASE}/api/v1/annotations/55"],
        "document_list": [f"{BASE}/api/v1/documents/3"],
    }


def test_render_email_template_empty_body_when_no_args(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_render_email_template",
        {"email_template_id": 9},
        lambda url, method, body: {"subject": "", "message": ""},
    )
    assert fake.calls[0]["body"] == {}


def test_render_email_template_is_read_only_annotation():
    assert server.TOOLS["rossum_render_email_template"]["annotations"]["readOnlyHint"] is True


def test_email_template_write_destructive_annotations():
    assert server.TOOLS["rossum_create_email_template"]["annotations"]["readOnlyHint"] is False
    assert server.TOOLS["rossum_create_email_template"]["annotations"]["destructiveHint"] is False
    assert server.TOOLS["rossum_patch_email_template"]["annotations"]["readOnlyHint"] is False
    assert server.TOOLS["rossum_delete_email_template"]["annotations"]["destructiveHint"] is True


# --- queue lifecycle tools (from_template / duplicate / patch / delete-cascade) ---

def test_create_queue_from_template_builds_body(monkeypatch):
    fake, emitted = run_handler(
        monkeypatch, "rossum_create_queue_from_template",
        {"name": "Test Q", "template_name": "EU Demo Template",
         "workspace_id": 3, "engine_id": 8, "legacy": True},
        lambda url, method, body: {"id": 7},
    )
    call = fake.calls[0]
    assert call["url"].endswith("/api/v1/queues/from_template?legacy=true")
    assert call["method"] == "POST"
    assert call["body"] == {
        "name": "Test Q",
        "template_name": "EU Demo Template",
        "workspace": f"{BASE}/api/v1/workspaces/3",
        "include_documents": False,  # required by the live API, defaulted off
        "engine": f"{BASE}/api/v1/engines/8",
    }


def test_create_queue_from_template_minimal(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_create_queue_from_template",
        {"name": "Q", "template_name": "EU Demo Template", "workspace_id": 3},
        lambda url, method, body: {"id": 7},
    )
    call = fake.calls[0]
    assert call["url"].endswith("/api/v1/queues/from_template")  # no ?legacy
    assert "engine" not in call["body"]
    assert call["body"]["include_documents"] is False


def test_duplicate_queue_builds_body(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_duplicate_queue",
        {"queue_id": 7, "name": "Copy", "copy_permissions": False},
        lambda url, method, body: {"id": 8},
    )
    call = fake.calls[0]
    assert call["url"].endswith("/api/v1/queues/7/duplicate")
    assert call["method"] == "POST"
    # only the explicitly-passed flag is sent; the rest keep API defaults (true)
    assert call["body"] == {"name": "Copy", "copy_permissions": False}


def test_patch_queue_maps_ids_to_urls(monkeypatch):
    fake, _ = run_handler(
        monkeypatch, "rossum_patch_queue",
        {"queue_id": 7, "name": "Renamed", "automation_level": "confident",
         "schema_id": 4, "hook_ids": [1, 2], "engine_id": None},
        lambda url, method, body: {"id": 7},
    )
    call = fake.calls[0]
    assert call["url"].endswith("/api/v1/queues/7")
    assert call["method"] == "PATCH"
    assert call["body"] == {
        "name": "Renamed",
        "automation_level": "confident",
        "schema": f"{BASE}/api/v1/schemas/4",
        "hooks": [f"{BASE}/api/v1/hooks/1", f"{BASE}/api/v1/hooks/2"],
        "engine": None,  # explicit null detaches back to the generic engine
    }


_QUEUE_TO_DELETE = {
    "id": 7,
    "schema": f"{BASE}/api/v1/schemas/4",
    "inbox": f"{BASE}/api/v1/inboxes/5",
    "engine": f"{BASE}/api/v1/engines/6",
}


def _delete_queue_responder(url, method, body):
    if method == "GET" and url.endswith("/api/v1/queues/7"):
        return _QUEUE_TO_DELETE
    if method == "DELETE" and "delete_after=0" in url:
        return 202  # parse_json=False path returns the status code
    return None


def test_delete_queue_cascades_owned_deps(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    silent_calls = []

    def silent(url, method="GET"):
        silent_calls.append((url, method))
        if method == "GET":
            return 404  # poll: queue already gone
        return 204      # schema/engine DELETE

    def status(url, method="GET", body=None):
        if "/api/v1/schemas/4" in url:
            return 200, {"id": 4, "queues": []}       # orphaned -> delete
        if "/api/v1/inboxes/5" in url:
            return 404, None                          # auto-removed with the queue
        if "queues?engine=6" in url:
            return 200, {"pagination": {"total": 0}}  # no other queue uses it
        return None, "unexpected"

    monkeypatch.setattr(server, "_http_request_silent", silent)
    monkeypatch.setattr(server, "_http_request_status", status)
    fake, emitted = run_handler(monkeypatch, "rossum_delete_queue",
                                {"queue_id": 7}, _delete_queue_responder)
    assert "delete_after=0" in fake.calls[1]["url"]
    out = emitted_payload(emitted)
    assert out["queue_deleted"] is True
    assert out["schema"] == {"id": 4, "result": "deleted"}
    assert out["inbox"] == {"id": 5, "result": "already_gone"}
    assert out["engine"] == {"id": 6, "result": "deleted"}
    deletes = [u for u, m in silent_calls if m == "DELETE"]
    assert any("/api/v1/schemas/4" in u for u in deletes)
    assert any("/api/v1/engines/6" in u for u in deletes)


def test_delete_queue_skips_shared_deps(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)

    def silent(url, method="GET"):
        assert method == "GET", f"must not DELETE a shared dependency: {url}"
        return 404  # poll: queue gone

    def status(url, method="GET", body=None):
        assert method == "GET"
        if "/api/v1/schemas/4" in url:
            return 200, {"id": 4, "queues": [f"{BASE}/api/v1/queues/9"]}
        if "/api/v1/inboxes/5" in url:
            return 404, None
        if "queues?engine=6" in url:
            return 200, {"pagination": {"total": 1}}
        return None, "unexpected"

    monkeypatch.setattr(server, "_http_request_silent", silent)
    monkeypatch.setattr(server, "_http_request_status", status)
    _, emitted = run_handler(monkeypatch, "rossum_delete_queue",
                             {"queue_id": 7}, _delete_queue_responder)
    out = emitted_payload(emitted)
    assert out["schema"] == {"id": 4, "result": "skipped_shared", "queues": [9]}
    assert out["engine"] == {"id": 6, "result": "skipped_shared",
                             "queues_still_using_it": 1}


def test_delete_queue_no_cascade_on_poll_timeout(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    monkeypatch.setattr(server, "_http_request_silent", lambda url, method="GET": 200)
    monkeypatch.setattr(server, "_http_request_status",
                        lambda url, method="GET", body=None: (_ for _ in ()).throw(
                            AssertionError("cascade must not run on timeout")))
    _, emitted = run_handler(monkeypatch, "rossum_delete_queue",
                             {"queue_id": 7, "poll_timeout": 0}, _delete_queue_responder)
    out = emitted_payload(emitted)
    assert out["queue_deleted"] is False
    assert "schema" not in out and "inbox" not in out and "engine" not in out
    assert "cascade was NOT attempted" in out["note"]


def test_delete_queue_cascade_false_skips_deps(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    monkeypatch.setattr(server, "_http_request_silent", lambda url, method="GET": 404)
    monkeypatch.setattr(server, "_http_request_status",
                        lambda url, method="GET", body=None: (_ for _ in ()).throw(
                            AssertionError("cascade=false must not touch dependencies")))
    _, emitted = run_handler(monkeypatch, "rossum_delete_queue",
                             {"queue_id": 7, "cascade": False}, _delete_queue_responder)
    out = emitted_payload(emitted)
    assert out["queue_deleted"] is True
    assert "schema" not in out and "inbox" not in out and "engine" not in out


def test_queue_lifecycle_annotations():
    assert server.TOOLS["rossum_create_queue_from_template"]["annotations"]["readOnlyHint"] is False
    assert server.TOOLS["rossum_create_queue_from_template"]["annotations"]["destructiveHint"] is False
    assert server.TOOLS["rossum_duplicate_queue"]["annotations"]["destructiveHint"] is False
    assert server.TOOLS["rossum_patch_queue"]["annotations"]["destructiveHint"] is False
    assert server.TOOLS["rossum_delete_queue"]["annotations"]["destructiveHint"] is True
