#!/usr/bin/env python3
"""Shape-record extractor — P2 first slice, schema v0.4.3.

Read-only, stdlib-only, no API calls. Reads one pulled prd2 environment and emits the
mechanically-determinable parts of a shape record. Judgement calls (role naming, instance
grouping, queue_axis) are emitted as CANDIDATES for a human to confirm, never guessed.

Design rules, each learned from a specific failure:
  * packaging comes from settings keys, never hostnames (one exception, documented below)
  * a settings key shared by two extensions cannot decide alone (credentials != import)
  * queue<->hook bindings are read from the QUEUE side; hook.queues goes stale in a pull
  * a run_after target absent from the pull is MISSING_HOOK, never a dropped edge
  * unknown shapes emit `unclassified`; nothing snaps to the nearest known pattern
  * master-data COLLECTION NAMES are customer data: only inferred ROLES are emitted

Usage:
  shape_extract.py --summary [ROOT]        one line per implementation under ROOT
  shape_extract.py --env PATH [--json OUT] full skeleton for one environment
"""
import json, glob, os, re, sys, collections, datetime

INCLUDE_NAMES = False   # set by --include-names; see infer_role()

# ---------- packaging decision table (v0.4.3) ----------

def packaging(h):
    s, cfg = h.get("settings") or {}, h.get("config") or {}
    url = cfg.get("url") or ""
    if "stages" in s:                                        return "request_processor"
    if "request" in s and ("auth" in s or "response_headers_reference_key" in s):
        return "legacy_rest_chain"
    if "export_configs" in s:                                return "custom_format_template"
    if "extract" in s:                                       return "response_extractor"
    if "import_config" in s:                                 return "hosted_scheduled_import"
    if "import_rules" in s:                                  return "file_storage_transfer"
    if "export_rules" in s:                                  return "file_storage_transfer"
    blob = json.dumps(s).lower()
    if re.search(r"\bsftp\b|file[_-]storage", blob):         return "file_storage_transfer"
    if h.get("extension_source") == "rossum_store":          return "store_extension"
    if h.get("type") == "function" and cfg.get("code"):
        return "parameterized_function" if s else "inline_function"
    if h.get("type") == "webhook" and url:
        # hostname consulted ONLY to split the residual unconfigured bucket
        return "hosted_service" if ".rossum-ext.app" in url else "bare_webhook"
    return "unclassified"

TRANSPORTING = {"legacy_rest_chain", "request_processor", "file_storage_transfer"}
FUNCTION_PACK = {"inline_function", "parameterized_function", "store_extension"}
ROSSUM_HOST = re.compile(r"(^|\.)(rossum\.(app|ai)|rossum-ext\.app)$", re.I)

HTTP_CLIENT = re.compile(r"requests\.(get|post|put|patch|delete)|urllib\.request|urlopen|"
                         r"httpx\.|http\.client", re.I)
# markers that a call targets ROSSUM'S OWN API rather than an external system. This pattern is
# everywhere (read a document, apply a label, patch a datapoint) and counting it as an outbound
# leg inflated one tree from file_out to "both".
ROSSUM_MARKER = ("rossum_authorization_token", "payload[", "/api/v1/", "base_url", "BASE_URL")

def outbound_http(h):
    """Does this function send data OUT of the platform? Returns
    "confirmed" | "confirmed_via_secret" | "candidate" | "".

    Hostnames are never emitted — a customer endpoint is customer data.
    """
    code = (h.get("config") or {}).get("code") or ""
    if not code or not HTTP_CLIENT.search(code):
        return ""
    hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", code))
    if any(not ROSSUM_HOST.search(x) for x in hosts):
        return "confirmed"
    if any(m in code for m in ROSSUM_MARKER):
        return ""                      # talks to Rossum itself; not an integration leg
    if re.search(r"\bsecrets?\b", code):
        return "confirmed_via_secret"  # endpoint assembled from a secret: real, destination hidden
    return "candidate"                 # client present, no evidence either way — leave for a human

EXPORT_TRIGGERS = ("export", "confirm", "status.changed")

def direction(h, pk):
    """File transfers carry no direction in settings — read it from the event (v0.4.4).
    The export trigger is NOT always annotation_content.export: one tree exports on
    .confirm, which made its only outbound leg invisible (v0.4.6)."""
    if pk != "file_storage_transfer":
        return "out"
    ev = " ".join(h.get("events") or [])
    if any(t in ev for t in EXPORT_TRIGGERS):  return "out"
    if "invocation" in ev or "upload" in ev:   return "in"
    return "uncertain"

