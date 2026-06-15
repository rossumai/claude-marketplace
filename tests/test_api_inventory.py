"""Unit tests for the API inventory + coverage tooling (issue #46). Stdlib only, no network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from api_inventory import build, coverage, diff, render_doc, render_issue  # noqa: E402

SPEC = {
    "paths": {
        "/api/v1/queues": {"get": {"operationId": "queues_list", "summary": "List queues",
                                    "tags": ["Queue"]}},
        "/api/v1/queues/{id}": {"get": {"operationId": "queues_get", "summary": "Get queue",
                                         "tags": ["Queue"]}},
        "/api/v1/annotations/{annotationID}/cancel": {
            "post": {"operationId": "ann_cancel", "summary": "Cancel", "tags": ["Annotation"]}},
        "/api/v1/engines": {"get": {"operationId": "engines_list", "summary": "List engines",
                                     "tags": ["Engine"]}},
    }
}

# Mimics the two helper-call styles in the real server: _rossum_list and the
# multi-line _http_request_silent (the variant the first cut of the matcher missed).
SERVER_SRC = '''
@_tool(
    "rossum_list_queues",
    "desc", {"type": "object"}, annotations=_READ_ONLY,
)
def handle_list_queues(request_id, arguments):
    _rossum_list(request_id, "/api/v1/queues", params, pick_fields=_QUEUE_FIELDS)

@_tool(
    "rossum_cancel_annotation",
    "desc", {"type": "object"},
)
def handle_cancel_annotation(request_id, arguments):
    status_code = _http_request_silent(
        f"{base_url}/api/v1/annotations/{annotation_id}/cancel", method="POST",
    )
'''


def test_extract_inventory_shape_and_tags():
    inv = build.extract_inventory(SPEC)
    assert len(inv) == 4
    q = next(o for o in inv if o["path"] == "/api/v1/queues")
    assert q["method"] == "GET" and q["tag"] == "Queue"
    assert [(o["path"], o["method"]) for o in inv] == sorted(
        (o["path"], o["method"]) for o in inv)


def test_normalize_path():
    assert coverage.normalize_path("/api/v1/queues/{arguments['id']}") == "/queues/{}"
    assert coverage.normalize_path("/v1/queues/{id}") == "/queues/{}"
    assert coverage.normalize_path("/api/v1/annotations/{id}/cancel?x=1") == "/annotations/{}/cancel"


def test_extract_tool_endpoints_includes_silent_variant():
    te = coverage.extract_tool_endpoints(SERVER_SRC)
    assert "rossum_list_queues" in te[("GET", "/queues")]
    # the multi-line _http_request_silent POST must be caught (regression: cancel was missed)
    assert ("POST", "/annotations/{}/cancel") in te
    assert "rossum_cancel_annotation" in te[("POST", "/annotations/{}/cancel")]


def test_extract_tool_endpoints_matches_request_raw():
    # Regression: the helper list named "_http_raw" (typo) instead of "_http_request_raw",
    # so upload-style tools calling _http_request_raw were never auto-mapped to an endpoint.
    src = '''
@_tool("rossum_upload", "d", {"type": "object"})
def handle_upload(request_id, arguments):
    _http_request_raw(request_id, f"{base_url}/api/v1/uploads", method="POST", raw_body=b"")
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("POST", "/uploads") in te
    assert "rossum_upload" in te[("POST", "/uploads")]


def test_seed_coverage_map_is_covered_only():
    inv = build.extract_inventory(SPEC)
    cmap = coverage.seed_coverage_map(inv, coverage.extract_tool_endpoints(SERVER_SRC))
    assert cmap["GET /queues"]["decision"] == "covered"
    assert "POST /annotations/{annotationID}/cancel" in cmap  # real param name preserved
    assert "GET /queues/{id}" not in cmap   # pending = simply absent from the map
    assert "GET /engines" not in cmap
    summ = coverage.summarize(inv, cmap)
    assert summ == {"covered": 2, "implicit": 2, "pending": 0}


def test_diff_ignores_summary_only_changes():
    old = [{"method": "GET", "path": "/v1/queues", "operationId": "q_list",
            "summary": "List", "tag": "Queue"},
           {"method": "GET", "path": "/v1/old", "operationId": "old", "summary": "x", "tag": "Old"}]
    new = [{"method": "GET", "path": "/v1/queues", "operationId": "q_list",
            "summary": "List queues NOW", "tag": "Queue"},          # summary-only -> NOT changed
           {"method": "POST", "path": "/v1/queues", "operationId": "q_create",
            "summary": "Create", "tag": "Queue"}]                   # added
    d = diff.diff_inventories(old, new)
    assert [(o["method"], o["path"]) for o in d["added"]] == [("POST", "/v1/queues")]
    assert [(o["method"], o["path"]) for o in d["removed"]] == [("GET", "/v1/old")]
    assert d["changed"] == []


def test_diff_detects_operationid_or_tag_change():
    old = [{"method": "GET", "path": "/v1/x", "operationId": "a", "summary": "s", "tag": "T1"}]
    new = [{"method": "GET", "path": "/v1/x", "operationId": "b", "summary": "s", "tag": "T2"}]
    d = diff.diff_inventories(old, new)
    assert len(d["changed"]) == 1
    assert d["changed"][0]["after"] == {"operationId": "b", "tag": "T2"}


def test_render_doc_groups_by_tag_with_status():
    inv = build.extract_inventory(SPEC)
    cmap = coverage.seed_coverage_map(inv, coverage.extract_tool_endpoints(SERVER_SRC))
    md = render_doc.render_coverage_doc(inv, cmap)
    assert "# Rossum API coverage" in md
    assert "2 covered · 2 via rossum_get" in md
    assert "## Queue" in md and "## Engine" in md
    assert "`GET /queues`" in md and "covered" in md
    assert "`GET /queues/{id}`" in md and "via rossum_get" in md
    assert "`GET /engines`" in md and "rossum_get" in md


def test_pending_operations_excludes_classified():
    inv = build.extract_inventory(SPEC)
    cmap = coverage.seed_coverage_map(inv, coverage.extract_tool_endpoints(SERVER_SRC))
    assert coverage.pending_operations(inv, cmap) == []


def test_pending_operations_includes_uncovered_writes():
    inv = [
        {"method": "POST", "path": "/api/v1/queues", "tag": "Queue",
         "operationId": "qc", "summary": "Create"},
        {"method": "GET", "path": "/api/v1/queues", "tag": "Queue",
         "operationId": "ql", "summary": "List"},
    ]
    cmap = {}  # nothing covered
    pending = coverage.pending_operations(inv, cmap)
    assert len(pending) == 1 and pending[0]["method"] == "POST"   # uncovered write -> pending
    assert coverage.implicit_operations(inv, cmap) == [inv[1]]    # uncovered GET -> implicit


def test_stale_coverage_entries():
    inv = build.extract_inventory(SPEC)
    cmap = {"GET /queues": {"decision": "covered", "tools": ["x"]},   # still in SPEC
            "GET /gone/{id}": {"decision": "covered", "tools": ["y"]}}  # not in SPEC
    assert coverage.stale_coverage_entries(inv, cmap) == ["GET /gone/{id}"]


def test_has_content_guard():
    empty = {"added": [], "changed": [], "removed": []}
    op = {"method": "GET", "path": "/x"}
    assert render_issue.has_content(empty, [], []) is False
    assert render_issue.has_content(empty, [op], []) is True                       # pending
    assert render_issue.has_content({"added": [op], "changed": [], "removed": []}, [], []) is True  # diff
    assert render_issue.has_content(empty, [], ["GET /gone"]) is True              # stale


def test_implicit_and_pending_split():
    spec = {"paths": {
        "/api/v1/engines": {"get": {"operationId": "e", "summary": "List", "tags": ["Engine"]}},
        "/api/v1/engines/{id}": {"delete": {"operationId": "ed", "summary": "Del",
                                             "tags": ["Engine"]}},
    }}
    inv = build.extract_inventory(spec)
    cmap = {}  # nothing covered
    pending = {f"{o['method']} {o['path']}" for o in coverage.pending_operations(inv, cmap)}
    implicit = {f"{o['method']} {o['path']}" for o in coverage.implicit_operations(inv, cmap)}
    assert pending == {"DELETE /api/v1/engines/{id}"}   # uncovered write -> pending
    assert implicit == {"GET /api/v1/engines"}          # uncovered read -> generic


def test_render_issue_body_sections_and_digest():
    diff_ = {
        "added": [{"method": "POST", "path": "/v1/engines", "summary": "Create", "tag": "Engine"}],
        "changed": [],
        "removed": [{"method": "DELETE", "path": "/v1/old", "summary": "", "tag": "Old"}],
    }
    pending = [{"method": "GET", "path": "/v1/labels", "summary": "List", "tag": "Label"}]
    stale = ["GET /gone"]
    body = render_issue.render_issue_body(diff_, pending, stale)
    assert "## Changes since the committed snapshot" in body
    assert "added `POST /v1/engines`" in body
    assert "removed `DELETE /v1/old`" in body
    assert "## Stale coverage-map entries (1)" in body and "`GET /gone`" in body
    assert "### Label (1)" in body and "`GET /v1/labels`" in body

    base = render_issue.extract_digest(body)
    empty = {"added": [], "changed": [], "removed": []}
    # digest is sensitive to each of the three sections...
    assert base != render_issue.extract_digest(render_issue.render_issue_body(empty, pending, stale))
    assert base != render_issue.extract_digest(render_issue.render_issue_body(diff_, [], stale))
    assert base != render_issue.extract_digest(render_issue.render_issue_body(diff_, pending, []))
    # ...and deterministic for the same input.
    assert base == render_issue.extract_digest(
        render_issue.render_issue_body(dict(diff_), list(pending), list(stale)))
