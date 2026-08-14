#!/usr/bin/env python3
"""Verify the pinned Google Form entry IDs still exist on the live form.

Manual and network-using — NOT run in CI. Run it before releasing any change to
feedback-config.json and after every edit of the Google Form (editing a form can
change its entry IDs; see README-internal.md, "Re-pin the feedback form").

Exit 0: form unconfigured, or every pinned ID found. Exit 1: drift detected.
Stdlib-only, matching the repo's runtime contract philosophy.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / (
    "plugins/rossum-sa/skills/plugin-feedback/feedback-config.json"
)

FORM_URL_RE = re.compile(r"https://docs\.google\.com/forms/d/e/[\w-]+/formResponse")
VIEW_URL_RE = re.compile(r"https://docs\.google\.com/forms/d/e/[\w-]+/viewform")


def missing_entries(html: str, form_fields: dict[str, str]) -> dict[str, str]:
    """Pinned entries whose numeric ID does not appear anywhere in the form HTML.

    Entry IDs surface in FB_PUBLIC_LOAD_DATA_ as bare integers, so a
    digit-substring check is the render-agnostic way to test presence; a 9-10
    digit ID colliding with unrelated page content is vanishingly unlikely.
    """
    return {
        field: entry
        for field, entry in form_fields.items()
        if entry.removeprefix("entry.") not in html
    }


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    form_url = cfg.get("form_url", "")
    view_url = cfg.get("form_view_url", "")
    fields = cfg.get("form_fields", {})
    if not form_url and not fields:
        print("form not configured (form_url empty) — nothing to check")
        return 0
    if not FORM_URL_RE.fullmatch(form_url):
        print(f"BAD form_url shape: {form_url!r}")
        return 1
    if not VIEW_URL_RE.fullmatch(view_url):
        print(f"BAD form_view_url shape: {view_url!r}")
        return 1
    html = urllib.request.urlopen(view_url, timeout=30).read().decode("utf-8", "replace")
    missing = missing_entries(html, fields)
    for field, entry in sorted(missing.items()):
        print(f"MISSING on live form: {field} -> {entry}")
    print(f"checked {len(fields)} pinned IDs against the live form")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
