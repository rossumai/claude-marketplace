#!/usr/bin/env python3
"""Phase 3a: apply a patch plan against a target annotation in two phases with a status toggle.

Encapsulates the API quirks discovered in real runs:

1. Row ops (`remove`, `add_empty_tuple`) go via the bulk operations endpoint
   `POST /annotations/<id>/content/operations` — there is no per-datapoint
   equivalent for row mutations.

2. Replace ops go via the **per-datapoint endpoint**
   `PATCH /annotations/<id>/content/<dp_id>` with body
   `{"content": {"value": "...", "page": N, "position": [...]}}`.

   Why not the bulk operations endpoint for replace ops? It returns HTTP 200 but
   silently no-ops on enum-typed and `ui_configuration.type=manual` fields
   (e.g., `document_type`, `enforce_draft`). Confirmed by direct testing; the
   per-dp endpoint applies correctly for every field type tested.

3. **Per-datapoint PATCH does NOT trigger hook events** (`annotation_content.updated`).
   Neither does the bulk `/content/operations` endpoint. The only events that fire
   the hook chain on an existing annotation are status transitions
   (`annotation_content.started` fires on `* → to_review`). Without a forced
   status toggle, MDH and rules will see only the OCR state — never the PATCHed
   state. This script does the toggle automatically (postpone → restore) between
   the two replace-op phases.

4. **Two-phase replace-op apply.** Fields prod's snapshot marks as written by
   hooks (`validation_sources` contains `data_matching`, `rules`, or `connector`)
   are MDH/rule OUTPUTS — they must be PATCHed AFTER hooks fire in the target,
   or the hook firing will overwrite them. Fields written by extraction or
   human edits are INPUTS — they must be PATCHed BEFORE hooks fire so the hooks
   see prod-faithful inputs. `build_patch_plan.py` tags each op with
   `_meta.phase` ∈ {`pre`, `post`}; this script applies them in that order with
   a status toggle in between.

5. `validation_sources` is NOT a PATCH-success signal. The per-dp endpoint
   applies the value but does not flip val_src to include "human". To verify
   success, the script reads the response body's `content.value` and compares
   against the requested value.

6. Dedup auto-delete: many customer queues have a duplicate-detection hook that
   auto-deletes annotations matching previously-uploaded documents. If the
   pre-PATCH GET sees `status: "deleted"` despite a recent upload, this script
   PATCHes status back to `to_review` and proceeds (the dedup hook does not
   re-fire on status change).

7. **Formula re-evaluation requires `POST /content/validate` while the
   annotation is in `reviewing` status.** Per-dp PATCH and
   `/content/operations` mutate values but do NOT re-evaluate formula
   fields. The dependency graph evaluator runs on `POST
   /annotations/<id>/content/validate` (this is what the UI triggers when the
   user types — that's why the Confirm button shows "validating"). The
   endpoint returns HTTP 409 "Document is not being annotated" unless the
   annotation is in `reviewing` (the state the UI puts it in when a user
   opens it), so `trigger_validate()` flips status to `reviewing`, calls
   validate, then restores the prior status. Without this, formulas stay at
   their OCR-time values even though their inputs changed.

Sequence:

  1. Pre-flight: dedup-restore if needed.
  2. Apply row ops via /content/operations.
  3. Apply pre-phase replace ops (inputs) via per-dp PATCH.
  4. POST /content/validate to trigger formula re-eval on PATCHed inputs.
  5. Status toggle: PATCH status to `postponed`, then back to `to_review`.
     The transition fires `annotation_content.started` → MDH and rules re-run
     on the prod-faithful inputs.
  6. Wait for hooks to settle (configurable, default 20s).
  7. Apply post-phase replace ops (hook outputs) via per-dp PATCH — these
     overwrite the hook-produced values with prod's ground truth.
  8. POST /content/validate again to re-evaluate formulas after post-hook ops.

Usage:

    python3 apply_patch_plan.py \
        --plan .test-runs/<ts>/patch-plans/<source_aid>.json \
        --target-aid <target_annotation_id> \
        --token <TARGET_TOKEN> \
        --base-url https://elis.rossum.ai \
        [--toggle-wait 20] [--no-toggle] \
        [--out .test-runs/<ts>/patch-plans/<source_aid>.results.json]
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from content_walker import walk as walk_content


def http(method, url, token, body=None, timeout=30):
    """Minimal HTTPS helper with retry on transient TLS / network errors."""
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                return resp.status, payload
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise last_err


def get_annotation(base, aid, token):
    status, body = http("GET", f"{base}/annotations/{aid}", token)
    if status != 200:
        raise RuntimeError(f"GET /annotations/{aid} HTTP {status}: {body[:200]}")
    return json.loads(body)


def get_dp(base, aid, dp_id, token):
    status, body = http("GET", f"{base}/annotations/{aid}/content/{dp_id}", token)
    if status != 200:
        return None
    return json.loads(body)


def trigger_validate(base, aid, token):
    """POST /content/validate to force formula re-evaluation.

    Per-dp PATCH and /content/operations don't re-run formula fields; the
    dependency-graph evaluator only runs when validate is invoked (this is
    what the UI does as the user types).

    The validate endpoint requires the annotation to be in `reviewing` status
    (it returns HTTP 409 "Document is not being annotated" otherwise — the
    state the UI puts the annotation into when a user opens it). This helper
    flips status to `reviewing`, calls validate, then restores the prior
    status. Returns (ok, error).
    """
    ann = get_annotation(base, aid, token)
    prior_status = ann.get("status")
    flipped = False
    if prior_status != "reviewing":
        st, body = http("PATCH", f"{base}/annotations/{aid}", token, {"status": "reviewing"})
        if st != 200:
            err = body[:200].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:200]
            return False, f"could not enter reviewing (HTTP {st}: {err})"
        flipped = True
    try:
        status, body = http("POST", f"{base}/annotations/{aid}/content/validate", token, {})
        if status != 200:
            err = body[:200].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:200]
            return False, f"HTTP {status}: {err}"
        return True, None
    finally:
        if flipped:
            http("PATCH", f"{base}/annotations/{aid}", token, {"status": prior_status})


def restore_if_deleted(base, aid, token):
    """If the annotation was auto-deleted by a dedup hook, status-flip it back."""
    a = get_annotation(base, aid, token)
    if a.get("status") == "deleted":
        print(f"  [dedup-restore] {aid} found in 'deleted' status — restoring to to_review")
        status, body = http(
            "PATCH",
            f"{base}/annotations/{aid}",
            token,
            {"status": "to_review"},
        )
        if status != 200:
            raise RuntimeError(f"restore PATCH HTTP {status}: {body[:200]}")
        time.sleep(2)
        return True
    return False


def apply_row_ops(base, aid, plan, token):
    row_ops = plan.get("row_ops") or []
    # Translate legacy "add_empty_tuple" → "add" with empty-object value.
    # Rossum /content/operations only accepts add/replace/remove; older plans used
    # the (illegal) add_empty_tuple op name.
    for o in row_ops:
        if o.get("op") == "add_empty_tuple":
            o["op"] = "add"
            o.setdefault("value", {})
    if not row_ops:
        return {"n": 0}
    body_ops = []
    for o in row_ops:
        bo = {"op": o["op"], "id": o["id"]}
        if "value" in o:
            bo["value"] = o["value"]
        body_ops.append(bo)
    status, body = http(
        "POST",
        f"{base}/annotations/{aid}/content/operations",
        token,
        {"operations": body_ops},
        timeout=60,
    )
    return {"n": len(row_ops), "status": status, "error": None if status == 200 else body[:200].decode("utf-8", "replace")}


def status_toggle(base, aid, token):
    """Toggle status to_review → postponed → to_review to fire annotation_content.started.

    PATCH endpoints (bulk + per-dp) do not trigger hook events on the annotation. The
    only event that re-runs MDH / rules / hook formulas after PATCH is a status
    transition that lands on `to_review` (which fires `annotation_content.started`).
    Postpone-then-restore is the lowest-risk trigger.
    """
    for status in ("postponed", "to_review"):
        st, body = http("PATCH", f"{base}/annotations/{aid}", token, {"status": status})
        if st != 200:
            return False, f"toggle to {status} HTTP {st}: {body[:160].decode('utf-8','replace') if isinstance(body,bytes) else body}"
        time.sleep(0.5)
    return True, None


def apply_replace_ops(base, aid, ops, token, verify=True):
    """Apply each replace op via PATCH /content/<dp_id>.

    Each op is submitted individually to avoid the bulk-endpoint silent-no-op trap on
    enum / manual-typed fields.
    """
    ok, fail, mismatch = 0, [], []
    for op in ops:
        v = op["value"]
        content = {"value": v["value"]}
        if v.get("page") is not None:
            content["page"] = v["page"]
        if v.get("position") is not None:
            content["position"] = v["position"]
        status, body = http(
            "PATCH",
            f"{base}/annotations/{aid}/content/{op['id']}",
            token,
            {"content": content},
        )
        if status != 200:
            fail.append({
                "schema_id": op["_meta"].get("schema_id"),
                "dp_id": op["id"],
                "http": status,
                "msg": body[:120].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:120],
            })
            continue
        ok += 1
        # read-back verify (the response body already contains the post-PATCH state; we just compare value)
        if verify:
            try:
                resp = json.loads(body)
                got = resp.get("content", {}).get("value")
                want = v["value"]
                if got != want and not (got is None and want in ("", None)):
                    mismatch.append({
                        "schema_id": op["_meta"].get("schema_id"),
                        "dp_id": op["id"],
                        "want": want,
                        "got": got,
                    })
            except Exception:
                pass
    return {"n_total": len(ops), "ok": ok, "fail": fail, "mismatch": mismatch}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plan", required=True, help="patch plan JSON from build_patch_plan.py")
    p.add_argument("--target-aid", required=True, type=int, help="target annotation ID to apply ops to")
    p.add_argument("--token", required=True, help="API token for the target environment")
    p.add_argument("--base-url", default="https://elis.rossum.ai", help="target base URL (e.g. https://elis.rossum.ai)")
    p.add_argument("--toggle-wait", type=int, default=20, help="seconds to wait after status toggle for hooks to settle (default 20)")
    p.add_argument("--no-toggle", action="store_true", help="skip the post-pre-phase status toggle (single-phase apply: pre + post ops together, hooks won't fire)")
    p.add_argument("--no-verify", action="store_true", help="skip post-PATCH read-back verification")
    p.add_argument("--out", help="optional output JSON for results (defaults to <plan>.results.json)")
    args = p.parse_args()

    plan = json.load(open(args.plan))
    base = args.base_url.rstrip("/") + "/api/v1"
    aid = args.target_aid

    print(f"[apply] plan={args.plan} -> annotation {aid} on {args.base_url}")

    all_ops = plan.get("ops_filtered") or plan.get("ops") or []
    pre_ops = [o for o in all_ops if (o.get("_meta") or {}).get("phase", "pre") == "pre"]
    post_ops = [o for o in all_ops if (o.get("_meta") or {}).get("phase") == "post"]

    # 1. Pre-flight: handle dedup auto-delete
    restored = restore_if_deleted(base, aid, args.token)

    # 2. Row ops first (they can change dp IDs in surviving rows; for remove they don't, but order is safer)
    row_result = apply_row_ops(base, aid, plan, args.token)
    print(f"  row_ops: applied={row_result['n']} status={row_result.get('status', '-')}")

    # 2b. Re-resolve "unmatched" prod fields whose missing rows were just added by add_empty_tuple ops.
    # The plan's `unmatched` list carries prod fields whose (path, row) had no uat counterpart
    # at plan-build time. After row reconciliation, those rows now exist with fresh dp_ids —
    # fetch the content again, look up the new dp_id by (path, row), and synthesize replace ops.
    extra_ops = []
    unmatched = plan.get("unmatched") or []
    no_match = [u for u in unmatched if u.get("reason") == "no_uat_match" and u.get("prod")]
    add_ops_present = any(
        o.get("op") in ("add", "add_empty_tuple")
        and (o.get("_meta") or {}).get("reason") == "row_only_in_prod"
        for o in plan.get("row_ops") or []
    )
    if no_match and add_ops_present:
        print(f"  re-resolving {len(no_match)} unmatched prod fields against post-row-ops content...")
        rstatus, rbody = http("GET", f"{base}/annotations/{aid}/content", args.token)
        if rstatus == 200:
            content = json.loads(rbody)
            uat_flat2 = walk_content(content if isinstance(content, list) else (content.get("content") or []))
            uat_idx2 = {}
            for f in uat_flat2:
                uat_idx2.setdefault((f["path"], f.get("row")), []).append(f)
            resolved = 0
            for um in no_match:
                pf = um["prod"]
                if pf.get("value") in (None, ""):
                    continue
                key = (pf["path"], pf.get("row"))
                cands = uat_idx2.get(key, [])
                if len(cands) != 1:
                    continue
                target = cands[0]
                pvs = pf.get("validation_sources") or []
                phase = "post" if any(s in ("data_matching", "rules", "connector") for s in pvs) else "pre"
                extra_ops.append({
                    "op": "replace",
                    "id": target["datapoint_id"],
                    "value": {
                        "value": pf["value"],
                        "page": pf.get("page"),
                        "position": pf.get("position"),
                    },
                    "_meta": {
                        "schema_id": pf.get("schema_id"),
                        "path": pf["path"],
                        "row": pf.get("row"),
                        "phase": phase,
                        "from_unmatched": True,
                    },
                })
                resolved += 1
            print(f"    re-resolved {resolved}/{len(no_match)} into new replace ops")
        else:
            print(f"    re-resolve content fetch failed: HTTP {rstatus}")

    if extra_ops:
        all_ops = list(all_ops) + extra_ops
        pre_ops = [o for o in all_ops if (o.get("_meta") or {}).get("phase", "pre") == "pre"]
        post_ops = [o for o in all_ops if (o.get("_meta") or {}).get("phase") == "post"]

    # 3. Pre-hook replace ops (inputs that hooks consume)
    if args.no_toggle:
        print(f"  --no-toggle set — applying ALL {len(all_ops)} replace ops in one phase (hooks will NOT re-fire)")
        rep_pre = apply_replace_ops(base, aid, all_ops, args.token, verify=not args.no_verify)
        print(f"  replace_ops (single-phase): ok={rep_pre['ok']}/{rep_pre['n_total']} fail={len(rep_pre['fail'])} mismatch={len(rep_pre['mismatch'])}")
        v_ok, v_err = trigger_validate(base, aid, args.token)
        print(f"  /content/validate: {'ok' if v_ok else 'fail: ' + str(v_err)}")
        rep_post = {"n_total": 0, "ok": 0, "fail": [], "mismatch": []}
        toggle_result = {"skipped": True}
    else:
        print(f"  pre-hook replace_ops ({len(pre_ops)} ops)...")
        rep_pre = apply_replace_ops(base, aid, pre_ops, args.token, verify=not args.no_verify)
        print(f"  pre-hook: ok={rep_pre['ok']}/{rep_pre['n_total']} fail={len(rep_pre['fail'])} mismatch={len(rep_pre['mismatch'])}")
        if rep_pre["fail"]:
            for f in rep_pre["fail"][:5]:
                print(f"    fail: {f}")

        # 4a. Force formula re-evaluation on the PATCHed inputs.
        v_ok, v_err = trigger_validate(base, aid, args.token)
        print(f"  /content/validate (pre-toggle): {'ok' if v_ok else 'fail: ' + str(v_err)}")

        # 4b. Status toggle to fire annotation_content.started → MDH/rules re-run on PATCHed inputs
        print(f"  status toggle (postponed → to_review) to fire hooks...")
        toggle_ok, toggle_err = status_toggle(base, aid, args.token)
        toggle_result = {"ok": toggle_ok, "error": toggle_err}
        if not toggle_ok:
            print(f"  WARN: status toggle failed: {toggle_err} — post-phase ops will overwrite stale hook outputs anyway")
        else:
            print(f"  waiting {args.toggle_wait}s for hooks to settle...")
            time.sleep(args.toggle_wait)

        # 5. Post-hook replace ops (hook outputs — overwrite whatever the hooks just produced)
        print(f"  post-hook replace_ops ({len(post_ops)} ops)...")
        rep_post = apply_replace_ops(base, aid, post_ops, args.token, verify=not args.no_verify)
        print(f"  post-hook: ok={rep_post['ok']}/{rep_post['n_total']} fail={len(rep_post['fail'])} mismatch={len(rep_post['mismatch'])}")
        if rep_post["fail"]:
            for f in rep_post["fail"][:5]:
                print(f"    fail: {f}")

        # 6. Final formula re-evaluation after post-hook ops.
        v_ok2, v_err2 = trigger_validate(base, aid, args.token)
        print(f"  /content/validate (final): {'ok' if v_ok2 else 'fail: ' + str(v_err2)}")

    out = {
        "plan_path": os.path.abspath(args.plan),
        "target_aid": aid,
        "base_url": args.base_url,
        "dedup_restored": restored,
        "row_ops": row_result,
        "replace_ops_pre": rep_pre,
        "status_toggle": toggle_result,
        "replace_ops_post": rep_post,
        "skipped_formula_targets": plan.get("skipped_formula_targets", []),
    }
    out_path = args.out or args.plan.rsplit(".json", 1)[0] + ".results.json"
    json.dump(out, open(out_path, "w"), indent=2, default=str)
    print(f"[apply] wrote {out_path}")

    # Exit non-zero on any failure or mismatch
    total_fail = len(rep_pre["fail"]) + len(rep_post["fail"])
    total_mismatch = len(rep_pre["mismatch"]) + len(rep_post["mismatch"])
    if total_fail or total_mismatch:
        sys.exit(2)


if __name__ == "__main__":
    main()
