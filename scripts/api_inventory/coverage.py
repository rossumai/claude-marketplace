"""Map the MCP server's tools to API endpoints and seed a coverage map.

The mapping is best-effort (regex over the server's helper calls) — it auto-seeds
the obvious `covered` decisions so a human only has to classify the rest. The
coverage map is the human-curated source of truth (issue #46).
"""
from __future__ import annotations

import re
from collections import Counter

# A /api/v1 path made of literal segments or {interpolations}.
_PATH = r"/api/v1/(?:[a-zA-Z_]+|\{[^}]*\})(?:/(?:[a-zA-Z_]+|\{[^}]*\}))*"
_HELPER_METHOD = {
    "_rossum_get": "GET", "_rossum_list": "GET", "_paginate": "GET",
    "_rossum_post": "POST", "_rossum_patch": "PATCH", "_rossum_delete": "DELETE",
}
# Lower-level HTTP helpers: method is parsed from a method="X" kwarg (default GET).
_HTTP_HELPERS = ("_http_request", "_http_request_silent", "_http_request_raw", "_http_get_bytes", "_http_get_no_follow")
# URL-builder helpers: they receive a pre-built `url` argument, so the path is NOT
# in the call args — it lives in a `url = f"...{path}..."` assignment in the handler,
# and the method is fixed by the helper itself (not a method="X" kwarg). For these we
# read the path from the url-building assignment in the same @_tool block.
_URL_BUILDER_HELPERS = {"_paginate_search": "POST"}
# Fixed-endpoint helpers: internal helpers that always call a known endpoint and don't
# expose the URL as a call arg. Map helper_name -> (method, /api/v1/... path).
_FIXED_ENDPOINT_HELPERS: dict[str, tuple[str, str]] = {
    "_upload_to_queue": ("POST", "/api/v1/uploads"),
}


def normalize_path(path: str) -> str:
    """Canonicalize a path for matching: drop /api + version, params -> {}.

    '/api/v1/queues/{arguments['id']}' -> '/queues/{}'
    '/v1/queues/{id}'                  -> '/queues/{}'
    """
    p = path.split("?")[0]
    p = re.sub(r"^/api", "", p)
    p = re.sub(r"^/v\d+", "", p)
    p = re.sub(r"\{[^}]*\}", "{}", p)
    return p.rstrip("/") or "/"


def extract_tool_endpoints(server_src: str) -> dict:
    """Return {(METHOD, normalized_path): {tool_names}} the server's tools reference."""
    cover: dict = {}
    # Split into @_tool blocks so we can attribute paths to tool names.
    blocks = re.split(r'(?=@_tool\(\s*\n?\s*")', server_src)
    for blk in blocks:
        name = re.search(r'@_tool\(\s*\n?\s*"([a-z_]+)"', blk)
        if not name:
            continue
        tool = name.group(1)

        def add(method, path):
            cover.setdefault((method, normalize_path(path)), set()).add(tool)

        helpers = "|".join(_HELPER_METHOD)
        for hm in re.finditer(rf"({helpers})\((.*?)\)", blk, re.S):
            pm = re.search(_PATH, hm.group(2))
            if pm:
                add(_HELPER_METHOD[hm.group(1)], pm.group(0))

        http = "|".join(_HTTP_HELPERS)
        for hm in re.finditer(rf"({http})\((.*?)\)", blk, re.S):
            pm = re.search(_PATH, hm.group(2))
            if pm:
                mm = re.search(r'method\s*=\s*"([A-Z]+)"', hm.group(2))
                add(mm.group(1) if mm else "GET", pm.group(0))

        # URL-builder helpers (e.g. _paginate_search): the path is built into a
        # `<var> = f"...{path}..."` assignment and passed to the helper as the 2nd
        # positional arg (signature: helper(request_id, url, ...)); the HTTP method
        # is fixed by the helper itself. Resolve the path from the assignment to THAT
        # variable only — not any url-building assignment in the block — so an
        # unrelated URL in the same handler isn't mis-attributed. The trailing `\(`
        # also stops a prefix helper (_paginate) from matching `_paginate_search(`.
        builders = "|".join(_URL_BUILDER_HELPERS)
        for bm in re.finditer(rf"({builders})\(([^)]*)\)", blk, re.S):
            method = _URL_BUILDER_HELPERS[bm.group(1)]
            call_args = [a.strip() for a in bm.group(2).split(",")]
            if len(call_args) < 2 or not call_args[1].isidentifier():
                continue
            url_var = call_args[1]
            for am in re.finditer(rf'\b{re.escape(url_var)}\s*=\s*f?["\'][^"\']*?({_PATH})', blk):
                add(method, am.group(1))

        # Fixed-endpoint helpers: internal helpers whose endpoint is always known, so
        # a single call in the @_tool block is enough to register the endpoint.
        fixed = "|".join(re.escape(h) for h in _FIXED_ENDPOINT_HELPERS)
        for fm in re.finditer(rf"\b({fixed})\s*\(", blk):
            method, path = _FIXED_ENDPOINT_HELPERS[fm.group(1)]
            add(method, path)
    return cover


