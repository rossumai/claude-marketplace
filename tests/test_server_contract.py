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