# ---------- target class (word-bounded: 'sage' must not match 'message') ----------

TARGETS = [("coupa", r"\bcoupa\b|coupahost"), ("sap", r"\bsap\b|s4hana|/sap/opu|\bidoc\b|\bbtp\b|hana\.ondemand"),
           ("workday", r"\bworkday\b"), ("netsuite", r"\bnetsuite\b"), ("oracle", r"\boracle\b"),
           ("salesforce", r"\bsalesforce\b|force\.com"), ("dynamics", r"\bdynamics\b|\bd365\b|business ?central"),
           ("sage", r"\bsage\b"), ("infor", r"\binfor\b"), ("edi", r"\bedi\b|\bx12\b|edifact")]

# ---------- master-data role inference (ORDER MATTERS; names never emitted) ----------

ROLES = [("purchase_order_line", r"(po|purchase[_ ]?order).*(line|item)|poline"),
         ("purchase_order",      r"purchase[_ ]?order|\bpo\b|\border"),
         ("supplier",            r"supplier|vendor|business[_ ]?partner|\bbp\b"),
         ("tax_registration",    r"tax[_ ]?reg|vat[_ ]?reg"),
         ("tax_code",            r"tax[_ ]?code|taxcode|\btax\b"),
         ("cost_center",         r"cost[_ ]?cent"),
         ("gl_account",          r"gl[_ ]?account|gl[_ ]?code|\bgl\b"),
         ("business_unit",       r"business[_ ]?unit|company[_ ]?code|\bentity\b"),
         ("commodity",           r"commodity"),
         ("address",             r"address"),
         ("lookup_values",       r"lookup"),
         ("user",                r"\buser|approver|employee")]

# noise words that appear in hook labels around the real dataset word
NOISE = re.compile(r"\b(prod|production|test|uat|dev|sandbox|import|imports|importer|export|webhook|"
                   r"scheduled|schedule|master|data|hub|update|updates|sync|rta|daily|weekly|hourly|"
                   r"every|new|old|v\d+|copy|of|the|to|from|and|coupa|sap|workday|netsuite|oracle|"
                   r"dynamics|salesforce)\b", re.I)

def infer_role(name, include_names=False):
    """Map a collection or hook label to a controlled role.

    Unknown roles are NOT slugged by default: a mangled collection name is still a collection
    name, and these records are mined from customer trees. --include-names opts in explicitly.
    """
    n = NOISE.sub(" ", (name or "").lower())
    for role, pat in ROLES:
        if re.search(pat, n):
            return role
    if include_names:
        return "other:" + re.sub(r"[^a-z0-9]+", "_", n)[:24].strip("_")
    return "other:unclassified"

def collection_names(h):
    """Every collection this hook reads or writes, from settings only."""
    s = h.get("settings") or {}
    out = set()
    for k in ("COLLECTION_NAME", "collection_name"):
        if isinstance(s.get(k), str): out.add(s[k])
    for c in (s.get("configurations") or []):
        if isinstance(c, dict):
            ds = (c.get("source") or {}).get("dataset")
            if isinstance(ds, str): out.add(ds)
    ic = s.get("import_config")
    if isinstance(ic, dict):
        for k in ("collection_name", "collection", "dataset", "target_collection"):
            if isinstance(ic.get(k), str): out.add(ic[k])
    if not out and "import_config" in s:
        # no collection in settings: infer from the hook label, with vendor/env noise stripped
        out.add(h.get("name", ""))
    return out

def internal_units(h):
    s = h.get("settings") or {}
    for k, kind in (("configurations", "configurations"), ("stages", "stages"),
                    ("export_configs", "export_configs"), ("rules", "rules")):
        v = s.get(k)
        if isinstance(v, list): return {"kind": kind, "n": len(v)}
    if isinstance(s.get("extract"), list):
        return {"kind": "extract_rules", "n": sum(len(e.get("extract_rules") or []) for e in s["extract"])}
    if "import_config" in s: return {"kind": "import_config", "n": 1}
    return None

# ---------- loading ----------

SLUG_ID = re.compile(r"_\[(\d+)\]$")

