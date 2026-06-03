#!/usr/bin/env python3
"""Phase 2c: Normalize raw API snapshots into a flat, diffable shape.

Input  : <id>.annotation.json + <id>.content.json [+ <id>.blocker.json]
Output : <id>.normalized.json with flat fields, blocker items, and summary counts.
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from content_walker import walk, parse_blocker, load_content_nodes


def normalize_one(annotation_path, content_path, blocker_path=None):
    annotation = json.load(open(annotation_path))
    nodes = load_content_nodes(content_path)

    blocker = None
    if blocker_path and os.path.exists(blocker_path):
        blocker = json.load(open(blocker_path))

    fields = walk(nodes)
    blocker_items = parse_blocker(blocker)
    human_edited = [f for f in fields if "human" in f["validation_sources"]]

    return {
        "annotation_id": annotation.get("id"),
        "url": annotation.get("url"),
        "queue": annotation.get("queue"),
        "schema": annotation.get("schema"),
        "status": annotation.get("status"),
        "automated": annotation.get("automated"),
        "confirmed_at": annotation.get("confirmed_at"),
        "exported_at": annotation.get("exported_at"),
        "modified_at": annotation.get("modified_at"),
        "messages": annotation.get("messages") or [],
        "automation_blocker": blocker,
        "blocker_items": blocker_items,
        "fields": fields,
        "field_count": len(fields),
        "human_edited_count": len(human_edited),
        "human_edited_schema_ids": sorted(set(f["schema_id"] for f in human_edited)),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--annotation", help="path to <id>.annotation.json")
    p.add_argument("--content", help="path to <id>.content.json")
    p.add_argument("--blocker", help="optional path to <id>.blocker.json")
    p.add_argument("--out", help="output normalized.json path (single mode)")
    p.add_argument(
        "--dir",
        help="batch: process all *.annotation.json in this dir, write <id>.normalized.json next to them",
    )
    args = p.parse_args()

    if args.dir:
        annotations = sorted(glob.glob(os.path.join(args.dir, "*.annotation.json")))
        if not annotations:
            print(f"no *.annotation.json in {args.dir}", file=sys.stderr)
            sys.exit(1)
        for ann_path in annotations:
            aid = os.path.basename(ann_path).split(".")[0]
            content_path = os.path.join(args.dir, f"{aid}.content.json")
            blocker_path = os.path.join(args.dir, f"{aid}.blocker.json")
            if not os.path.exists(content_path):
                print(f"  skip {aid}: no content.json", file=sys.stderr)
                continue
            normalized = normalize_one(ann_path, content_path, blocker_path)
            out_path = os.path.join(args.dir, f"{aid}.normalized.json")
            json.dump(normalized, open(out_path, "w"), indent=2, sort_keys=True, default=str)
            print(
                f"{aid}: {normalized['field_count']} fields, "
                f"{normalized['human_edited_count']} human edits, "
                f"{len(normalized['blocker_items'])} blocker items"
            )
        return

    if not (args.annotation and args.content and args.out):
        print("either --dir or --annotation+--content+--out required", file=sys.stderr)
        sys.exit(2)
    normalized = normalize_one(args.annotation, args.content, args.blocker)
    json.dump(normalized, open(args.out, "w"), indent=2, sort_keys=True, default=str)
    print(f"{normalized['annotation_id']}: {normalized['field_count']} fields -> {args.out}")


if __name__ == "__main__":
    main()
