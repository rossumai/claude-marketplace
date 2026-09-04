"""Read-only B2Brouter client, supporting both API generations B2Brouter runs
concurrently.

B2Brouter selects the API generation per request via the `X-B2B-API-Version`
header -- not a property of the key -- defaulting, when the header is
omitted, to whatever version the account GROUP is configured for. A group
whose default is the newer generation rejects every legacy-host call with
`400 {"error": {"code": "api_version_subdomain_mismatch"}}`. This client
therefore sends the header explicitly on EVERY request, never relying on a
group's default, and derives host/path/envelope/field-name differences from
a small per-version profile table (`API_PROFILES` below) rather than
hardcoding one generation.

Everything below was measured live against BOTH versions, with two different
keys, on two different account groups:

    LEGACY (`2025-01-01`, the importer extension's own version, and this
    client's default -- matches the behaviour this tool has always had):
      host:   https://app.b2brouter.net      (staging: app-staging.)
      list:   GET /projects/{ACCOUNT_ID}/received.json?...
                -> flat envelope {"invoices": [...], "total_count", "offset", "limit"}
      sender: row["client"]["name"]
      detail: GET /invoices/{ID}.json -> invoice.project.id is the account;
              invoice.account is null
      dates:  no fractional seconds, e.g. 2026-08-27T12:00:32Z

    NEW (`2026-06-26`):
      host:   https://api.b2brouter.net      (staging: api-staging.)
      list:   GET /accounts/{ACCOUNT_ID}/invoices?...
                -> {"invoices": [...], "meta": {"total_count", "offset", "limit"}}
      sender: row["contact"]["name"]; list rows carry NO account/project
              field at all -- the account comes from the request, not the row
      detail: GET /invoices/{ID} (no .json) -> invoice.account.id is the
              account; invoice.project is null
      dates:  millisecond fractions, e.g. 2026-08-27T12:00:32.000Z

Shared by both, regardless of version:
  - `GET /accounts?limit=500` -> a FLAT envelope on BOTH generations (the
    new generation's invoice listing is the odd one out, not `/accounts`).
  - Listing received invoices requires `type=ReceivedInvoice&ack=true&limit=
    N&offset=M` -- identical query parameters on both hosts/paths. `ack` is
    REQUIRED, not optional: omitting it entirely (or sending `ack=false`)
    does not return the full index -- it returns ONLY the importer's
    pending/unacknowledged queue, which for a mostly-processed account is a
    handful of rows out of thousands, on BOTH versions. `ack=true` was
    verified NOT to mutate anything: three consecutive `ack=true` list calls
    left a pending invoice's `ack_at` still None and its `updated_at`
    unchanged, and a follow-up `ack=false` call still returned that same
    invoice. `ack=false` must never be sent by this tool.
  - `limit` is clamped at 500 server-side and the CLAMPED value is echoed
    back, on both versions -- see the page-fullness reasoning below.
  - List rows carry `ack_at: null` and (on the legacy host) `from_net: null`
    regardless of the truth, on both versions; only the per-invoice DETAIL
    response populates them.
  - Detail responses are WRAPPED as `{"invoice": {...}}` on both versions.
  - Wrong-generation host/path (legacy path hit on the new host, or vice
    versa) -> `400 api_version_subdomain_mismatch`. An unrecognised version
    string in the header -> `400 invalid_api_version`. A key from the wrong
    environment (e.g. a staging key against a production host) ->
    `401 invalid_api_key`. `B2bError.code` (see below) exposes the parsed
    `error.code` from any of these so callers can branch on them without
    string-matching.

This endpoint's `date_from`/`date_to` filters key on the invoice's ISSUE
date, not its arrival, and issue-to-arrival skew is unbounded in principle
(a single-day server-side query has been observed returning rows created
multiple days later). Reconciliation windows on arrival, so this client
deliberately never sends those filters: it pages every account's full index
and applies the [since, until] window client-side against created_at, which
is the sole authority for "did this invoice arrive in the window".
"""

import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from match import B2bInvoice

PAGE_SIZE = 500

# Hard backstop on pagination: no server response, however malformed, may spin
# this tool forever. A real account should never come close to this many pages
# at PAGE_SIZE=500 (5,000,000 rows); it exists purely to fail loudly instead of
# hanging.
MAX_PAGES = 10_000

