"""Read-only Rossum inventory of e-invoice documents.

Measured constraints that shape this module:
  * GET /annotations silently IGNORES created_at__gte / created_at__lte and
    einvoice=true — HTTP 200, unchanged total. It cannot be used for windowing.
  * GET /documents only honours an EXACT original_file_name; the __startswith and
    __contains variants are ignored and match every document in the org.
  * POST /annotations/search does filter properly, including
    original_file_name.$startsWith, and is therefore the primary query surface.
    Its pagination is a CURSOR: ?page=N is ignored, so `pagination.next` must be
    followed, re-POSTing the same body.
  * The search index is eventually consistent, so a just-imported annotation can
    lag by seconds. The caller's grace window absorbs that.
  * The search's default status coverage is neither everything nor a documented
    subset: an explicit status clause returns rows the default omits, while the
    clause itself can only name statuses this tool already models. Hence the two
    unioned searches in einvoice_index -- see its docstring.
"""

import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from match import ALL_STATUSES, ARRIVED_STATUSES, RossumAnn

FILENAME_PREFIX = "einvoice"
# The trailing `(?:_\d+)?` accepts an optional `_<digits>` suffix -- a
# MEASURED real-world variant (e.g. `einvoice<invoice id>_<annotation
# id>.pdf`), seen on a small number of documents out of a much larger sample
# and believed to come from a copy/re-upload path. The invoice id always
# comes from the FIRST captured group only; the suffix, when present, is a
# Rossum annotation id and is not captured. Without this, those documents
# were invisible to the index -- and if the same invoice showed up in the
# B2Brouter listing, the tool reported MISSING_IN_ROSSUM for a document that
# was sitting right there. Do not tighten this back to a bare `\.` before
# the extension without re-confirming the suffix no longer occurs live.
EINVOICE_FILENAME_RE = re.compile(
    rf"^{FILENAME_PREFIX}(\d+)(?:_\d+)?\.(pdf|xml)$", re.IGNORECASE
)

SEARCH_PATH = "/api/v1/annotations/search"
PAGE_SIZE = 500
# The two systems' clocks are independent; index a little further back than the
# reporting window so an invoice at a window edge still finds its annotation.
INDEX_LOOKBACK = timedelta(hours=24)


class RossumError(RuntimeError):
    pass


