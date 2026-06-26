"""Unit tests for the API inventory + coverage tooling (issue #46). Stdlib only, no network."""
from __future__ import annotations

import json
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


def test_committed_coverage_doc_matches_regeneration():
    """The autoloaded api-coverage.md must equal a regeneration from the committed
    snapshot + coverage map. Guards the drift class where coverage-map.json is edited
    but the generated doc isn't refreshed (the doc is injected into every session)."""
    data = ROOT / "data"
    doc = ROOT / "plugins/rossum-sa/skills/rossum-reference/api-coverage.md"
    inventory = json.loads((data / "api-inventory.json").read_text(encoding="utf-8"))
    cmap = json.loads((data / "coverage-map.json").read_text(encoding="utf-8"))
    expected = render_doc.render_coverage_doc(inventory, cmap)
    assert doc.read_text(encoding="utf-8") == expected, (
        "api-coverage.md is stale vs data/coverage-map.json. Regenerate it from the "
        "committed snapshot:\n"
        "  python -c \"import json,sys; sys.path.insert(0,'scripts'); "
        "from api_inventory import render_doc; "
        "open('plugins/rossum-sa/skills/rossum-reference/api-coverage.md','w').write("
        "render_doc.render_coverage_doc(json.load(open('data/api-inventory.json')), "
        "json.load(open('data/coverage-map.json'))))\""
    )


# --- scanner: URL-builder delegation (regression for helper-delegated tools) ---

def test_extract_tool_endpoints_matches_url_builder_delegation():
    """A handler that builds the URL into a `url` var and delegates the request to a
    URL-builder helper (_paginate_search) — where the HTTP method lives in the helper,
    not the handler — must still be attributed to the tool + endpoint. Without this the
    scanner under-reports coverage (e.g. rossum_search_annotations_advanced)."""
    src = '''
@_tool("rossum_search_annotations_advanced", "d", {"type": "object"}, annotations=_READ_ONLY)
def handle_search_annotations_advanced(request_id, arguments):
    url = f"{base_url}/api/v1/annotations/search?{urlencode(params)}"
    result = _paginate_search(request_id, url, body, max_results=max_results)
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("POST", "/annotations/search") in te
    assert "rossum_search_annotations_advanced" in te[("POST", "/annotations/search")]


# --- forever-guards: committed coverage-map must stay in sync with reality ---

_SERVER_PY = ROOT / "plugins/rossum-sa/mcp-servers/rossum-api/server.py"


def _committed():
    inv = json.loads((ROOT / "data/api-inventory.json").read_text(encoding="utf-8"))
    cmap = json.loads((ROOT / "data/coverage-map.json").read_text(encoding="utf-8"))
    return inv, cmap


def test_committed_covered_matches_server():
    """The `covered` section of coverage-map.json must EXACTLY equal what the server
    actually wraps (same endpoints, same tools). This is the root-cause guard: you
    cannot add, rename, or remove a tool->endpoint without updating the map to match —
    CI fails otherwise. Relies on extract_tool_endpoints seeing every wrapping pattern
    (direct helpers, _http_request, and URL-builder delegation)."""
    inv, cmap = _committed()
    seeded = coverage.seed_coverage_map(inv, coverage.extract_tool_endpoints(
        _SERVER_PY.read_text(encoding="utf-8")))
    committed = {k: v for k, v in cmap.items() if v.get("decision") == "covered"}

    missing = sorted(set(seeded) - set(committed))   # server wraps it, map doesn't say covered
    extra = sorted(set(committed) - set(seeded))      # map claims covered, scanner can't see it
    assert not missing and not extra, (
        "coverage-map.json 'covered' set is out of sync with the server.\n"
        f"  server wraps but map missing/!=covered: {missing}\n"
        f"  map claims covered but server scan can't see it: {extra}\n"
        "Fix: update data/coverage-map.json (and regenerate api-coverage.md). If a new "
        "tool builds its URL and delegates to a helper, add that helper to "
        "_URL_BUILDER_HELPERS in scripts/api_inventory/coverage.py so the scanner sees it."
    )
    mismatched = {k: {"server": sorted(seeded[k]["tools"]),
                      "map": sorted(committed[k]["tools"])}
                  for k in seeded if sorted(seeded[k]["tools"]) != sorted(committed[k]["tools"])}
    assert not mismatched, f"covered tool lists differ from the server: {mismatched}"


def test_no_stale_coverage_entries():
    """Every key in coverage-map.json must correspond to an endpoint that still exists
    in the committed API inventory. Catches decisions left behind after an endpoint is
    removed or renamed."""
    inv, cmap = _committed()
    stale = coverage.stale_coverage_entries(inv, cmap)
    assert not stale, f"coverage-map.json has entries for endpoints not in the inventory: {stale}"


def test_extract_tool_endpoints_matches_fixed_endpoint_helper():
    """A handler that calls a _FIXED_ENDPOINT_HELPERS entry (e.g. _upload_to_queue)
    with no direct _http_request_raw / url-building in the block must still be
    attributed to the fixed endpoint registered for that helper."""
    src = '''