# The two measured API generations. LEGACY matches what the Rossum importer
# extension itself sends and is this client's default, so an operator who
# never touches version selection sees exactly today's behaviour.
LEGACY_API_VERSION = "2025-01-01"
NEW_API_VERSION = "2026-06-26"


@dataclass(frozen=True)
class ApiProfile:
    """Everything that differs between B2Brouter API generations.

    Adding a THIRD generation means adding one more entry to API_PROFILES
    with these facts -- nothing else in this module should need to change on
    the strength of a version bump alone. `host_own`/`host_other` are
    parallel tuples of host-family markers (staging variant first, since
    "app-staging." contains "app." as a substring and must be matched before
    it -- see `_rehost`).
    """
    version: str
    host_own: tuple[str, ...]
    host_other: tuple[str, ...]
    list_path: Callable[[str], str]
    list_envelope: str  # "flat" or "meta"
    sender_field: str  # "client" or "contact"
    detail_path: Callable[[str], str]
    # The field tried FIRST for the owning account on a detail lookup ("project"
    # or "account"). The other of the two is always tried as a fallback,
    # regardless of profile -- see B2brouterClient._account_id_from_invoice --
    # because both generations were measured to carry the unused field as a
    # real, always-null key rather than omitting it.
    detail_account_field: str


API_PROFILES: dict[str, ApiProfile] = {
    LEGACY_API_VERSION: ApiProfile(
        version=LEGACY_API_VERSION,
        host_own=("app-staging.", "app."),
        host_other=("api-staging.", "api."),
        list_path=lambda account_id: f"/projects/{account_id}/received.json",
        list_envelope="flat",
        sender_field="client",
        detail_path=lambda einvoice_id: f"/invoices/{einvoice_id}.json",
        detail_account_field="project",
    ),
    NEW_API_VERSION: ApiProfile(
        version=NEW_API_VERSION,
        host_own=("api-staging.", "api."),
        host_other=("app-staging.", "app."),
        list_path=lambda account_id: f"/accounts/{account_id}/invoices",
        list_envelope="meta",
        sender_field="contact",
        detail_path=lambda einvoice_id: f"/invoices/{einvoice_id}",
        detail_account_field="account",
    ),
}


def other_api_version(version: str) -> str:
    """The other known profile's version string -- used by recon.py's
    auto-detection to retry once against the alternate generation. Raises
    ValueError for an unrecognised version rather than guessing."""
    others = [v for v in API_PROFILES if v != version]
    if version not in API_PROFILES or len(others) != 1:
        raise ValueError(f"no single alternate profile for version {version!r}")
    return others[0]


def _rehost(base_url: str, profile: ApiProfile) -> str:
    """Swap `base_url`'s host onto `profile`'s own family (app./api.,
    including the staging variants), derived from whatever host the hook's
    `b2b_router_base_url` setting configured -- which always names the
    LEGACY host, since that is what the importer extension itself uses.
    Left unchanged if it already matches this profile's family, or if it
    matches neither known family at all (an unrecognised host shape must
    fail loudly downstream, not be silently rewritten here).
    """
    for own, other in zip(profile.host_own, profile.host_other):
        if own in base_url:
            return base_url
        if other in base_url:
            return base_url.replace(other, own)
    return base_url


def _error_code(exc: urllib.error.HTTPError) -> str | None:
    """The parsed `error.code` from an HTTPError's JSON body, e.g.
    `api_version_subdomain_mismatch` / `invalid_api_version` /
    `invalid_api_key` -- or None when the body isn't that shape (already
    consumed, not JSON, or missing the key). Never raises: a body this
    client cannot parse is simply an error with no code, not a reason to
    obscure the original HTTP failure.
    """
    try:
        body = json.loads(exc.read())
    except Exception:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


