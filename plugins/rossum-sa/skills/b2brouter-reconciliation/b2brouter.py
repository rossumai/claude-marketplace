"""Read-only B2Brouter client.

The working listing endpoint, measured directly against the live API by
replicating what the production importer integration itself sends:

    GET {base}/projects/{ACCOUNT_ID}/received.json?type=ReceivedInvoice&ack=true
      -> {"invoices": [...], "total_count": N, "offset": O, "limit": L}

`invoices.json` (the endpoint this client used to call) is not a working
alias for received invoices: it measured total_count: 0 for every account and
every parameter combination tried. `received.json` is the correct index.

`ack=true` is REQUIRED, not optional. Omitting `ack` entirely (or sending
`ack=false`) does not return the full index -- it returns ONLY the importer's
pending/unacknowledged queue, which for a mostly-processed account is a
handful of rows out of thousands. That silent default is why this tool used
to see next to nothing even once pointed at the right path. `ack=true` was
verified NOT to mutate anything: three consecutive `ack=true` list calls left
a pending invoice's `ack_at` still None and its `updated_at` unchanged, and a
follow-up `ack=false` call still returned that same invoice -- the parameter
is a read filter, not a state change, despite the alarming name. `ack=false`
must never be sent by this tool; it is the importer's own work queue, not the
reconciliation's population.

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


class B2bError(RuntimeError):
    pass


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
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
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
        request = urllib.request.Request(
            url, headers={"X-B2B-API-Key": self._key}, method="GET"
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
                raise B2bError(f"GET {url} -> HTTP {exc.code}") from exc
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

        Response shape MEASURED directly against the legacy host's live
        response (unlike the rest of this docstring's usual caveats, this one
        is confirmed, not assumed): the invoice comes back WRAPPED as
        `{"invoice": {"id": ..., "project": {"id": ..., "name": ...},
        "account": null, "created_at": ..., ...}}` -- the account/project id
        lives at `invoice.project.id`; `invoice.account` is a real key on
        this host but is always `null`. A newer API version has been
        reported to return the invoice UNWRAPPED (no `"invoice"` envelope)
        with the id under `account` instead of `project` -- this client
        supports both: it unwraps `payload["invoice"]` when present (falling
        back to treating `payload` itself as the invoice object when it is
        not), and reads `project.id`, falling back to `account.id` only when
        `project` is absent or `null`. A payload with neither field, on
        either shape, raises rather than being silently read as "no
        account" -- see the raise below. `created_at` is read straight off
        that same invoice object; if it is absent or not a string, the
        returned `InvoiceRef.created_at` is None rather than raising -- an
        account id without a usable arrival date is still useful to the
        caller (it just can't be used to detect a pre-window re-import).
        `ack_at` is read the same way, off the same invoice object: if it is
        absent or not a string, `InvoiceRef.ack_at` is None. That None is
        genuinely ambiguous in isolation (unacknowledged vs. a field this
        host doesn't populate at all) -- but the caller only ever reaches
        into `ack_at` after this lookup already SUCCEEDED (a real invoice
        object came back), so None here means "this invoice, confirmed to
        exist, has no acknowledgement timestamp" -- as distinct from a lookup
        that failed or 404d entirely, which the caller marks differently.

        A 404 means this key/account group cannot see this invoice id and
        returns None, exactly like a per-id fallback lookup that legitimately
        finds nothing. Any other failure (network, non-2xx, an unrecognised
        response shape) raises B2bError so the caller can tell "checked, not
        found" apart from "the check itself failed" -- and, in particular,
        never silently count a real failure as proof no account owns the id.
        """
        try:
            payload = self._transport(f"/invoices/{einvoice_id}.json")
        except B2bError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        # Wrapped (measured, legacy host) vs. unwrapped (reported, newer API
        # version): unwrap only when "invoice" is actually present and is
        # itself an object, so an unwrapped payload that merely lacks the key
        # is read as the invoice object directly rather than as garbage.
        invoice = payload["invoice"] if isinstance(payload.get("invoice"), dict) else payload
        project = invoice.get("project")
        account_id = None
        if isinstance(project, dict) and project.get("id") is not None:
            account_id = str(project["id"])
        else:
            # `project` absent or null (measured: always null on the legacy
            # host's own `account` key) -- fall back to `account.id`, the
            # newer API version's shape.
            account = invoice.get("account")
            if isinstance(account, dict) and account.get("id") is not None:
                account_id = str(account["id"])
        if account_id is None:
            raise B2bError(
                f"Invoice {einvoice_id} lookup response has no usable 'project' "
                f"or 'account' object (keys present: {sorted(invoice)})."
            )
        created_at = invoice.get("created_at")
        ack_at = invoice.get("ack_at")
        return InvoiceRef(
            account_id=account_id,
            created_at=created_at if isinstance(created_at, str) else None,
            ack_at=ack_at if isinstance(ack_at, str) else None,
        )

    def received_invoices(
        self, account_id: str, *, since: datetime, until: datetime
    ) -> list[B2bInvoice]:
        """Received invoices that ARRIVED in [since, until] for one account.

        Calls GET /projects/{account_id}/received.json with ack=true. ack=true
        is required to see the full index rather than just the importer's
        pending queue (see the module docstring for the measured evidence
        that this does not mutate anything); ack=false must never be sent.

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
            payload = self._transport(f"/projects/{account_id}/received.json?{query}")
            # I3: require the key. `.get("invoices", [])` turned any 200
            # response of an unexpected shape -- a captive proxy page, an
            # envelope change -- into a clean, empty, error-free account.
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
            if total_declared is None and "total_count" in payload:
                total_declared = payload["total_count"]
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
            if "limit" not in payload:
                raise B2bError(
                    f"Account {account_id}: listing response has no 'limit' key "
                    f"(keys present: {sorted(payload)}). The documented response "
                    "envelope always carries it; refusing to treat its absence "
                    "as 'the requested page size was honoured'."
                )
            echoed_limit = payload["limit"]
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
                        sender=(row.get("client") or {}).get("name"),
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