def load(env):
    """Hooks are keyed by BOTH numeric id and file slug: some repo layouts reference hooks by
    slug from the queue side, and resolving by id alone reports phantom MISSING_HOOKs."""
    hooks, bad, alias = {}, [], {}
    for f in glob.glob(f"{env}/hooks/*.json"):
        try: h = json.load(open(f))
        except Exception as ex: bad.append((f, str(ex))); continue
        base = os.path.basename(f)[:-5]
        slug = SLUG_ID.sub("", base)
        # code may live in a sibling .py rather than inline in config.code
        if not (h.get("config") or {}).get("code"):
            for cand in (f[:-5] + ".py", os.path.join(os.path.dirname(f), slug + ".py")):
                if os.path.exists(cand):
                    h.setdefault("config", {})["code"] = open(cand, errors="replace").read()
                    h["_code_from_sibling"] = True
                    break
        key = str(h.get("id") if h.get("id") is not None else slug)
        hooks[key] = h
        alias[slug] = key
        alias[base] = key
        alias[key] = key
    globals()["ALIAS"] = alias
    queues = []
    for f in glob.glob(f"{env}/workspaces/*/queues/*/queue.json"):
        try: queues.append(json.load(open(f)))
        except Exception as e: bad.append((f, str(e)))
    rules = []
    for f in glob.glob(f"{env}/rules/*.json"):
        try: rules.append(json.load(open(f)))
        except Exception as e: bad.append((f, str(e)))
    return hooks, queues, rules, bad

def mtime_span(env):
    ds = sorted({datetime.date.fromtimestamp(os.path.getmtime(f)).isoformat()
                 for f in glob.glob(f"{env}/**/*.json", recursive=True)})
    return (ds[0], ds[-1]) if ds else ("?", "?")

# ---------- extraction ----------