class B2bError(RuntimeError):
    """Raised for any B2Brouter request failure.

    `code` is the parsed `error.code` from a JSON error body (e.g.
    `api_version_subdomain_mismatch`, `invalid_api_version`,
    `invalid_api_key`) when the response carried one, else None -- callers
    branch on this attribute instead of substring-matching `str(exc)`. The
    message text is unchanged from before this attribute existed.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InvoiceRef:
    """The minimal identity of one invoice, as looked up by id.

    Deliberately NOT a `B2bInvoice`: that type carries the full
    received.json row shape (and I4's skip/validation rules for a LISTING).
    This is only the two facts per-account attribution needs from a single
    by-id lookup -- which account owns this id, and when it arrived -- kept
    separate so a by-id lookup's narrower, differently-shaped response can
    never be mistaken for a listing row.
    """
    account_id: str
    # None when the response carried no usable `created_at` -- a lookup that
    # otherwise succeeded (a real account id was found) but can't answer
    # "when did this arrive". The caller treats that as unknown arrival, not
    # as evidence of anything -- see recon.py's attribution logic.
    created_at: str | None
    # None when the response carried no usable `ack_at` -- either the field
    # was absent/non-string, or (the common, legitimate case) the invoice
    # genuinely has not been acknowledged yet. This is the SAME single GET
    # used for attribution and arrival, not a second request: the listing
    # endpoint (received.json) always returns `ack_at: null` regardless of
    # the true state (see the module docstring's ack=true note and
    # recon.py's exception-row backfill), so this by-id lookup is the only
    # place this tool can ever read a real acknowledgement timestamp.
    ack_at: str | None = None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class B2brouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        transport: Callable[[str], dict] | None = None,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        ssl_context: ssl.SSLContext | None = None,
        api_version: str = LEGACY_API_VERSION,
    ) -> None:
        if api_version not in API_PROFILES:
            raise ValueError(
                f"unknown B2Brouter API version {api_version!r} "
                f"(known: {sorted(API_PROFILES)})"
            )
        self._key = api_key
        self._api_version = api_version
        self._profile = API_PROFILES[api_version]
        # `base_url` is whatever the hook's `b2b_router_base_url` setting
        # configured, which always names the LEGACY host -- that is what the
        # importer extension itself sends. Rehosted onto THIS profile's own
        # family so a client pinned or auto-detected to the new generation
        # still talks to the right host without the caller needing to know
        # the swap happened.
        self._base = _rehost(base_url.rstrip("/"), self._profile)
        self._page_size = page_size
        self._max_pages = max_pages
        self._transport = transport or self._get
        # Opt-in only, via --relax-x509-strict in recon.py. None (the default)
        # means: do not construct a context at all here -- let urlopen() use
        # its own, so the no-flag path is byte-for-byte what it was before
        # this parameter existed.
        self._ssl_context = ssl_context
        # I4: account id -> number of source rows this client had to skip
        # because they could not be identified (no id, or no created_at).
        # Read by the CLI, which warns about them: a skipped row is an invoice
        # that exists on the network and would otherwise vanish from the
        # evidence file without a trace. Keyed by account because one client
        # is reused for every account a single key can see.
        self.skipped_rows: dict[str, int] = {}

    def _get(self, path: str) -> dict:
        """The ONLY HTTP verb in this module."""
        url = f"{self._base}{path}"
        # The API generation is a per-REQUEST header, not a property of the
        # key or the host -- sent explicitly on every call so behaviour never
        # depends on the account group's configured default (see module
        # docstring).
        request = urllib.request.Request(
            url,
            headers={
                "X-B2B-API-Key": self._key,
                "X-B2B-API-Version": self._api_version,
            },
            method="GET",
        )
        # context is only added to the call when a caller supplied one -- not
        # passed as context=None -- so the no-flag path calls urlopen() with
        # the exact same arguments it always has.
        urlopen_kwargs = {"timeout": 60}
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
                raise B2bError(f"GET {url} -> HTTP {exc.code}", code=_error_code(exc)) from exc
            except urllib.error.URLError as exc:
                # Detect certificate verification errors and give helpful guidance.
                if isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise B2bError(
                        f"SSL certificate verification failed for {url} "
                        f"({exc.reason}) -- likely a corporate TLS-inspecting "
                        "proxy: if its CA is not yet trusted, set SSL_CERT_FILE "
                        "to a bundle that includes it. If the CA is already "
                        "trusted but the error mentions key usage, the CA "
                        "lacks the Key Usage extension Python's strict X.509 "
                        "checks require by default -- use --relax-x509-strict "
                        "instead."
                    ) from exc
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise B2bError(f"GET {url} failed: {exc}") from exc
            except (TimeoutError, http.client.IncompleteRead, OSError) as exc:
                # I7: a stall or a truncated body during json.load(response)
                # raises here, NOT as an HTTPError/URLError -- a bare
                # TimeoutError, or http.client.IncompleteRead, from reading the
                # response after the request itself succeeded. Uncaught, one
                # flaky read on account 18 of 20 escaped the per-account
                # handler and lost the entire run to a traceback instead of
                # marking a single account unverified. Treated as retryable,
                # like a 5xx, then raised as the module's domain error.
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise B2bError(f"GET {url} failed: {exc!r}") from exc
        raise B2bError(f"GET {url} exhausted retries")

    def visible_account_ids(self) -> set[str]:
        """Accounts this key can see, used to map keys to accounts.

        Raises B2bError if the account list may be truncated (returned accounts
        equal the requested limit).
        """
        limit = 500
        payload = self._transport(f"/accounts?limit={limit}")
        # I3: require the key. `.get("accounts", [])` turned any 200 response
        # of an unexpected shape -- a captive proxy page, an envelope change --
        # into "this key sees no accounts", which reads as a coverage problem
        # instead of the protocol failure it actually is.
        if "accounts" not in payload:
            raise B2bError(
                "Account listing response has no 'accounts' key "
                f"(keys present: {sorted(payload)}). Refusing to treat an "
                "unrecognised response as an empty account list."
            )
        accounts = payload["accounts"]
        if not isinstance(accounts, list):
            raise B2bError(
                f"Account listing 'accounts' is {type(accounts).__name__}, not a list."
            )

        # Detect truncation: if we got exactly as many accounts as we requested,
        # the list may be incomplete.
        if len(accounts) >= limit:
            raise B2bError(
                f"Account listing may be truncated: received {len(accounts)} accounts "
                f"at limit {limit}. This key may have visibility to more accounts."
            )

        return {str(a["id"]) for a in accounts}

    def get_invoice(self, einvoice_id: str) -> InvoiceRef | None:
        """Look up ONE received invoice by id and return which account owns
        it and when it arrived, or None if this key cannot see any such
        invoice.

        Used for per-account attribution of a Rossum-side id that no listed
        invoice matched: `received_invoices()` is scoped per account and can
        only tell you what one already-known account listed, but attribution
        needs the reverse -- given just an id, which account owns it, and (to
        tell a genuine listing gap apart from a re-import of an invoice that
        arrived long before the reporting window) when it arrived. A single
        GET, no pagination, no listing -- all three facts, including
        `ack_at`, come out of the one response.

        This is also the ONLY path this tool has to a real acknowledgement
        timestamp: `received.json` (the listing endpoint) returns `ack_at:
        null` for every invoice regardless of the truth (see the module
        docstring), so recon.py reuses this exact same by-id call -- never a
        second request -- to backfill `acked_at` for exception rows after the
        join.

        Response shape MEASURED directly against BOTH hosts' live responses:
        the invoice comes back WRAPPED as `{"invoice": {"id": ..., "project":
        {"id": ..., "name": ...}, "account": null, "created_at": ..., ...}}`
        on the legacy generation -- the account/project id lives at
        `invoice.project.id`; `invoice.account` is a real key but always
        `null`. The new generation is the mirror image: `invoice.account.id`
        is the account and `invoice.project` is null (or absent). Both are
        wrapped the same way. This client reads whichever of the two fields
        `self._profile.detail_account_field` says is primary for the active
        version, falling back to the other one -- so a payload that happens
        to carry the OTHER generation's shape (e.g. a version mismatch that
        the server tolerated instead of rejecting) is still read correctly
        rather than raising. A payload with neither field populated, on
        either shape, raises rather than being silently read as "no
        account" -- see `_account_id_from_invoice` below. `created_at` is
        read straight off that same invoice object; if it is absent or not a
        string, the returned `InvoiceRef.created_at` is None rather than
        raising -- an account id without a usable arrival date is still
        useful to the caller (it just can't be used to detect a pre-window
        re-import). `ack_at` is read the same way, off the same invoice
        object: if it is absent or not a string, `InvoiceRef.ack_at` is
        None. That None is genuinely ambiguous in isolation (unacknowledged
        vs. a field this host doesn't populate at all) -- but the caller
        only ever reaches into `ack_at` after this lookup already SUCCEEDED
        (a real invoice object came back), so None here means "this
        invoice, confirmed to exist, has no acknowledgement timestamp" -- as
        distinct from a lookup that failed or 404d entirely, which the
        caller marks differently.

        A 404 means this key/account group cannot see this invoice id and
        returns None, exactly like a per-id fallback lookup that legitimately
        finds nothing. Any other failure (network, non-2xx, an unrecognised
        response shape) raises B2bError so the caller can tell "checked, not
        found" apart from "the check itself failed" -- and, in particular,
        never silently count a real failure as proof no account owns the id.
        """
        try:
            payload = self._transport(self._profile.detail_path(einvoice_id))
        except B2bError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        # Wrapped on both generations (measured) -- unwrap only when
        # "invoice" is actually present and is itself an object, so a
        # payload that merely lacks the key is read as the invoice object
        # directly rather than as garbage.
        invoice = payload["invoice"] if isinstance(payload.get("invoice"), dict) else payload
        account_id = self._account_id_from_invoice(invoice, einvoice_id)
        created_at = invoice.get("created_at")
        ack_at = invoice.get("ack_at")
        return InvoiceRef(
            account_id=account_id,
            created_at=created_at if isinstance(created_at, str) else None,
            ack_at=ack_at if isinstance(ack_at, str) else None,
        )

    def _account_id_from_invoice(self, invoice: dict, einvoice_id: str) -> str:
        """The owning account id off a detail-lookup invoice object, trying
        this client's profile's primary field first (`project` for legacy,
        `account` for the new generation) and the other one as a fallback --
        see `get_invoice`'s docstring for why the fallback exists. Raises
        B2bError, naming the invoice id and both field names, if neither is
        a usable object -- never silently reads that as "no account".
        """
        primary_field = self._profile.detail_account_field
        fallback_field = "account" if primary_field == "project" else "project"
        for field in (primary_field, fallback_field):
            candidate = invoice.get(field)
            if isinstance(candidate, dict) and candidate.get("id") is not None:
                return str(candidate["id"])
        raise B2bError(
            f"Invoice {einvoice_id} lookup response has no usable "
            f"'{primary_field}' or '{fallback_field}' object "
            f"(keys present: {sorted(invoice)})."
        )

    def received_invoices(
        self, account_id: str, *, since: datetime, until: datetime
    ) -> list[B2bInvoice]:
        """Received invoices that ARRIVED in [since, until] for one account.

        Calls the active profile's list endpoint (`/projects/{account_id}/
        received.json` on the legacy generation, `/accounts/{account_id}/
        invoices` on the new one) with the SAME query parameters on both --
        `type=ReceivedInvoice&ack=true&limit=N&offset=M`. ack=true is
        required to see the full index rather than just the importer's
        pending queue on EITHER generation (see the module docstring for the
        measured evidence that this does not mutate anything); ack=false
        must never be sent. `total_count`/`limit` are read from the active
        profile's envelope location -- top-level for the flat (legacy)
        envelope, under `meta` for the new one -- via `_list_meta_object`
        below; every check on them is otherwise identical for both.

        The listing order is unspecified, so the account is paged in full and the
        window applied client-side on created_at -- this endpoint's date_from/
        date_to filters key on issue date, not arrival, so they are deliberately
        never used here (see module docstring). Raises B2bError on failure so
        the caller can mark the account unverified rather than treat an empty
        result as a clean one.

        Pagination ends ONLY on a short (or empty) page, and fullness is
        measured against the limit the SERVER echoes back — never against the
        page size we asked for. Legacy list endpoints commonly clamp the page
        size: a server that clamps 500 to 100 returns full 100-row pages, and
        comparing 100 against the requested 500 would read the very first page
        as short and stop, reporting 100 of 250 invoices as the complete
        account. A clamped limit means the request was not honoured, so it
        raises B2bError naming both numbers — an account reported UNVERIFIED is
        far better than one reported complete out of truncated data. The same
        reasoning makes `limit` itself REQUIRED, exactly like `invoices`: a
        server that clamps the page size WITHOUT echoing `limit` at all
        reaches the identical failure through a missing key instead of a
        smaller value, and falling back to the requested page size in that
        case would end the loop on the first clamped page with no error.

        A server-declared total_count is NEVER used to end the loop. A server
        that UNDER-declares (says 2, actually holds more) would otherwise
        truncate the authoritative side of the reconciliation on a full page.
        The declared count is still captured — the first time it appears on ANY
        page, since a server may omit it on page one — and used purely as a
        sanity check: if it is materially GREATER than the number of rows
        walked, that means truncation and raises B2bError naming both numbers.
        Walking MORE rows than declared is harmless (declared counts can
        undercount) and does not raise.

        A hard page-count backstop (self._max_pages) guarantees the loop cannot
        spin forever against a server that never returns a short page.

        Rows that cannot be identified (no id, or no created_at) are skipped --
        nothing in Rossum can be joined to them -- but they are COUNTED, in
        self.skipped_rows[account_id], so the CLI can warn about them. Such a
        row is an invoice that exists on the network and would otherwise
        disappear from the evidence file with no trace at all.
        """
        invoices: list[B2bInvoice] = []
        offset = 0
        total_declared: int | None = None
        page_count = 0
        skipped = 0
        self.skipped_rows[str(account_id)] = 0

        while True:
            page_count += 1
            if page_count > self._max_pages:
                raise B2bError(
                    f"Account {account_id}: exceeded {self._max_pages} pages while "
                    "listing received invoices without reaching the end of results. "
                    "Aborting to avoid an endless loop."
                )

            query = urllib.parse.urlencode({
                "type": "ReceivedInvoice",
                "ack": "true",
                "limit": self._page_size,
                "offset": offset,
            })
            path = self._profile.list_path(account_id)
            payload = self._transport(f"{path}?{query}")
            # I3: require the key. `.get("invoices", [])` turned any 200
            # response of an unexpected shape -- a captive proxy page, an
            # envelope change -- into a clean, empty, error-free account.
            # `invoices` itself is always top-level, on BOTH envelope kinds --
            # only `limit`/`total_count` move under `meta` on the new one.
            if "invoices" not in payload:
                raise B2bError(
                    f"Account {account_id}: listing response has no 'invoices' key "
                    f"(keys present: {sorted(payload)}). Refusing to treat an "
                    "unrecognised response as an empty account."
                )
            rows = payload["invoices"]
            if not isinstance(rows, list):
                raise B2bError(
                    f"Account {account_id}: 'invoices' is {type(rows).__name__}, "
                    "not a list."
                )
            meta_obj = self._list_meta_object(payload)

            # Capture and validate total_count the first time it appears on
            # any page -- a server may omit it on page one and include it
            # later, and falling back to the MAX_PAGES backstop in that case
            # would waste thousands of requests.
            #
            # RESIDUAL CRITICAL, once real: an earlier version treated a
            # declared `total_count: 0` as an end-of-data signal and returned
            # immediately, even when the very same page was FULL (rows ==
            # page_size). A server that declares 0 while a full page keeps
            # arriving is not empty, it just hasn't told you the real count
            # yet -- stopping there silently truncated the account after page
            # one. `total_declared` is captured here purely for the mismatch
            # check below (`usable_total = ... and total_declared > 0`); it is
            # NEVER read as a reason to end the loop -- only a short/empty
            # page (see `short_page` below) does that. The dedicated
            # regression test for this was pruned as a duplicate of the
            # general "declared count is never a stop signal" contract; this
            # comment is what is left to explain why total_declared plays no
            # part in the loop's own stop condition.
            if total_declared is None and "total_count" in meta_obj:
                total_declared = meta_obj["total_count"]
                # Guard against non-numeric total_count (bool is a subclass of
                # int in Python but is never a legitimate count here).
                if not isinstance(total_declared, int) or isinstance(total_declared, bool):
                    raise B2bError(
                        f"Invalid total_count: expected int, got {type(total_declared).__name__} "
                        f"({total_declared!r})"
                    )

            # C1: the echoed `limit` is the authority for page fullness. A
            # legacy endpoint that silently clamps the page size returns full
            # pages of its own smaller size; measuring those against the size
            # we REQUESTED reads the first one as short and ends the listing
            # early, reporting a truncated account as a complete one.
            #
            # `limit` is REQUIRED, exactly like `invoices` above: falling back
            # to the requested page size when the key is simply absent reached
            # the very same failure through a different door -- a server that
            # clamps the page size WITHOUT echoing `limit` at all would end the
            # loop on its first (clamped) page with no error, silently
            # reporting a truncated account as complete. The documented
            # envelope always carries `limit`; its absence means this response
            # is not a shape this tool understands, and an unverified account
            # is always better than a truncated one.
            if "limit" not in meta_obj:
                raise B2bError(
                    f"Account {account_id}: listing response has no 'limit' key "
                    f"(keys present: {sorted(meta_obj)}). The documented response "
                    "envelope always carries it; refusing to treat its absence "
                    "as 'the requested page size was honoured'."
                )
            echoed_limit = meta_obj["limit"]
            if not isinstance(echoed_limit, int) or isinstance(echoed_limit, bool):
                raise B2bError(
                    f"Invalid limit for account {account_id}: expected int, got "
                    f"{type(echoed_limit).__name__} ({echoed_limit!r})"
                )
            if echoed_limit < self._page_size:
                raise B2bError(
                    f"Account {account_id}: requested a page size of "
                    f"{self._page_size} but the server echoed limit "
                    f"{echoed_limit}. The request was not honoured, so the "
                    "listing cannot be trusted to be complete; refusing to "
                    "report a possibly truncated account as enumerated."
                )
            # echoed >= requested: a page can still never hold more rows
            # than we asked for, so the requested size remains the ceiling.
            page_capacity = min(echoed_limit, self._page_size)

            for row in rows:
                # A row with no id, or no created_at, cannot be joined to
                # anything in Rossum -- but it is COUNTED, never silently
                # dropped. The total_count sanity check cannot catch these:
                # it compares raw rows WALKED, not rows kept.
                if "id" not in row or not row.get("created_at"):
                    skipped += 1
                    continue
                created = row["created_at"]
                arrived = _parse_iso(created)
                if arrived < since or arrived > until:
                    continue
                invoices.append(
                    B2bInvoice(
                        einvoice_id=str(row["id"]),
                        account_id=str(account_id),
                        number=row.get("number"),
                        # "client" on the legacy generation, "contact" on the
                        # new one -- see self._profile.sender_field. New-
                        # generation rows carry NO account/project field at
                        # all; the account this invoice belongs to comes from
                        # `account_id` (the request), never from the row.
                        sender=(row.get(self._profile.sender_field) or {}).get("name"),
                        total=None if row.get("total") is None else str(row["total"]),
                        currency=row.get("currency"),
                        state=row.get("state"),
                        created_at=created,
                        ack_at=row.get("ack_at"),
                    )
                )

            rows_walked = offset + len(rows)

            # The ONLY stop condition: an empty or short page. "Short" is
            # measured against the limit the server itself echoed for this
            # page -- see the clamp check above, which has already rejected an
            # echoed limit smaller than the one requested. A larger echoed
            # limit cannot make a page look short either: we can never receive
            # more rows than we asked for, so the requested size stays the
            # ceiling.
            short_page = len(rows) < page_capacity

            if short_page:
                usable_total = total_declared is not None and total_declared > 0
                if usable_total and total_declared > rows_walked:
                    raise B2bError(
                        f"total_count mismatch for account {account_id}: server "
                        f"declared {total_declared} invoices but pagination ended "
                        f"after walking only {rows_walked} rows. This indicates "
                        "truncated results."
                    )
                self.skipped_rows[str(account_id)] = skipped
                return invoices

            offset = rows_walked

    def _list_meta_object(self, payload: dict) -> dict:
        """The sub-object carrying `limit`/`total_count`/`offset` for the
        active profile's envelope shape -- the flat top-level `payload`
        itself for the legacy generation, or `payload["meta"]` for the new
        one. `invoices` is unaffected by this and always stays top-level on
        both (see the caller). Never raises on a missing/malformed `meta`:
        an empty dict here simply reads as "the keys are absent" to every
        check that already handles absence -- an unrecognised envelope must
        still fail loudly downstream (the `limit`-required check), not
        silently here.
        """
        if self._profile.list_envelope == "meta":
            meta = payload.get("meta")
            return meta if isinstance(meta, dict) else {}
        return payload