class RossumClient:
    def __init__(
        self,
        token: str,
        base_url: str,
        transport: Callable[[str], dict] | None = None,
        searcher: Callable[[str, dict], dict] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._transport = transport or self._get
        self._searcher = searcher or self._search
        # Opt-in only, via --relax-x509-strict in recon.py. None (the default)
        # means: do not construct a context at all here -- let urlopen() use
        # its own, so the no-flag path is byte-for-byte what it was before
        # this parameter existed. See recon.py's context builder for what a
        # non-None value actually relaxes (and, just as importantly, what it
        # does not).
        self._ssl_context = ssl_context

    # --- HTTP: exactly one GET helper and one guarded search helper -----------

    def _request(self, url: str, body: bytes | None, method: str) -> dict:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        # context is only added to the call when a caller supplied one -- not
        # passed as context=None -- so the no-flag path calls urlopen() with
        # the exact same arguments it always has.
        urlopen_kwargs = {"timeout": 90}
        if self._ssl_context is not None:
            urlopen_kwargs["context"] = self._ssl_context
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, **urlopen_kwargs) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RossumError(f"{method} {url} -> HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RossumError(f"{method} {url} failed: {exc}") from exc
            except (TimeoutError, http.client.IncompleteRead, OSError) as exc:
                # I7: a stall or a truncated body during json.load(response)
                # raises here, NOT as an HTTPError/URLError -- a bare
                # TimeoutError, or http.client.IncompleteRead, from reading the
                # response after the request itself succeeded. Uncaught, it
                # escaped the CLI's narrow try and aborted the whole run with a
                # traceback. Treated as retryable, like a 5xx, then raised as
                # the module's domain error.
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RossumError(f"{method} {url} failed: {exc!r}") from exc
        raise RossumError(f"{method} {url} exhausted retries")

    def _get(self, path: str) -> dict:
        url = path if path.startswith("http") else f"{self._base}{path}"
        return self._request(url, None, "GET")

    def _search(self, path: str, body: dict) -> dict:
        """POST, but read-only: guarded to the search endpoint alone.

        The guard is a startswith check on the request's PATH component, not
        substring containment: containment would have accepted any URL that
        merely mentioned the search path anywhere, including inside a query
        string (`/api/v1/queues?x=/api/v1/annotations/search`), which is a
        POST to a different endpoint entirely. The path has to match from its
        start; the trailing part is only ever the search cursor's query string.
        """
        url = path if path.startswith("http") else f"{self._base}{path}"
        if not urllib.parse.urlsplit(url).path.startswith(SEARCH_PATH):
            raise RossumError(f"refusing to POST to {path!r}: not the search endpoint")
        return self._request(url, json.dumps(body).encode(), "POST")

    # --- queries -------------------------------------------------------------

    def list_hooks(self) -> list[dict]:
        """All hooks in the organization, for channel discovery."""
        hooks: list[dict] = []
        path = "/api/v1/hooks?page_size=200"
        while path:
            payload = self._transport(path)
            hooks.extend(payload.get("results", []))
            path = (payload.get("pagination") or {}).get("next")
        return hooks

    def einvoice_index(
        self, queue_ids: Sequence[int], since: datetime
    ) -> dict[str, list[RossumAnn]]:
        """Map einvoice id -> its annotations, for the given queues and window.

        Filters on the FILENAME, not on the einvoice flag: a `failed_import` XML
        twin is not flagged, and those rows are exactly the ones that matter.

        TWO searches are issued per window and unioned by annotation id, and
        BOTH are needed -- do not "optimise" one away. Query (b) carries an
        explicit `status.$in` clause and query (a) carries no status clause at
        all. (b) is measured necessary: the default search omits some statuses
        (one queue returned 17,897 rows with the clause and 17,891 without).
        (a) is necessary because the clause can only list statuses this tool
        already knows, so any status outside those sets would never be
        RETURNED, and the classifier's UNKNOWN_STATUS guard would never see it
        -- an annotation in an unmodelled state would silently read as "no
        annotation" or, beside a healthy sibling, as plain `ok`: a verdict of
        "fine" for a state the tool does not understand. Enumerating the
        platform's status enum here is not an option: it cannot be
        authoritatively listed from outside, and even the platform's own
        tooling omits statuses that occur live. So the query is asked both
        ways, and the union is what the classifier sees.
        """
        cutoff = (since - INDEX_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
        base_clauses = [
            {"queue": {"$in": [
                f"{self._base}/api/v1/queues/{queue_id}" for queue_id in queue_ids
            ]}},
            {"original_file_name": {"$startsWith": FILENAME_PREFIX}},
            {"created_at": {"$gte": cutoff}},
        ]
        queries = (
            # (a) no status clause: nothing can be invisible for being in a
            # status this tool has never heard of.
            base_clauses,
            # (b) the explicit clause: measured to return rows the default
            # search leaves out.
            base_clauses + [{"status": {"$in": list(ALL_STATUSES)}}],
        )

        index: dict[str, list[RossumAnn]] = {}
        seen: set[int] = set()

        for clauses in queries:
            body = {"query": {"$and": clauses}}
            path = f"{SEARCH_PATH}?page_size={PAGE_SIZE}&sideload=documents"
            while path:
                payload = self._searcher(path, body)
                documents = {d["id"]: d for d in payload.get("documents", [])}
                for annotation in payload.get("results", []):
                    # The two queries overlap heavily; an annotation returned
                    # by both must appear exactly once.
                    if annotation["id"] in seen:
                        continue
                    # Skip annotations with missing or null document field.
                    if not annotation.get("document"):
                        continue
                    document = documents.get(
                        int(str(annotation["document"]).rstrip("/").rsplit("/", 1)[-1]), {}
                    )
                    match = EINVOICE_FILENAME_RE.match(
                        document.get("original_file_name") or ""
                    )
                    if not match:
                        continue
                    seen.add(annotation["id"])
                    index.setdefault(match.group(1), []).append(
                        RossumAnn(
                            annotation_id=annotation["id"],
                            status=annotation["status"],
                            filename=document["original_file_name"],
                            einvoice_flag=bool(annotation.get("einvoice")),
                            created_at=annotation["created_at"],
                        )
                    )
                # ?page=N is ignored by this endpoint; the cursor in `next` is
                # the only way forward.
                path = (payload.get("pagination") or {}).get("next")

        return index

    def has_surviving_original(self, invoice_number: str) -> bool:
        """True if a healthy annotation exists ANYWHERE in the organization
        whose extracted content carries this invoice number.

        Used to decide whether a DELETED row EARNS the DELETED_AS_DUPLICATE
        label -- this tool never asserts a duplicate exists, it confirms
        one. The surviving original may have arrived under a completely
        DIFFERENT e-invoice id (a re-import, a manual upload, a different
        channel), which is exactly why pairing by e-invoice id alone can
        never catch a loss hiding in this bucket: `field.document_id.string`
        is the invoice NUMBER as extracted from the document's own content,
        shared regardless of which id or queue the surviving copy landed
        under.

        MEASURED: a single-clause query on `field.document_id.string` alone
        -- `{"query": {"field.document_id.string": {"$eq": ...}}}` -- gets
        HTTP 400 from this endpoint, every time, with no partial result;
        the exact same trap as GET /documents' filename-only query. The
        caller-side effect is worse than a loud error: recon.py's per-row
        try/except around this call treats a raised RossumError as "the
        check did not complete" and leaves the row unverified rather than
        aborting the run -- which is the right behaviour for a real
        transient failure, but on a live run it silently swallowed a 400 on
        EVERY row (0 of 222 verified) with nothing looking wrong in the
        summary beyond an unusually large "not verified" count. The fix,
        MEASURED working: a two-clause `$and`, pairing the content clause
        with an explicit `status.$in` naming the FULL status list
        (`ALL_STATUSES`, not just ARRIVED_STATUSES -- a status outside the
        healthy set still has to come back so this function can tell
        "found a deleted copy" apart from "found nothing at all"). Do not
        collapse this back to a single clause.

        Deliberately NOT scoped to any particular queue, unlike
        einvoice_index() -- the whole point is to look beyond the channel's
        own queues for a survivor elsewhere in the organization. Uses the
        SAME guarded search helper as einvoice_index(), never a new POST
        path. Returns True on the first healthy (ARRIVED_STATUSES) hit
        rather than paging to exhaustion once one is found; only a fully
        paged-through result set with no healthy hit at all counts as "not
        found" (an empty result set included).
        """
        body = {"query": {"$and": [
            {"field.document_id.string": {"$eq": invoice_number}},
            {"status": {"$in": list(ALL_STATUSES)}},
        ]}}
        path = f"{SEARCH_PATH}?page_size={PAGE_SIZE}"
        while path:
            payload = self._searcher(path, body)
            for annotation in payload.get("results", []):
                if annotation.get("status") in ARRIVED_STATUSES:
                    return True
            path = (payload.get("pagination") or {}).get("next")
        return False

    def lookup_einvoice(self, einvoice_id: str) -> list[RossumAnn]:
        """Exact-filename fallback for an id the search did not return.

        Catches documents whose annotation was purged, and documents that landed
        in a queue outside the discovered set.
        """
        found: list[RossumAnn] = []
        for extension in ("pdf", "xml"):
            filename = f"{FILENAME_PREFIX}{einvoice_id}.{extension}"
            query = urllib.parse.urlencode({"original_file_name": filename})
            payload = self._transport(f"/api/v1/documents?{query}")
            for document in payload.get("results", []):
                for annotation_url in document.get("annotations", []):
                    annotation = self._transport(annotation_url)
                    found.append(
                        RossumAnn(
                            annotation_id=annotation["id"],
                            status=annotation["status"],
                            filename=document["original_file_name"],
                            einvoice_flag=bool(annotation.get("einvoice")),
                            created_at=annotation["created_at"],
                        )
                    )
        return found
