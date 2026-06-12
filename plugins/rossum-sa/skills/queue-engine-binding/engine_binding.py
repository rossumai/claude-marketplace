#!/usr/bin/env python3
"""queue-engine-binding helper: bind Rossum queues to custom extraction engines.

Modes (all default to dry-run; pass --execute to apply):
  convert    generic-engine queue -> new custom engine
  greenfield create a new engine-bound queue from a local schema JSON
  attach     bind an existing queue to an existing engine (reconciles fields)
  revert     detach a queue back to the generic engine

Auth: --base-url plus a token in the ROSSUM_TOKEN environment variable.
"""
import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.request

NON_EXTRACTED_UI_TYPES = {"formula", "data", "manual"}

SCHEMA_TYPE_DEFAULTS = {"string": "string", "number": "number", "date": "date", "enum": "enum"}


# ---------- pure core (unit-tested) ----------

def iter_datapoints(content):
    """Yield (datapoint, inside_multivalue) for every datapoint in schema content."""
    def walk(nodes, tabular):
        for node in nodes:
            category = node.get("category")
            if category == "datapoint":
                yield node, tabular
            elif category == "multivalue":
                child = node.get("children")
                if isinstance(child, dict):
                    yield from walk([child], True)
            elif isinstance(node.get("children"), list):
                yield from walk(node["children"], category == "tuple" or tabular)
    yield from walk(content, False)


def is_engine_extracted(datapoint):
    ui_type = (datapoint.get("ui_configuration") or {}).get("type")
    return ui_type not in NON_EXTRACTED_UI_TYPES


def derive_engine_fields(content, catalog):
    """One engine-field dict per engine-extracted datapoint, seeded from the pretrained catalog."""
    by_name = {entry["name"]: entry for entry in catalog}
    fields = []
    for dp, tabular in iter_datapoints(content):
        if not is_engine_extracted(dp):
            continue
        seed = next((by_name[r] for r in dp.get("rir_field_names") or [] if r in by_name), None)
        fields.append({
            "name": dp["id"],
            "label": dp.get("label", dp["id"]),
            "type": seed["type"] if seed else SCHEMA_TYPE_DEFAULTS.get(dp.get("type", "string"), "string"),
            "subtype": seed["subtype"] if seed else None,
            "pre_trained_field_id": seed["name"] if seed else None,
            "tabular": tabular,
            "multiline": seed["multiline"] if seed else "false",
        })
    return fields


def clean_schema(content):
    """Return (cleaned_content, change_log): empty rir on extracted datapoints, normalize ui,
    strip disable_prediction everywhere; multivalue containers and non-captured ui kept."""
    cleaned = copy.deepcopy(content)
    changes = []
    for dp, _tabular in iter_datapoints(cleaned):
        if dp.pop("disable_prediction", None) is not None:
            changes.append(f"{dp['id']}: removed disable_prediction")
        if is_engine_extracted(dp):
            if dp.get("rir_field_names"):
                changes.append(f"{dp['id']}: rir_field_names {dp['rir_field_names']} -> []")
            dp["rir_field_names"] = []
            if dp.get("ui_configuration") != {"type": "captured", "edit": "enabled"}:
                changes.append(f"{dp['id']}: ui_configuration -> captured/enabled")
            dp["ui_configuration"] = {"type": "captured", "edit": "enabled"}
    return cleaned, changes


def restore_rir(content, engine_fields):
    """Revert helper: map pre_trained_field_id back onto rir_field_names (best effort)."""
    restored = copy.deepcopy(content)
    ptf = {f["name"]: f.get("pre_trained_field_id") for f in engine_fields}
    for dp, _tabular in iter_datapoints(restored):
        if is_engine_extracted(dp) and ptf.get(dp["id"]):
            dp["rir_field_names"] = [ptf[dp["id"]]]
    return restored


# ---------- API layer ----------

