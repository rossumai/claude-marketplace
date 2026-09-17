import json
import urllib.request
from datetime import datetime, timezone

import pytest

from b2brouter import LEGACY_API_VERSION, NEW_API_VERSION, B2bError, B2brouterClient

SINCE = datetime(2026, 1, 18, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc)
BASE = "https://app.example-router.net"


def _invoice(id_, created):
    return {
        "id": id_, "number": f"NUM-{id_}", "state": "new", "total": 2886.1,
        "currency": "EUR", "created_at": created, "ack_at": None,
        "client": {"name": "Example Supplier"}, "project": {"id": 900001},
    }


# --- pagination termination -------------------------------------------------
#
# The one failure this tool must never have: silently truncating the
# authoritative (source) side of the reconciliation. Fullness is judged
# against the page-size limit the SERVER echoes back, never the size we
# asked for -- a missing, zero, or clamped-smaller echoed limit must all
# raise rather than be read as "the request was honoured, stop here".

def test_pages_to_the_short_final_page_and_honours_the_declared_count():
    """Happy path: a full page then a short page ends the loop normally, and
    a declared total_count that matches what was walked passes through."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        if offset == 0:
            rows = [_invoice(0, "2026-01-19T10:00:00Z"), _invoice(1, "2026-01-19T10:00:00Z")]
        else:
            rows = [_invoice(2, "2026-01-19T10:00:00Z")]
        return {"invoices": rows, "total_count": 3, "offset": offset, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    invoices = client.received_invoices("900001", since=SINCE, until=UNTIL)
    assert [i.einvoice_id for i in invoices] == ["0", "1", "2"]


@pytest.mark.parametrize("echoed_limit", [None, 0, 100], ids=["missing", "zero", "clamped"])
def test_a_missing_zero_or_clamped_page_size_signal_raises_instead_of_ending_early(echoed_limit):
    """Any of the three ways a server can fail to honour the requested page
    size (omit `limit` entirely, echo 0, or echo something smaller than
    asked) must raise -- not be read as "the page was short, stop here".
    Silently trusting any of them would report a truncated account as a
    complete one."""
    def transport(path):
        payload = {"invoices": [_invoice(n, "2026-01-19T10:00:00Z") for n in range(100)],
                   "offset": 0}
        if echoed_limit is not None:
            payload["limit"] = echoed_limit
        return payload

    client = B2brouterClient("k", BASE, transport=transport, page_size=500)
    with pytest.raises(B2bError):
        client.received_invoices("900001", since=SINCE, until=UNTIL)


def test_under_declared_total_count_does_not_truncate_a_full_page():
    """A server that declares fewer invoices than it actually holds must not
    end the listing on a full page -- only a short/empty page may."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        if offset == 0:
            return {"invoices": [_invoice(0, "2026-01-19T10:00:00Z"), _invoice(1, "2026-01-19T10:00:00Z")],
                    "total_count": 2, "offset": 0, "limit": 2}
        return {"invoices": [_invoice(2, "2026-01-19T10:00:00Z")], "total_count": 2, "offset": 2, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    invoices = client.received_invoices("900001", since=SINCE, until=UNTIL)
    # The third invoice would have been lost by stopping at the declared 2.
    assert [i.einvoice_id for i in invoices] == ["0", "1", "2"]


# --- API version profiles ----------------------------------------------------
#
# One test per generation, going through the REAL _get() (urlopen patched,
# not the `transport` shortcut used above) so the request itself -- host,
# path, header -- is actually exercised, not just the response parsing. Host,
# path and header are a live-verified API contract (see b2brouter.py's module
# docstring), so pinning them once here, with this comment explaining why, is
# the testing bar's stated exception for exactly that case.

class _FakeHttpResponse:
    """A minimal stand-in for the object urlopen()'s context manager yields:
    just enough for `json.load(response)` to work."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _CapturingUrlopen:
    """Stateful fake standing in for urllib.request.urlopen: records the
    Request it was given and answers with one canned payload, so a test can
    assert BOTH the request shape (host, path, header) and the response
    parsing (envelope, sender field) with no real network access."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, **kwargs) -> _FakeHttpResponse:
        self.requests.append(request)
        return _FakeHttpResponse(self.payload)


def test_legacy_profile_lists_via_the_legacy_host_path_header_and_sender_field(monkeypatch):
    fake = _CapturingUrlopen({
        "invoices": [{
            "id": "42", "number": "N42", "state": "new", "total": 12.5,
            "currency": "EUR", "created_at": "2026-01-19T10:00:00Z",
            "ack_at": None, "client": {"name": "Legacy Sender"},
        }],
        "total_count": 1, "offset": 0, "limit": 500,
    })
    monkeypatch.setattr(urllib.request, "urlopen", fake)

    client = B2brouterClient("k", BASE)  # default: LEGACY_API_VERSION
    invoices = client.received_invoices("900001", since=SINCE, until=UNTIL)

    request = fake.requests[0]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.full_url.startswith(
        "https://app.example-router.net/projects/900001/received.json?"
    )
    assert headers["x-b2b-api-version"] == LEGACY_API_VERSION
    assert [i.sender for i in invoices] == ["Legacy Sender"]


def test_new_profile_lists_via_the_new_host_path_header_meta_envelope_and_sender_field(monkeypatch):
    """Also covers the host swap (configured base_url always names the
    legacy `app.` host; this profile must rehost onto `api.`) and a
    millisecond-fraction timestamp, which must parse exactly like the
    legacy generation's whole-second one."""
    fake = _CapturingUrlopen({
        "invoices": [{
            "id": "77", "number": "N77", "state": "new", "total": 9.99,
            "currency": "EUR", "created_at": "2026-01-19T10:00:00.500Z",
            "ack_at": None, "contact": {"name": "New-Gen Sender"},
        }],
        "meta": {"total_count": 1, "offset": 0, "limit": 500},
    })
    monkeypatch.setattr(urllib.request, "urlopen", fake)

    client = B2brouterClient("k", BASE, api_version=NEW_API_VERSION)
    invoices = client.received_invoices("900001", since=SINCE, until=UNTIL)

    request = fake.requests[0]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.full_url.startswith(
        "https://api.example-router.net/accounts/900001/invoices?"
    )
    assert headers["x-b2b-api-version"] == NEW_API_VERSION
    assert [i.sender for i in invoices] == ["New-Gen Sender"]
    assert [i.einvoice_id for i in invoices] == ["77"]


# --- account listing (the coverage probe) -----------------------------------
#
# `visible_account_ids` enumerates every account a key can see; --check-coverage
# gates the whole reconciliation on it. A group larger than one page must be
# PAGED, never rejected: treating a full first page as evidence of truncation
# made the gate unpassable for any group over the page size, which is exactly
# the large deployments reconciliation matters most for.

def _account(id_):
    return {"id": id_, "name": f"Account {id_}"}


def test_account_listing_pages_past_a_full_first_page():
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in range(offset, min(offset + 2, 5))],
                "total_count": 5, "offset": offset, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    assert client.visible_account_ids() == {"0", "1", "2", "3", "4"}


