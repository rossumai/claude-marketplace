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
_HTTP_HELPERS = ("_http_request", "_http_request_silent", "_http_raw", "_http_get_bytes")


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


def summarize(inventory: list[dict], coverage_map: dict) -> dict:
    """Counts per curated decision, plus implicit `pending` (inventory minus classified)."""
    out = dict(Counter(v["decision"] for v in coverage_map.values()))
    out["pending"] = len(inventory) - len(coverage_map)
    return out


def pending_operations(inventory: list[dict], coverage_map: dict) -> list[dict]:
    """Operations with no curated decision (absent, or explicitly `pending`)."""
    out = []
    for op in inventory:
        entry = coverage_map.get(f"{op['method']} {_display_path(op['path'])}")
        if entry is None or entry.get("decision") == "pending":
            out.append(op)
    return out
