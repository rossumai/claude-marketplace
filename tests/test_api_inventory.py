"""Unit tests for the API inventory + coverage tooling (issue #46). Stdlib only, no network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from api_inventory import build, coverage, diff, render_doc  # noqa: E402

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


def test_seed_coverage_map_is_covered_only():
    inv = build.extract_inventory(SPEC)
    cmap = coverage.seed_coverage_map(inv, coverage.extract_tool_endpoints(SERVER_SRC))
    assert cmap["GET /queues"]["decision"] == "covered"
    assert "POST /annotations/{annotationID}/cancel" in cmap  # real param name preserved
    assert "GET /queues/{id}" not in cmap   # pending = simply absent from the map
    assert "GET /engines" not in cmap
    summ = coverage.summarize(inv, cmap)
    assert summ == {"covered": 2, "pending": 2}


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
    assert "2 covered · 2 pending" in md
    assert "## Queue" in md and "## Engine" in md
    assert "`GET /queues`" in md and "covered" in md
    assert "`GET /queues/{id}`" in md and "pending" in md
