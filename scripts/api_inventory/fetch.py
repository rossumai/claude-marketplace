"""Fetch the public Rossum OpenAPI spec (no auth)."""
from __future__ import annotations

import json
import urllib.request

SPEC_URL = "https://rossum.app/api/docs/openapi/openapi-specs/openapi.json"


def fetch_spec(url: str = SPEC_URL) -> dict:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (trusted URL)
        return json.loads(resp.read().decode("utf-8"))