def extract(env):
    hooks, queues, rules, bad = load(env)
    lo, hi = mtime_span(env)

    # bindings from the QUEUE side
    q_of_hook = collections.defaultdict(set)
    alias = globals().get("ALIAS", {})
    for q in queues:
        for hu in (q.get("hooks") or []):
            ref = str(hu).rsplit("/", 1)[-1]
            q_of_hook[alias.get(ref, ref)].add(str(q["id"]))
    referenced = set(q_of_hook)
    missing_hooks = sorted(referenced - set(hooks))
    h_of_queue = {hid: {u.rsplit("/", 1)[-1] for u in (h.get("queues") or [])}
                  for hid, h in hooks.items()}
    stale = [hid for hid in hooks if h_of_queue[hid] != q_of_hook.get(hid, set())]
    # D4 said "always read the queue side" — true for a prd2 pull, WRONG for layouts where the
    # queue side is sparse. Rule: use the side with more coverage; report which, and the delta.
    queue_side_total = sum(len(v) for v in q_of_hook.values())
    hook_side_total = sum(len(v) for v in h_of_queue.values())
    binding_source = "queue_side" if queue_side_total >= hook_side_total else "hook_side"
    bound = q_of_hook if binding_source == "queue_side" else h_of_queue

    pk = {hid: packaging(h) for hid, h in hooks.items()}
    pset = sorted(set(pk.values()))
    fn_out = [ob for hid, h in hooks.items()
              if (ob := outbound_http(h)) and pk[hid] in FUNCTION_PACK
              and any(t in " ".join(h.get("events") or []) for t in EXPORT_TRIGGERS)]
    proven = [x for x in fn_out if x in ("confirmed", "confirmed_via_secret")]
    http_out = bool({"legacy_rest_chain", "request_processor"} & set(pset)) or bool(proven)
    shape_evidence = ("settings" if {"legacy_rest_chain", "request_processor"} & set(pset)
                      else "function_secret_endpoint" if "confirmed_via_secret" in fn_out
                      else "function_external_host" if "confirmed" in fn_out
                      else "function_http_client_unproven" if fn_out else None)
    file_out = any(pk[hid] == "file_storage_transfer" and direction(h, pk[hid]) == "out"
                   for hid, h in hooks.items())
    shape = ("both" if http_out and file_out else "https_out" if http_out
             else "file_out" if file_out else "unclassified")

    # transport legs — one per hook that actually sends data out
    legs = []
    for hid, h in hooks.items():
        ob = outbound_http(h)
        if pk[hid] in FUNCTION_PACK and ob and \
           any(t in " ".join(h.get("events") or []) for t in EXPORT_TRIGGERS):
            legs.append({"hook_label": h.get("name", "")[:60], "packaging": pk[hid],
                         "direction": "out", "via": "function_code", "confidence": ob,
                         "gated": False, "active": bool(h.get("active", True))})
        if pk[hid] in TRANSPORTING:
            s = h.get("settings") or {}
            legs.append({"hook_label": h.get("name", "")[:60], "packaging": pk[hid],
                         "direction": direction(h, pk[hid]),
                         "gated": bool(s.get("condition")), "active": bool(h.get("active", True))})

    # coding model — ALL schemas, not a sample
    segs, named = set(), set()
    for f in glob.glob(f"{env}/workspaces/*/queues/*/schema.json"):
        try: txt = open(f).read()
        except Exception: continue
        segs.update(int(m) for m in re.findall(r'"(?:item_)?(?:ext_)?account_segment_(\d+)"', txt))
        for canon, pat in (("gl_account", r"gl_?(account|code)"), ("cost_center", r"cost_?cent|\bdept\b|dept_"),
                           ("wbs", r"\bwbs\b"), ("project", r"project_?(code|id|no)"),
                           ("business_unit", r"business_?unit|company_?code"), ("branch", r"branch_?(id|code)")):
            if re.search(r'"(?:item_|ext_)?[a-z_]*' + pat, txt): named.add(canon)
    coding = {"model": "segmented" if segs else ("named" if named else "none"),
              "n_segments": len(segs) or None, "segment_numbers": sorted(segs) or None,
              "dimensions": sorted(named) or []}

    # master data: ROLES only
    md = collections.defaultdict(set)
    unclassified_md = 0
    for hid, h in hooks.items():
        for cn in collection_names(h):
            role = infer_role(cn, INCLUDE_NAMES)
            if role == "other:unclassified": unclassified_md += 1
            md[role].add("imported" if pk[hid] in
                         ("hosted_scheduled_import", "parameterized_function") else "read")

    # hook graph — one node per hook, edges resolved, plus grouping CANDIDATES
    nodes = []
    for hid, h in hooks.items():
        cfg = h.get("config") or {}
        edges = []
        for u in (h.get("run_after") or []):
            t = str(u).rsplit("/", 1)[-1]
            edges.append(hooks[t].get("name", t)[:48] if t in hooks else "MISSING_HOOK")
        nodes.append({"hook_id": hid, "hook_label": h.get("name", "")[:60],
                      "events": h.get("events") or [], "type": h.get("type"),
                      "packaging": pk[hid], "active": bool(h.get("active", True)),
                      "token_owner": bool(h.get("token_owner")), "sideload": h.get("sideload") or [],
                      "timeout_s": cfg.get("timeout_s"), "queues_bound": len(bound.get(hid, set()) if isinstance(bound, dict) else set()),
                      "internal_units": internal_units(h), "code_len": len(cfg.get("code") or ""),
                      "run_after": edges,
                      "gated": bool((h.get("settings") or {}).get("condition"))})
    # candidate instance groups: same packaging + same edge set + same code length
    groups = collections.defaultdict(list)
    for n in nodes:
        groups[(n["packaging"], tuple(sorted(n["run_after"])), n["code_len"], tuple(n["events"]))].append(n["hook_label"])
    candidates = [{"n": len(v), "members": v} for v in groups.values() if len(v) > 1]

    # rules
    ACT = {"add_automation_blocker": "automation_blocker", "change_queue": "routing",
           "show_message": "validation_message", "add_remove_label": "label_or_visibility",
           "show_hide_field": "label_or_visibility", "add_label": "label_or_visibility",
           "remove_label": "label_or_visibility", "send_email": "notification",
           "change_status": "status_change", "set_value": "field_write",
           "show_field": "label_or_visibility", "hide_field": "label_or_visibility"}
    by_act = collections.Counter()
    for r in rules:
        for a in (r.get("actions") or []):
            by_act[ACT.get(a.get("type"), a.get("type") or "unknown")] += 1

    blob = " ".join((h.get("name", "") + json.dumps(h.get("settings") or {})[:4000] +
                     str((h.get("config") or {}).get("url"))).lower() for h in hooks.values())
    mdh_files = glob.glob(f"{env}/mdh/**/*.json", recursive=True)
    mdh_dir_configs = 0
    for f in mdh_files:
        try:
            d = json.load(open(f))
            c = d.get("configurations") if isinstance(d, dict) else None
            mdh_dir_configs += len(c) if isinstance(c, list) else 1
        except Exception: pass
    engines = glob.glob(f"{env}/engines/*/")
    eng_bound = sum(1 for q in queues if q.get("engine"))

    LIFECYCLE = re.compile(r"\(ip(uat|dev|test|sit|qa)\)|\b(uat|dev|test|sit|qa)[ _\-]|"
                           r"[ _\-](uat|dev|test|sit|qa)\b|^\s*(test|dev|uat)\b", re.I)
    lc = collections.Counter()
    for h in hooks.values():
        m = LIFECYCLE.search(h.get("name", "") or "")
        if m: lc[next(g for g in m.groups() if g).lower()] += 1

    ingest_modes = set()
    for hid, h in hooks.items():
        ev = " ".join(h.get("events") or [])
        if "email.received" in ev: ingest_modes.add("email")
        if "upload.created" in ev: ingest_modes.add("upload")
        if pk[hid] == "file_storage_transfer" and direction(h, pk[hid]) == "in":
            ingest_modes.add("sftp_pull")
        if re.search(r"structured|sfi|zugferd|ubl|e-?invoic", h.get("name", ""), re.I):
            ingest_modes.add("structured_format")

    # org-level scheduled/manual hooks have no edges BY NATURE — excluding them or the ratio lies
    orderable = [n for n in nodes
                 if not all(ev.startswith("invocation.") for ev in (n["events"] or ["x"]))]
    if glob.glob(f"{env}/workspaces/*/queues/*/inbox.json"): ingest_modes.add("email")
    roots = sum(1 for n in orderable if not n["run_after"])
    frac = roots / max(len(orderable), 1)
    # longest edge chain, as a second signal the ratio cannot give
    by_label = {n["hook_label"]: n for n in nodes}
    def depth(n, seen=()):
        if n["hook_label"] in seen: return 0
        ups = [by_label[p] for p in n["run_after"] if p in by_label]
        return 1 + max([depth(u, seen + (n["hook_label"],)) for u in ups], default=0)
    max_depth = max([depth(n) for n in nodes], default=0)
    # two independent signals; only agreement licenses a definite label
    ordering = ("implicit_by_queue_scope" if frac > 0.6 and max_depth <= 3
                else "explicit_edges"     if frac < 0.3 and max_depth >= 5
                else "mixed")

    return {
        "schema_version": "0.4.8",
        "ingest": {"modes": sorted(ingest_modes)},
        "structure": {"export_trigger": sorted({ev for hid, h in hooks.items()
                                                for ev in (h.get("events") or [])
                                                if any(t in ev for t in EXPORT_TRIGGERS)
                                                and (pk[hid] in TRANSPORTING or outbound_http(h))}),
                      "ordering": ordering, "roots_of_orderable": roots,
                      "orderable_nodes": len(orderable), "nodes": len(nodes),
                      "max_chain_depth": max_depth}, "tier": "PRIVATE — collection names withheld; labels are not",
        "source_pin": {"env": env, "pulled": lo if lo == hi else f"{lo}..{hi}",
                       "snapshot_consistent": lo == hi,
                       "complete": not missing_hooks,
                       "missing_objects": [f"hook:{m} (bound on {len(q_of_hook[m])} queues)" for m in missing_hooks],
                       "binding_source": binding_source,
                       "binding_coverage": {"queue_side": queue_side_total, "hook_side": hook_side_total},
                       "unparsable_files": len(bad),
                       "stale_hook_side_bindings": len(stale)},
        "lifecycle_variants": {"tagged_hooks": sum(lc.values()), "tags": dict(lc),
                               "note": "non-production copies inside this environment; exclude "
                                       "them before clustering or hook counts are inflated"},
        "topology": {"workspaces": len(glob.glob(f"{env}/workspaces/*/")), "queues": len(queues),
                     "queue_labels_sample": sorted(q.get("name", "") for q in queues)[:8],
                     "unintegrated_queues": sum(1 for q in queues if not (q.get("hooks") or []))},
        "target_profile": {"name_class_candidates": [n for n, p in TARGETS if re.search(p, blob)],
                           "md_inbound_roles": sorted(r for r, k in md.items() if "imported" in k),
                           "md_read_roles": sorted(md),
                           "md_unclassified_refs": unclassified_md, "coding_model": coding},
        "integration_shape": shape,
        "integration_shape_evidence": shape_evidence,
        "integration_shape_confidence": (
            "high" if shape_evidence in ("settings", "function_external_host",
                                         "function_secret_endpoint")
            else "candidate" if shape_evidence else "n/a"),
        "packaging": pset,
        "packaging_counts": dict(collections.Counter(pk.values()).most_common()),
        "transport_candidates": [l for l in legs if l["direction"] == "out"],
        "inbound_file_transfers": [l for l in legs if l["direction"] != "out"],
        "hook_graph": {"nodes": nodes, "instance_group_candidates": candidates},
        "rules": {"scope": "native_only", "total": len(rules),
                  "enabled": sum(1 for r in rules if r.get("enabled")), "by_action_class": dict(by_act)},
        "matching": {"configs_in_hook_settings":
                         sum((n["internal_units"] or {}).get("n", 0) if isinstance((n["internal_units"] or {}).get("n"), int) else 0
                             for n in nodes if (n["internal_units"] or {}).get("kind") == "configurations"),
                     "config_files_in_mdh_dir": len(mdh_files),
                     "configs_in_mdh_dir": mdh_dir_configs},
        "engines": {"total": len(engines), "bound_to_queues": eng_bound,
                    "custom_fields": bool(glob.glob(f"{env}/engines/*/engine_fields/*.json"))},
    }