def test_a_clamped_account_page_size_does_not_end_the_listing_early():
    """The declared total, not page fullness, ends the loop -- a server that
    clamps the page size below the one requested must still be paged out."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in range(offset, min(offset + 1, 3))],
                "total_count": 3, "offset": offset, "limit": 1}

    client = B2brouterClient("k", BASE, transport=transport, page_size=500)
    assert client.visible_account_ids() == {"0", "1", "2"}


def test_an_account_listing_without_a_declared_total_ends_on_a_short_page():
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in range(offset, min(offset + 2, 3))],
                "offset": offset, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    assert client.visible_account_ids() == {"0", "1", "2"}


def test_an_account_listing_that_stops_short_of_its_declared_total_raises():
    """Under-enumerating a key's visibility reports covered accounts as
    uncovered, which fails the coverage gate for a reason that isn't real."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(0)] if offset == 0 else [],
                "total_count": 5, "offset": offset, "limit": 500}

    client = B2brouterClient("k", BASE, transport=transport, page_size=500)
    with pytest.raises(B2bError):
        client.visible_account_ids()


def test_an_account_listing_that_ignores_offset_raises_instead_of_looping():
    """A server that replays page one forever must abort, not spin."""
    def transport(path):
        return {"accounts": [_account(0), _account(1)], "total_count": 9,
                "offset": 0, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    with pytest.raises(B2bError):
        client.visible_account_ids()


def test_a_declared_total_of_zero_does_not_end_the_account_listing_on_a_full_page():
    """The declared total is a REASON TO KEEP GOING, never a reason to stop
    on a full page. `received_invoices` documents `total_count: 0` alongside
    a full page as a measured behaviour of this API; trusting it here would
    report 500 of 708 accounts as a key's full visibility, which is issue
    #144's own failure (a covered account reported uncovered) through a
    different door."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in range(offset, min(offset + 2, 5))],
                "total_count": 0, "offset": offset, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    assert client.visible_account_ids() == {"0", "1", "2", "3", "4"}


def test_overlapping_account_pages_that_walk_past_the_declared_total_raise():
    """`offset` advances by rows returned while the result is a SET, so pages
    that re-serve a row -- what a group being edited mid-listing looks like --
    let the walk reach the declared total having actually seen fewer
    accounts. Stopping there under-reports visibility silently."""
    pages = {0: [0, 1], 2: [1, 2], 4: [2, 3]}

    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in pages.get(offset, [])],
                "total_count": 5, "offset": offset, "limit": 2}

    client = B2brouterClient("k", BASE, transport=transport, page_size=2)
    with pytest.raises(B2bError):
        client.visible_account_ids()


def test_a_clamped_page_size_pages_on_even_with_no_declared_total():
    """With no total to page against, fullness is judged against the limit
    the SERVER echoed, not the one requested -- otherwise a server that
    clamps the page size makes every page look short and the listing ends on
    page one."""
    def transport(path):
        offset = int(path.split("offset=")[1].split("&")[0])
        return {"accounts": [_account(n) for n in range(offset, min(offset + 100, 250))],
                "offset": offset, "limit": 100}

    client = B2brouterClient("k", BASE, transport=transport, page_size=500)
    assert len(client.visible_account_ids()) == 250