def _display_path(path: str) -> str:
    """Spec path minus /api + version, keeping real param names: '/annotations/{annotationID}'."""
    p = re.sub(r"^/api", "", path)
    p = re.sub(r"^/v\d+", "", p)
    return p or "/"


def seed_coverage_map(inventory: list[dict], tool_endpoints: dict) -> dict:
    """Coverage map of CURATED decisions only — auto-seeds `covered` where a tool matches.

    Anything absent from the map is implicitly `pending` (issue #46). Humans add
    `not_planned` / `deprecated` entries later. Keyed `"METHOD /path"` (real param
    names); `summary` kept for readability.
    """
    cmap = {}
    for op in inventory:
        match = tool_endpoints.get((op["method"], normalize_path(op["path"])))
        if match:
            key = f"{op['method']} {_display_path(op['path'])}"
            cmap[key] = {"decision": "covered", "tools": sorted(match),
                         "summary": op["summary"]}
    return cmap


def pending_operations(inventory: list[dict], coverage_map: dict) -> list[dict]:
    """Operations still needing a dedicated tool: uncovered WRITES, or explicit `pending`.

    GET operations with no dedicated tool are NOT pending — they are implicitly
    covered by the generic `rossum_get` tool (see implicit_operations)."""
    out = []
    for op in inventory:
        entry = coverage_map.get(f"{op['method']} {_display_path(op['path'])}")
        if entry is None:
            if op["method"] != "GET":
                out.append(op)
        elif entry.get("decision") == "pending":
            out.append(op)
    return out


def implicit_operations(inventory: list[dict], coverage_map: dict) -> list[dict]:
    """GET operations with no dedicated tool — reachable via the generic rossum_get."""
    return [op for op in inventory
            if op["method"] == "GET"
            and coverage_map.get(f"{op['method']} {_display_path(op['path'])}") is None]


def summarize(inventory: list[dict], coverage_map: dict) -> dict:
    """Counts per curated decision, plus derived `implicit` (reads via rossum_get)
    and `pending` (uncovered writes)."""
    out = {k: v for k, v in Counter(v["decision"] for v in coverage_map.values()).items()
           if k != "pending"}
    out["implicit"] = len(implicit_operations(inventory, coverage_map))
    out["pending"] = len(pending_operations(inventory, coverage_map))
    return out


def stale_coverage_entries(inventory: list[dict], coverage_map: dict) -> list[str]:
    """Coverage-map keys whose operation no longer exists in the inventory.

    Catches decisions (e.g. `covered`) left behind when an endpoint is removed
    from the API — otherwise they vanish silently from the generated doc.
    """
    live = {f"{op['method']} {_display_path(op['path'])}" for op in inventory}
    return sorted(k for k in coverage_map if k not in live)
