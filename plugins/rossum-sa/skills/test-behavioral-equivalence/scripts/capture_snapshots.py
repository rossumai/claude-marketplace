#!/usr/bin/env python3
"""Phase 2c: Fetch annotation + content + document + automation_blocker for each corpus item.

Reads corpus.json (with items[].annotation_id; document_id and automation_blocker
URLs are auto-derived from the annotation response — corpus.json does NOT need to
pre-populate them).

Writes <id>.{annotation,content,document,blocker}.json into --out-dir.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def fetch(url, token, out_path):
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            with open(out_path, "wb") as f:
                f.write(data)
            return resp.status, len(data)
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:
        print(f"  ERR fetching {url}: {e}", file=sys.stderr)
        return -1, 0


def url_to_id(url):
    """Trim trailing /<id> from a full Rossum URL."""
    if not url:
        return None
    return url.rsplit("/", 1)[-1]


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--corpus", required=True, help="path to corpus.json")
    p.add_argument("--token", required=True, help="Rossum API token")
    p.add_argument("--base-url", default="https://elis.rossum.ai", help="Rossum API base URL")
    p.add_argument("--out-dir", required=True, help="output dir for <id>.*.json files")
    args = p.parse_args()

    corpus = json.load(open(args.corpus))
    base = args.base_url.rstrip("/") + "/api/v1"
    os.makedirs(args.out_dir, exist_ok=True)

    items = corpus.get("items") or corpus
    if isinstance(items, dict):
        items = list(items.values())

    summary = []
    for item in items:
        aid = item["annotation_id"]
        print(f"=== annotation {aid} ===")
        results = {}

        # Annotation first — we may parse blocker/document URL from its body.
        ann_path = os.path.join(args.out_dir, f"{aid}.annotation.json")
        status, size = fetch(f"{base}/annotations/{aid}", args.token, ann_path)
        print(f"  annotation: HTTP {status} {size}B")
        results["annotation"] = {"http": status, "size": size}

        # Parse the annotation we just wrote to learn document + automation_blocker URLs.
        ann = None
        if status == 200:
            try:
                ann = json.load(open(ann_path))
            except Exception as e:
                print(f"  WARN: could not parse annotation: {e}", file=sys.stderr)

        # Content
        content_path = os.path.join(args.out_dir, f"{aid}.content.json")
        status, size = fetch(f"{base}/annotations/{aid}/content", args.token, content_path)
        print(f"  content: HTTP {status} {size}B")
        results["content"] = {"http": status, "size": size}

        # Document — prefer corpus override, otherwise derive from annotation.document URL.
        did = item.get("document_id") or url_to_id((ann or {}).get("document"))
        if did:
            out_path = os.path.join(args.out_dir, f"{aid}.document.json")
            status, size = fetch(f"{base}/documents/{did}", args.token, out_path)
            print(f"  document: HTTP {status} {size}B")
            results["document"] = {"http": status, "size": size}

        # Automation blocker — prefer corpus override, otherwise derive from annotation.automation_blocker URL.
        # Only fetch if the annotation actually has a blocker (URL is non-null when blockers exist).
        bid = item.get("automation_blocker") or url_to_id((ann or {}).get("automation_blocker"))
        if bid:
            out_path = os.path.join(args.out_dir, f"{aid}.blocker.json")
            status, size = fetch(f"{base}/automation_blockers/{bid}", args.token, out_path)
            print(f"  blocker: HTTP {status} {size}B")
            results["blocker"] = {"http": status, "size": size}

        summary.append({"annotation_id": aid, "fetched": results})

    print(f"\nFetched {len(summary)} annotation snapshots into {args.out_dir}")


if __name__ == "__main__":
    main()
