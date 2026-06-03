"""Shared helpers for walking Rossum annotation content trees."""


def walk(nodes, path="", row=None):
    """Flatten a content tree to a list of datapoints.

    Each datapoint dict carries: datapoint_id, schema_id, path, row,
    value, page, position, validation_sources.
    """
    out = []
    _walk(nodes or [], path, row, out, datapoints_only=True)
    return out


def walk_tree(nodes, path="", row=None):
    """Flatten a content tree including container nodes (multivalue, tuple, section).

    Useful when callers need the multivalue/tuple IDs (e.g. for add/remove ops).
    """
    out = []
    _walk(nodes or [], path, row, out, datapoints_only=False)
    return out


def _walk(nodes, path, row, out, datapoints_only):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        sid = node.get("schema_id")
        cat = node.get("category")
        npath = f"{path}.{sid}" if path and sid else (sid or path)

        if cat == "datapoint":
            content = node.get("content") or {}
            out.append({
                "datapoint_id": node.get("id"),
                "schema_id": sid,
                "path": npath,
                "row": row,
                "value": content.get("value"),
                "page": content.get("page"),
                "position": content.get("position"),
                "validation_sources": node.get("validation_sources") or [],
                "category": "datapoint",
            })
            continue

        if not datapoints_only:
            out.append({
                "id": node.get("id"),
                "schema_id": sid,
                "path": npath,
                "row": row,
                "category": cat,
            })

        if cat == "multivalue":
            for i, child in enumerate(node.get("children") or []):
                _walk([child], npath, i, out, datapoints_only)
        elif cat == "tuple":
            for sub in node.get("children") or []:
                _walk([sub], npath, row, out, datapoints_only)
        else:
            for sub in node.get("children") or []:
                _walk([sub], npath, row, out, datapoints_only)


def index_by_path_row(flat):
    """Return {(path, row): item or [items if ambiguous]}."""
    idx = {}
    for f in flat:
        key = (f["path"], f.get("row"))
        if key in idx:
            existing = idx[key]
            if not isinstance(existing, list):
                idx[key] = [existing]
            idx[key].append(f)
        else:
            idx[key] = f
    return idx


def parse_blocker(blocker):
    """Flatten an automation_blocker payload into a list of items."""
    items = []
    if not blocker:
        return items
    for entry in blocker.get("content") or []:
        sid = entry.get("schema_id")
        typ = entry.get("type")
        for sample in entry.get("samples") or []:
            details = sample.get("details") or {}
            messages = details.get("message_content") or details.get("content") or [None]
            source = ""
            if details.get("detail"):
                d0 = details["detail"][0]
                source = d0.get("hook_name") or d0.get("rule_name") or ""
            for msg in messages:
                items.append({
                    "schema_id": sid,
                    "type": typ,
                    "message": msg,
                    "source": source,
                    "datapoint_id": sample.get("datapoint_id"),
                })
    return items


def load_content_nodes(content_path):
    """Load a content.json file and return the raw node list."""
    import json
    obj = json.load(open(content_path))
    if isinstance(obj, list):
        return obj
    return obj.get("content") or obj.get("results") or [obj]