def api(base_url, token, method, path, body=None):
    request = urllib.request.Request(
        f"{base_url}{path}", method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                fail(f"non-JSON response from {path} ({response.status}): "
                     f"{raw.decode('utf-8', 'replace')[:200]}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except Exception:
            return error.code, {}
    except urllib.error.URLError as error:
        fail(f"connection failed: {error.reason}")


def fail(message, payload=None):
    print(f"ERROR: {message}", file=sys.stderr)
    if payload is not None:
        print(json.dumps(payload, indent=2), file=sys.stderr)
    sys.exit(1)


def fetch_catalog(base_url, token):
    status, data = api(base_url, token, "GET", "/api/v1/engine_fields/pre_trained_fields")
    if status != 200:
        fail(f"pre_trained_fields fetch failed ({status})", data)
    return data["results"] if isinstance(data, dict) else data


def fetch_engine_fields(base_url, token, engine_id):
    fields, path = [], f"/api/v1/engine_fields?engine={engine_id}&page_size=100"
    while path:
        status, page = api(base_url, token, "GET", path)
        if status != 200:
            fail(f"engine_fields fetch failed ({status})", page)
        fields.extend(page.get("results", []))
        next_url = (page.get("pagination") or {}).get("next")
        path = next_url.replace(base_url, "") if next_url else None
    return fields


def snapshot(args, label, payload):
    if args.snapshot_dir:
        os.makedirs(args.snapshot_dir, exist_ok=True)
        with open(os.path.join(args.snapshot_dir, f"{label}.json"), "w") as handle:
            json.dump(payload, handle, indent=2)


def check_nonempty_plan(args, fields):
    if fields:
        return
    message = ("no engine-extracted datapoints found in the schema — nothing to bind; "
               "check ui_configuration types")
    if args.execute:
        fail(message)
    print(f"WARNING: {message}", file=sys.stderr)


def warn_multi_source(content):
    multi = [dp["id"] for dp, _t in iter_datapoints(content)
             if is_engine_extracted(dp) and len(dp.get("rir_field_names") or []) > 1]
    if multi:
        print(f"warning: multi-source rir_field_names on {', '.join(multi)} — only the first "
              f"catalog match becomes pre_trained_field_id; other sources are dropped",
              file=sys.stderr)


# ---------- modes ----------

def mode_convert(args, token):
    base = args.base_url
    status, queue = api(base, token, "GET", f"/api/v1/queues/{args.queue_id}")
    if status != 200:
        fail(f"queue fetch failed ({status})", queue)
    if queue.get("engine") or queue.get("dedicated_engine"):
        fail("queue is not generic-engine bound; convert mode requires generic_engine binding")
    schema_id = queue["schema"].rstrip("/").split("/")[-1]
    status, schema = api(base, token, "GET", f"/api/v1/schemas/{schema_id}")
    if status != 200:
        fail(f"schema fetch failed ({status})", schema)
    snapshot(args, "pre_queue", queue)
    snapshot(args, "pre_schema", schema)

    if len(schema.get("queues", [])) > 1:
        fail("schema is shared by multiple queues — copy it first (POST /v1/schemas with this "
             "content, PATCH this queue's schema to the copy), then re-run")

    catalog = fetch_catalog(base, token)
    fields = derive_engine_fields(schema["content"], catalog)
    warn_multi_source(schema["content"])
    check_nonempty_plan(args, fields)
    cleaned, changes = clean_schema(schema["content"])
    plan = {
        "mode": "convert", "queue": queue["id"], "engine_name": args.engine_name or queue["name"],
        "engine_fields": fields, "schema_changes": changes,
    }
    print(json.dumps(plan, indent=2))
    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.", file=sys.stderr)
        return

    status, engine = api(base, token, "POST", "/api/v1/engines", {
        "name": args.engine_name or queue["name"], "type": "extractor",
        "learning_enabled": True, "training_queues": [queue["url"]]})
    if status != 201:
        fail(f"engine creation failed ({status}) — a 403 means the org/token lacks this "
             f"permission; escalate to Rossum support", engine)
    print(f"engine created: {engine['id']}", file=sys.stderr)
    for field in fields:
        status, created = api(base, token, "POST", "/api/v1/engine_fields",
                              {**field, "engine": engine["url"]})
        if status != 201:
            fail(f"engine field '{field['name']}' creation failed ({status})", created)
    status, resp = api(base, token, "PATCH", f"/api/v1/schemas/{schema_id}", {"content": cleaned})
    if status != 200:
        fail(f"schema cleanup failed ({status})", resp)
    status, resp = api(base, token, "PATCH", f"/api/v1/queues/{queue['id']}", {"engine": engine["url"]})
    if status != 200:
        fail(f"queue flip failed ({status}); violations listed in non_field_errors", resp)
    result = {"flipped": True, "engine": engine["url"]}
    status, stats = api(base, token, "GET", f"/api/v1/engines/{engine['id']}/queue_stats")
    if status == 200:
        result["queue_stats"] = stats
    else:
        print(f"warning: queue_stats fetch failed ({status})", file=sys.stderr)
    print(json.dumps(result, indent=2))


def mode_attach(args, token):
    base = args.base_url
    status, queue = api(base, token, "GET", f"/api/v1/queues/{args.queue_id}")
    if status != 200:
        fail(f"queue fetch failed ({status})", queue)
    status, engine = api(base, token, "GET", f"/api/v1/engines/{args.engine_id}")
    if status != 200:
        fail(f"engine fetch failed ({status})", engine)
    schema_id = queue["schema"].rstrip("/").split("/")[-1]
    status, schema = api(base, token, "GET", f"/api/v1/schemas/{schema_id}")
    if status != 200:
        fail(f"schema fetch failed ({status})", schema)
    snapshot(args, "pre_attach_queue", queue)
    snapshot(args, "pre_attach_schema", schema)
    existing = {f["name"] for f in fetch_engine_fields(base, token, args.engine_id)}
    catalog = fetch_catalog(base, token)
    needed = derive_engine_fields(schema["content"], catalog)
    missing = [f for f in needed if f["name"] not in existing]
    cleaned, changes = clean_schema(schema["content"])
    print(json.dumps({"mode": "attach", "missing_engine_fields": missing,
                      "schema_changes": changes}, indent=2))
    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.", file=sys.stderr)
        return
    for field in missing:
        status, created = api(base, token, "POST", "/api/v1/engine_fields",
                              {**field, "engine": engine["url"]})
        if status != 201:
            fail(f"engine field '{field['name']}' creation failed ({status})", created)
    status, resp = api(base, token, "PATCH", f"/api/v1/schemas/{schema_id}", {"content": cleaned})
    if status != 200:
        fail(f"schema cleanup failed ({status})", resp)
    status, resp = api(base, token, "PATCH", f"/api/v1/queues/{queue['id']}", {"engine": engine["url"]})
    if status != 200:
        fail(f"queue flip failed ({status})", resp)
    print(json.dumps({"attached": True, "engine": engine["url"]}, indent=2))


def mode_greenfield(args, token):
    base = args.base_url
    try:
        with open(args.schema_file) as handle:
            content = json.load(handle)["content"]
    except FileNotFoundError:
        fail(f"schema file not found: {args.schema_file}")
    except json.JSONDecodeError as error:
        fail(f"schema file is not valid JSON: {args.schema_file} ({error})")
    except KeyError:
        fail(f"schema file has no 'content' key: {args.schema_file}")
    catalog = fetch_catalog(base, token)
    fields = derive_engine_fields(content, catalog)
    warn_multi_source(content)
    check_nonempty_plan(args, fields)
    cleaned, changes = clean_schema(content)
    print(json.dumps({"mode": "greenfield", "engine_fields": fields,
                      "schema_changes": changes}, indent=2))
    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.", file=sys.stderr)
        return
    status, engine = api(base, token, "POST", "/api/v1/engines", {
        "name": args.engine_name or args.queue_name, "type": "extractor",
        "learning_enabled": True, "training_queues": []})
    if status != 201:
        fail(f"engine creation failed ({status})", engine)
    for field in fields:
        status, created = api(base, token, "POST", "/api/v1/engine_fields",
                              {**field, "engine": engine["url"]})
        if status != 201:
            fail(f"engine field '{field['name']}' creation failed ({status})", created)
    status, schema = api(base, token, "POST", "/api/v1/schemas",
                         {"name": f"{args.queue_name} schema", "content": cleaned})
    if status != 201:
        fail(f"schema creation failed ({status})", schema)
    queue_body = {"name": args.queue_name, "workspace": args.workspace_url,
                  "schema": schema["url"], "engine": engine["url"]}
    status, queue = api(base, token, "POST", "/api/v1/queues", queue_body)
    if status != 201:
        # POST /v1/queues accepts engine directly (verified live); keep a defensive fallback
        queue_body.pop("engine")
        status, queue = api(base, token, "POST", "/api/v1/queues", queue_body)
        if status != 201:
            fail(f"queue creation failed ({status})", queue)
        status, queue = api(base, token, "PATCH", f"/api/v1/queues/{queue['id']}",
                            {"engine": engine["url"]})
        if status != 200:
            fail(f"queue engine flip failed ({status})", queue)
    status, resp = api(base, token, "PATCH", f"/api/v1/engines/{engine['id']}",
                       {"training_queues": [queue["url"]]})
    if status != 200:
        fail(f"engine training_queues update failed ({status}) — the queue was created but the "
             f"engine has no training queue; PATCH /v1/engines/{engine['id']} manually", resp)
    print(json.dumps({"created": True, "queue": queue["url"], "engine": engine["url"]}, indent=2))


def mode_revert(args, token):
    base = args.base_url
    status, queue = api(base, token, "GET", f"/api/v1/queues/{args.queue_id}")
    if status != 200:
        fail(f"queue fetch failed ({status})", queue)
    if not queue.get("engine"):
        fail("queue is not bound to a custom engine")
    engine_id = queue["engine"].rstrip("/").split("/")[-1]
    schema_id = queue["schema"].rstrip("/").split("/")[-1]
    status, schema = api(base, token, "GET", f"/api/v1/schemas/{schema_id}")
    if status != 200:
        fail(f"schema fetch failed ({status})", schema)
    snapshot(args, "pre_revert_queue", queue)
    snapshot(args, "pre_revert_schema", schema)
    engine_fields = fetch_engine_fields(base, token, engine_id)
    restored = restore_rir(schema["content"], engine_fields)
    print(json.dumps({"mode": "revert", "generic_engine": args.generic_engine_url,
                      "restored_rir_fields": sum(1 for dp, _ in iter_datapoints(restored)
                                                 if dp.get("rir_field_names"))}, indent=2))
    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.", file=sys.stderr)
        return
    status, resp = api(base, token, "PATCH", f"/api/v1/queues/{queue['id']}",
                       {"engine": None, "generic_engine": args.generic_engine_url})
    if status != 200:
        fail(f"queue detach failed ({status})", resp)
    status, resp = api(base, token, "PATCH", f"/api/v1/schemas/{schema_id}", {"content": restored})
    if status != 200:
        fail(f"rir restore failed ({status})", resp)
    print(json.dumps({"reverted": True}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["convert", "greenfield", "attach", "revert"])
    parser.add_argument("--base-url", required=True, help="e.g. https://example.rossum.app")
    parser.add_argument("--queue-id", type=int, help="convert/attach/revert: target queue")
    parser.add_argument("--engine-id", type=int, help="attach: existing engine id")
    parser.add_argument("--engine-name", help="convert/greenfield: engine name (default: queue name)")
    parser.add_argument("--schema-file", help="greenfield: local JSON with {'content': [...]}")
    parser.add_argument("--queue-name", help="greenfield: new queue name")
    parser.add_argument("--workspace-url", help="greenfield: workspace URL for the new queue")
    parser.add_argument("--generic-engine-url", help="revert: generic engine URL to rebind")
    parser.add_argument("--snapshot-dir", help="directory for pre-state snapshots")
    parser.add_argument("--execute", action="store_true", help="apply changes (default: dry run)")
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")

    token = os.environ.get("ROSSUM_TOKEN")
    if not token:
        fail("set ROSSUM_TOKEN in the environment")
    required = {"convert": ["queue_id"], "attach": ["queue_id", "engine_id"],
                "greenfield": ["schema_file", "queue_name", "workspace_url"],
                "revert": ["queue_id", "generic_engine_url"]}
    for name in required[args.mode]:
        if getattr(args, name) is None:
            fail(f"--{name.replace('_', '-')} is required for {args.mode}")
    {"convert": mode_convert, "attach": mode_attach,
     "greenfield": mode_greenfield, "revert": mode_revert}[args.mode](args, token)


if __name__ == "__main__":
    main()