# ---------- discovery / CLI ----------

SKIP = re.compile(r"rossum-claude-plugin|deployment-manager|script|code_analyzer|adhoc|"
                  r"chrome-overrides|404-finder|APP-deployments|\.venv|node_modules", re.I)

def find_envs(root):
    out = []
    for hd in sorted(glob.glob(f"{root}/*/*/*/hooks")):
        env = os.path.dirname(hd)
        if not SKIP.search(env): out.append(env)
    return out

def main():
    global INCLUDE_NAMES
    a = sys.argv[1:]
    INCLUDE_NAMES = "--include-names" in a
    if "--env" in a:
        rec = extract(a[a.index("--env") + 1])
        if "--json" in a:
            p = a[a.index("--json") + 1]; json.dump(rec, open(p, "w"), indent=2)
            print("written:", p)
        else:
            print(json.dumps(rec, indent=2))
        return
    root = os.path.expanduser(a[1] if len(a) > 1 else "~/Projects")
    envs = find_envs(root)
    best = {}
    for env in envs:                       # one env per tree: prefer prod, then most hooks
        tree = env.split("/")[len(root.rstrip("/").split("/"))]
        n = len(glob.glob(f"{env}/hooks/*.json"))
        score = (1 if re.search(r"prod", env, re.I) else 0, n)
        if n and score > best.get(tree, ((-1, -1), None))[0]: best[tree] = (score, env)
    print(f"{'tree':17}{'q':>4}{'h':>4}{'r':>4}{'eng':>4}  {'shape':12}{'target':16}"
          f"{'coding':11}{'pin':>12} {'ok':>3}  md_inbound_roles")
    rows = []
    for tree, (_, env) in best.items():
        try: r = extract(env)
        except Exception as e: print(f"{tree[:17]:17} EXTRACT FAILED: {e}"); continue
        rows.append((tree, r))
    for tree, r in sorted(rows, key=lambda t: -len(t[1]["hook_graph"]["nodes"])):
        sp, tp = r["source_pin"], r["target_profile"]
        ok = "yes" if sp["complete"] and sp["snapshot_consistent"] else \
             ("PIN" if not sp["snapshot_consistent"] else "GAP")
        print(f"{tree[:17]:17}{r['topology']['queues']:>4}{len(r['hook_graph']['nodes']):>4}"
              f"{r['rules']['total']:>4}{r['engines']['total']:>4}  {r['integration_shape']:12}"
              f"{','.join(tp['name_class_candidates'])[:15]:16}{tp['coding_model']['model']:11}"
              f"{sp['pulled'][-10:]:>12} {ok:>3}  {','.join(tp['md_inbound_roles'])[:44]}")
    print(f"\n{len(rows)} implementations; shape tally:",
          dict(collections.Counter(r['integration_shape'] for _, r in rows)))

if __name__ == "__main__":
    main()
