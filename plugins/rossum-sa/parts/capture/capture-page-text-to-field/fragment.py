"""Park first/last page OCR text into schema fields.

Hook object requirements (JSON, not code):
  events   : ["annotation_content.initialize"]
  sideload : ["schemas"]            <- REQUIRED; TxScript.from_payload fails without it
  token_owner: <user id>            <- REQUIRED; supplies rossum_authorization_token

Enrichment hook: it FAILS OPEN. Every failure path writes empty strings and lets
the document through rather than blocking it.
"""
import json
import urllib.error
import urllib.request

from txscript import TxScript

MAX_CHARS = «max_chars»


def _api_get(base_url, path, token):
    req = urllib.request.Request(
        f"{base_url}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _page_count(base_url, annotation_id, token):
    """Page total, read from the paginated /pages envelope (no page bodies fetched)."""
    data = _api_get(
        base_url, f"/api/v1/pages?annotation={annotation_id}&page_size=1", token
    )
    return int(data["pagination"]["total"])


def _page_texts(base_url, annotation_id, token, page_numbers):
    """Whole-page text for the requested pages, keyed by page number.

    granularity=texts returns exactly one item per page holding the full page
    text (and, unlike every other granularity, no 'position' key) — so there is
    no line joining or reading-order reconstruction to get wrong.

    page_numbers is ALWAYS sent explicitly. Omitting it silently defaults to the
    first 20 pages, which would make the "last page" of a longer document be
    page 20. Results also come back ascending regardless of request order, so
    they are keyed by page_number rather than read positionally.
    """
    qs = ",".join(str(n) for n in page_numbers)
    data = _api_get(
        base_url,
        f"/api/v1/annotations/{annotation_id}/page_data"
        f"?granularity=texts&page_numbers={qs}",
        token,
    )
    out = {}
    for page in data.get("results", []):
        items = page.get("items") or [{}]
        out[page["page_number"]] = items[0].get("text", "")
    return out


def rossum_hook_request_handler(payload):
    t = TxScript.from_payload(payload)

    base_url = payload["base_url"]
    token = payload["rossum_authorization_token"]
    annotation_id = int(payload["annotation"]["url"].rstrip("/").rsplit("/", 1)[-1])

    first_text = ""
    last_text = ""
    try:
        total = _page_count(base_url, annotation_id, token)
        if total > 0:
            # A 1-page document asks for page 1 once; the two fields then match.
            wanted = sorted({1, total})
            texts = _page_texts(base_url, annotation_id, token, wanted)
            first_text = texts.get(1, "")
            last_text = texts.get(total, first_text)
        else:
            print(f"page-text capture: annotation {annotation_id} reports 0 pages")
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            ValueError, TimeoutError) as exc:
        # Fail open: this hook adds optional context. Raising would block the
        # document, and would retry retry_count + 1 times before settling.
        print(f"page-text capture failed for annotation {annotation_id}: {exc!r}")

    t.field.«first_page_field» = first_text[:MAX_CHARS]
    t.field.«last_page_field» = last_text[:MAX_CHARS]

    return t.hook_response()