@_tool("rossum_upload_document", "d", {"type": "object"})
def handle_upload_document(request_id, arguments):
    _upload_to_queue(request_id, queue_id, file_content, filename)
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("POST", "/uploads") in te
    assert "rossum_upload_document" in te[("POST", "/uploads")]


def test_extract_tool_endpoints_handles_trailing_query_string():
    # rossum_get_task GETs /tasks/{id}?no_redirect=true via the plain _http_request helper;
    # the trailing query string must not defeat path extraction.
    src = '''
@_tool("rossum_get_task", "d", {"type": "object"})
def handle_get_task(request_id, arguments):
    _http_request(request_id, f"{base_url}/api/v1/tasks/{tid}?no_redirect=true")
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("GET", "/tasks/{}") in te
    assert "rossum_get_task" in te[("GET", "/tasks/{}")]


def test_extract_tool_endpoints_matches_resource_url_builder():
    """A handler that wraps its URL arg in the forward builder _resource_url(...) instead
    of an inline f-string must still be attributed to the tool + endpoint. The method
    defaults to GET, and a method="X" kwarg living *past* the builder's nested closing
    paren must still be read (balanced-arg capture, not non-greedy)."""
    src = '''
@_tool("rossum_get_thing", "d", {"type": "object"})
def handle_get_thing(request_id, arguments):
    thing = _http_request(request_id, _resource_url(base_url, "hooks", hid))
    patched = _http_request(
        request_id, _resource_url(base_url, "annotations", aid),
        method="PATCH", body={"status": "to_review"},
    )
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("GET", "/hooks/{}") in te
    assert "rossum_get_thing" in te[("GET", "/hooks/{}")]
    assert ("PATCH", "/annotations/{}") in te
    assert "rossum_get_thing" in te[("PATCH", "/annotations/{}")]


def test_extract_tool_endpoints_url_builder_scopes_to_passed_var():
    """Regression (PR #73 review): the URL-builder branch must attribute ONLY the URL
    variable actually passed to the helper, not every f-string assignment in the block.
    A handler that delegates to _paginate_search AND builds an unrelated URL must not
    have that unrelated path mis-attributed (which the strict guard would then demand
    as a spurious covered entry)."""
    src = '''
@_tool("rossum_search_x", "d", {"type": "object"}, annotations=_READ_ONLY)
def handle_x(request_id, arguments):
    other_url = f"{base_url}/api/v1/queues/{qid}"
    url = f"{base_url}/api/v1/annotations/search?{urlencode(params)}"
    result = _paginate_search(request_id, url, body, max_results=max_results)
'''
    te = coverage.extract_tool_endpoints(src)
    assert ("POST", "/annotations/search") in te
    assert ("POST", "/queues/{}") not in te          # unrelated URL must NOT be attributed
    assert ("GET", "/queues/{}") not in te
