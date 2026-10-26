import csv
from datetime import datetime, timezone

import pytest

from b2brouter import LEGACY_API_VERSION, NEW_API_VERSION, B2bError, InvoiceRef
from discovery import Channel
from match import NOT_CHECKED, B2bInvoice, RossumAnn
from recon import build_client_resolver, check_coverage, main, reconcile_channel

import recon

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 1, 18, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc)

CHANNEL = Channel(
    hook_id=11, name="Region A", queue_ids=(111,),
    account_ids=("900001", "900002"), b2b_base_url="https://app.example-router.net",
    active=True,
)


def _inv(eid, account="900001", *, created="2026-01-19T10:00:00Z", number=None,
         sender="Example Supplier", total="10.0"):
    return B2bInvoice(
        einvoice_id=eid, account_id=account, number=f"N{eid}" if number is None else number,
        sender=sender, total=total, currency="EUR", state="new",
        # The listing never carries ack_at in production; only a by-id lookup does.
        created_at=created, ack_at=None,
    )


def _ann(aid, status="exported", created="2026-01-19T10:00:00Z"):
    return RossumAnn(aid, status, f"einvoice{aid}.pdf", True, created)


_ALL_MATCH_FIELDS = {"document_id": "string", "amount_total": "number"}


class FakeB2b:
    def __init__(self, per_account, failing=(), refs=None):
        self.per_account = per_account
        self.failing = set(failing)
        self.refs = refs or {}
        self.skipped_rows = {}
        self.looked_up = []

    def received_invoices(self, account_id, *, since, until):
        if account_id in self.failing:
            raise B2bError("HTTP 401")
        return self.per_account.get(account_id, [])

    def get_invoice(self, einvoice_id):
        self.looked_up.append(einvoice_id)
        return self.refs.get(einvoice_id)


class FakeRossum:
    """`survivors` maps (invoice number, total) -> the id of a healthy
    annotation elsewhere in the org carrying that number and total."""

    def __init__(self, index, survivors=None):
        self.index = index
        self.survivors = survivors or {}

    def einvoice_index(self, queue_ids, since):
        return self.index

    def lookup_einvoice(self, einvoice_id):
        return []

    def has_surviving_original(self, invoice_number):
        return False

    def surviving_annotation_id(self, invoice_number, *, total=None, total_field=None):
        return self.survivors.get((invoice_number, total))


# --- exit-code contract ------------------------------------------------------
#
# The exit code is this tool's actual safety property: a run that could not
# fully enumerate some accounts must never exit 0 -- neither reconcile_channel
# (which still emits a synthetic row for the account, never a silent drop)
# nor main() (which reflects that into the process exit code and prints
# INCOMPLETE:) nor --check-coverage (a dedicated pre-flight for exactly this
# question) may report a clean run when an account was never actually
# verified.

def test_uncovered_account_is_never_silently_skipped():
    b2b = FakeB2b({"900001": [_inv("1")]})
    rossum = FakeRossum({"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]})

    rows, failed = reconcile_channel(
        CHANNEL, lambda account: b2b, rossum,
        since=SINCE, until=UNTIL, now=NOW, grace_minutes=30,
        uncovered={"900002"}, ui_host="rossum.invalid",
    )

    assert "900002" in failed
    unverified = next(r for r in rows if r.account == "900002")
    assert unverified.note == "UNVERIFIED_SOURCE"
    assert "no API key" in unverified.b2b_state


_HOOKS_ONE_CHANNEL = [{
    "id": 42, "name": "Region Z", "active": True,
    "queues": ["https://rossum.example.test/api/v1/queues/222"],
    "settings": {
        "b2b_router_account_id": ["800001"],
        "b2b_router_base_url": "https://app.example-router.net",
    },
}]


def _fake_rossum_factory(hooks, index, schema_fields=_ALL_MATCH_FIELDS):
    class _FakeRossumClient:
        def __init__(self, token, base_url):
            pass

        def list_hooks(self):
            return hooks

        def einvoice_index(self, queue_ids, since):
            return index

        def lookup_einvoice(self, einvoice_id):
            return []

        def has_surviving_original(self, invoice_number):
            return False

        def surviving_annotation_id(self, invoice_number, *, total=None, total_field=None):
            return None

        def schema_datapoints(self, queue_ids):
            return schema_fields

    return _FakeRossumClient


def _fake_b2b_factory(per_account, failing=()):
    class _FakeB2bClient:
        def __init__(self, api_key, base_url, api_version=None):
            self.skipped_rows = {}

        def visible_account_ids(self):
            return set(per_account.keys())

        def received_invoices(self, account_id, *, since, until):
            if account_id in failing:
                raise B2bError("HTTP 401")
            return per_account.get(account_id, [])

        def get_invoice(self, einvoice_id):
            return None

    return _FakeB2bClient


def test_main_clean_run_exits_zero_and_writes_the_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    rossum_cls = _fake_rossum_factory(
        _HOOKS_ONE_CHANNEL,
        index={"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _fake_b2b_factory({"800001": [_inv("1", "800001")]}))

    out_path = tmp_path / "out.csv"
    rc = main(["--ui-host", "rossum.invalid", "--out", str(out_path)])

    assert rc == 0
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["note"] for r in rows] == ["ok"]


def test_main_reports_incomplete_and_exits_one_when_an_account_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    hooks = [{
        "id": 42, "name": "Region Z", "active": True,
        "queues": ["https://rossum.example.test/api/v1/queues/222"],
        "settings": {
            "b2b_router_account_id": ["800001", "800002"],
            "b2b_router_base_url": "https://app.example-router.net",
        },
    }]
    rossum_cls = _fake_rossum_factory(
        hooks, index={"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]},
    )
    b2b_cls = _fake_b2b_factory(
        {"800001": [_inv("1", "800001")], "800002": [_inv("2", "800002")]}, failing={"800002"},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", b2b_cls)

    out_path = tmp_path / "out.csv"
    rc = main(["--ui-host", "rossum.invalid", "--out", str(out_path)])

    captured = capsys.readouterr()
    assert rc == 1
    assert rc != 2  # never reinterpreted as a whole-run abort
    assert "INCOMPLETE:" in captured.err

    # The CSV itself discloses its own incompleteness -- nobody reading the
    # emailed/filed report sees stderr or the exit code.
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_account = {r["account"]: r for r in rows}
    assert by_account["800001"]["note"] == "ok"
    assert by_account["800002"]["note"] == "UNVERIFIED_SOURCE"


def test_check_coverage_returns_one_and_names_the_uncovered_ids(capsys):
    channel = Channel(
        hook_id=1, name="Region A", queue_ids=(1,), account_ids=("900001", "900002", "900003"),
        b2b_base_url="https://app.example-router.net", active=True,
    )

    rc = check_coverage(
        [channel], uncovered_by_host={"https://app.example-router.net": ["900002", "900003"]},
    )

    captured = capsys.readouterr()
    assert rc == 1
    # A count alone is not actionable -- the actual ids must be printed.
    assert "900002" in captured.out
    assert "900003" in captured.out


def test_coverage_counts_only_a_channels_own_uncovered_accounts(capsys):
    """Several channels routinely share one B2Brouter host, each owning a
    subset of that host's accounts. Coverage is reported PER CHANNEL, so a
    sibling channel's uncovered accounts must not be subtracted from this
    channel's total -- doing that produced impossible counts like
    `-20/11 accounts covered` and listed ids the channel does not own."""
    host = "https://app.example-router.net"
    channel_a = Channel(
        hook_id=1, name="Region A", queue_ids=(1,), account_ids=("900001", "900002"),
        b2b_base_url=host, active=True,
    )
    channel_b = Channel(
        hook_id=2, name="Region B", queue_ids=(2,), account_ids=("900003",),
        b2b_base_url=host, active=True,
    )

    rc = check_coverage([channel_a, channel_b], uncovered_by_host={host: ["900003"]})

    captured = capsys.readouterr()
    assert rc == 1
    assert "Region A: 2/2 accounts covered" in captured.out
    assert "Region B: 0/1 accounts covered" in captured.out
    # Region A is fully covered, so it must not name a sibling's account.
    region_a_line = [l for l in captured.out.splitlines() if l.startswith("Region A")]
    assert all("900003" not in line for line in region_a_line)


def test_main_check_coverage_exits_one_and_prints_the_uncovered_account(monkeypatch, capsys):
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    hooks = [{
        "id": 42, "name": "Region Z", "active": True,
        "queues": ["https://rossum.example.test/api/v1/queues/222"],
        "settings": {
            "b2b_router_account_id": ["800001", "800002"],
            "b2b_router_base_url": "https://app.example-router.net",
        },
    }]
    rossum_cls = _fake_rossum_factory(hooks, index={})

    class _NoInvoiceFetchB2bClient:
        """--check-coverage must never list invoices, only probe visibility."""

        def __init__(self, api_key, base_url, api_version=None):
            self.skipped_rows = {}

        def visible_account_ids(self):
            return {"800001"}  # 800002 is NOT visible

        def received_invoices(self, account_id, *, since, until):
            raise AssertionError("--check-coverage must not fetch invoices")

    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _NoInvoiceFetchB2bClient)

    rc = main(["--ui-host", "rossum.invalid", "--check-coverage"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "800002" in captured.out


# --- B2Brouter API version auto-detection / pinning -------------------------
#
# A group whose default API generation is newer rejects a legacy-host call
# with api_version_subdomain_mismatch (see b2brouter.py's module docstring).
# build_client_resolver must retry exactly once against the other generation
# on THAT code, lock it in for every later call on the host, and must NOT
# retry on any other failure code -- a plain bad key must not cost a second
# request "confirming" the same failure at the other version.

_VERSION_CHANNEL = Channel(
    hook_id=1, name="Region A", queue_ids=(1,), account_ids=("900001",),
    b2b_base_url="https://app.example-router.net", active=True,
)


def _versioned_fake_client_factory(calls, *, always_fails_with=None, works_at=NEW_API_VERSION):
    """A fake B2brouterClient whose visible_account_ids() outcome depends on
    the api_version it was built with -- `calls` records every version this
    factory was asked to build a client for, in order, so a test can assert
    exactly which versions were tried and how many times."""
    class _FakeClient:
        def __init__(self, api_key, base_url, api_version=LEGACY_API_VERSION):
            self.api_version = api_version

        def visible_account_ids(self):
            calls.append(self.api_version)
            if always_fails_with is not None:
                raise B2bError("HTTP 400", code=always_fails_with)
            if self.api_version != works_at:
                raise B2bError("HTTP 400", code="api_version_subdomain_mismatch")
            return {"900001"}

    return _FakeClient


def test_build_client_resolver_auto_detects_the_new_version_on_subdomain_mismatch():
    calls: list[str] = []
    mapping, uncovered, _get_b2b, failed = build_client_resolver(
        [_VERSION_CHANNEL], {"K": "key"},
        client_factory=_versioned_fake_client_factory(calls, works_at=NEW_API_VERSION),
    )

    assert not failed
    assert not uncovered
    assert mapping[("https://app.example-router.net", "900001")] == "K"
    # The default (legacy) was tried first and rejected; the retry at the
    # other generation is what actually succeeded -- never more than once.
    assert calls == [LEGACY_API_VERSION, NEW_API_VERSION]


def test_build_client_resolver_does_not_retry_on_a_different_error_code():
    calls: list[str] = []
    with pytest.raises(B2bError):
        build_client_resolver(
            [_VERSION_CHANNEL], {"K": "key"},
            client_factory=_versioned_fake_client_factory(calls, always_fails_with="invalid_api_key"),
        )
    # A bad key fails identically at either version -- retrying would just
    # spend a second request confirming the same answer.
    assert calls == [LEGACY_API_VERSION]


def test_build_client_resolver_pinned_version_skips_detection():
    calls: list[str] = []
    mapping, uncovered, _get_b2b, failed = build_client_resolver(
        [_VERSION_CHANNEL], {"K": "key"},
        client_factory=_versioned_fake_client_factory(calls, works_at=NEW_API_VERSION),
        pinned_api_version=NEW_API_VERSION,
    )

    assert not failed
    assert not uncovered
    assert mapping[("https://app.example-router.net", "900001")] == "K"
    # Pinning goes straight to the working version -- the legacy default is
    # never tried, so there is nothing to detect or retry.
    assert calls == [NEW_API_VERSION]


# --- UI host for annotation links -------------------------------------------
#
# The links in the report are built from `ui_host` alone. Nothing in the API
# reveals it, so a wrong value is SILENT: the report looks perfect and every
# link lands on the wrong cell. The API base URL does reveal it, though --
# both cell shapes this tool supports serve the UI and /api/v1 on the same
# host -- so an explicitly supplied base URL settles it.

@pytest.mark.parametrize("base_url,expected", [
    ("https://example-org.rossum.app", "example-org.rossum.app"),
    ("https://example-org.rossum.app/", "example-org.rossum.app"),
    ("https://elis.rossum.ai/api/v1", "elis.rossum.ai"),
    ("https://api.elis.rossum.ai", "elis.rossum.ai"),
    ("elis.rossum.ai", "elis.rossum.ai"),
])
def test_ui_host_is_read_off_the_api_base_url(base_url, expected):
    assert recon.ui_host_from_base_url(base_url) == expected


def test_an_explicit_base_url_supplies_the_ui_host_for_links(tmp_path, monkeypatch):
    """An operator who names the org's own cell must not also have to repeat
    it as --ui-host -- repeating it is where the two drift apart."""
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    rossum_cls = _fake_rossum_factory(
        _HOOKS_ONE_CHANNEL,
        index={"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _fake_b2b_factory({"800001": [_inv("1", "800001")]}))

    out_path = tmp_path / "out.csv"
    rc = main(["--base-url", "https://example-org.rossum.app", "--out", str(out_path)])

    assert rc == 0
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["annotation_link"].startswith("https://example-org.rossum.app/document/")


def test_neither_a_ui_host_nor_a_base_url_still_refuses_to_guess(monkeypatch):
    """With no base URL either, the default is a guess about which cell the
    org lives on -- and a wrong link is silent. Keep refusing, and refuse
    BEFORE touching the network: exit 2 is also what an auth failure returns,
    so a client that gets built here would make this test pass for the wrong
    reason (see the credentials-isolation fixture in conftest.py)."""
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")

    class _NeverBuilt:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "main() must refuse before constructing any API client"
            )

    monkeypatch.setattr(recon, "RossumClient", _NeverBuilt)
    monkeypatch.setattr(recon, "B2brouterClient", _NeverBuilt)

    assert main(["--check-coverage"]) == 2


def test_a_ui_host_on_a_different_host_than_the_api_is_flagged(tmp_path, monkeypatch, capsys):
    """The exact Eurofins case: links built for the shared cell while the API
    talks to the org's own. Both are reachable, so nothing else notices."""
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    rossum_cls = _fake_rossum_factory(
        _HOOKS_ONE_CHANNEL,
        index={"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _fake_b2b_factory({"800001": [_inv("1", "800001")]}))

    out_path = tmp_path / "out.csv"
    rc = main([
        "--base-url", "https://example-org.rossum.app",
        "--ui-host", "elis.rossum.ai",
        "--out", str(out_path),
    ])

    captured = capsys.readouterr()
    assert rc == 0  # a warning, never a refusal -- both hosts can be valid
    assert "elis.rossum.ai" in captured.err and "example-org.rossum.app" in captured.err
    # The operator's explicit choice still wins.
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["annotation_link"].startswith("https://elis.rossum.ai/document/")


def test_a_ui_host_pasted_with_its_scheme_is_not_reported_as_a_mismatch(tmp_path, monkeypatch, capsys):
    """`--ui-host https://acme.rossum.app` names the same host as
    `--base-url https://acme.rossum.app`. Comparing the two raw turns a
    harmless paste into a warning about cross-cell links -- the one message
    guaranteed to send an operator looking in the wrong place -- and the
    scheme lands in the link itself."""
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    rossum_cls = _fake_rossum_factory(
        _HOOKS_ONE_CHANNEL,
        index={"1": [RossumAnn(1, "exported", "einvoice1.pdf", True, "2026-01-19T10:00:00Z")]},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _fake_b2b_factory({"800001": [_inv("1", "800001")]}))

    out_path = tmp_path / "out.csv"
    rc = main([
        "--base-url", "https://example-org.rossum.app",
        "--ui-host", "https://example-org.rossum.app",
        "--out", str(out_path),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "WARNING" not in captured.err
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["annotation_link"] == "https://example-org.rossum.app/document/1"


# --- window scoping with --to in the past ------------------------------------
#
# The index has no upper bound on purpose: an invoice that arrived before
# --to but was imported later must still find its annotation. So ids whose
# annotations were all created AFTER --to are in the index too, and must not
# count as in-window ids that no listed invoice matched -- on a live run with
# --to a month back, that turned every account of the channel into
# UNVERIFIED_SOURCE.

PAST_SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)
PAST_UNTIL = datetime(2026, 1, 10, tzinfo=timezone.utc)


def test_ids_annotated_after_the_window_end_flag_no_account(capsys):
    later = "2026-01-15T09:00:00Z"
    b2b = FakeB2b(
        {"900001": [_inv("1", created="2026-01-05T10:00:00Z")]},
        refs={eid: InvoiceRef("900001", later) for eid in ("5", "6")},
    )
    rossum = FakeRossum({
        "1": [_ann(1, created="2026-01-05T10:01:00Z")],
        "5": [_ann(5, created=later)],
        "6": [_ann(6, created=later)],
    })

    rows, failed = reconcile_channel(
        CHANNEL, lambda account: b2b, rossum,
        since=PAST_SINCE, until=PAST_UNTIL, now=NOW, grace_minutes=30,
        uncovered=set(), ui_host="rossum.invalid",
    )

    assert failed == []
    assert [r.note for r in rows] == ["ok"]
    # The excluded ids are disclosed as their own count, not dropped silently.
    after_line = next(l for l in capsys.readouterr().out.splitlines()
                      if "after the window end" in l)
    assert after_line.split()[0] == "2"


def test_an_invoice_listed_before_the_window_end_matches_an_annotation_after_it():
    b2b = FakeB2b({"900001": [_inv("1", created="2026-01-09T23:59:00Z")]})
    rossum = FakeRossum({"1": [_ann(1, created="2026-01-10T00:01:00Z")]})

    rows, failed = reconcile_channel(
        CHANNEL, lambda account: b2b, rossum,
        since=PAST_SINCE, until=PAST_UNTIL, now=NOW, grace_minutes=30,
        uncovered=set(), ui_host="rossum.invalid",
    )

    assert failed == []
    assert [r.note for r in rows] == ["ok"]


# --- acked_at lookups go to MISSING_IN_ROSSUM first ---------------------------
#
# On a MISSING_IN_ROSSUM row, acked_at decides whose fault the loss is. DELETED
# rows are far more numerous and need no action, so they must not use up the
# cap first.

def test_ack_lookups_reach_missing_rows_before_deleted_rows():
    b2b = FakeB2b(
        {"900001": [_inv("1"), _inv("2")]},
        refs={eid: InvoiceRef("900001", "2026-01-19T10:00:00Z", ack_at="2026-01-19T10:05:00Z")
              for eid in ("1", "2")},
    )
    rossum = FakeRossum({"1": [_ann(1, status="deleted")]})  # "2" has no trace

    rows, _ = reconcile_channel(
        CHANNEL, lambda account: b2b, rossum,
        since=SINCE, until=UNTIL, now=NOW, grace_minutes=30,
        uncovered=set(), ui_host="rossum.invalid", ack_cap=1,
    )

    by_note = {r.note: r for r in rows}
    assert by_note["MISSING_IN_ROSSUM"].acked_at == "2026-01-19T10:05:00Z"
    assert by_note["DELETED"].acked_at == NOT_CHECKED


# --- MISSING_IN_ROSSUM checked by invoice number -------------------------------
#
# A resend gets a new e-invoice id, and an invoice can reach Rossum by another
# route. Either way a copy exists, and the row must not tell the operator to
# escalate. A number alone collides across suppliers, so the match also needs
# the sender or the total.

def _reconcile(invoices, index, rossum_survivors=None, **kwargs):
    b2b = FakeB2b({"900001": invoices})
    rows, _ = reconcile_channel(
        CHANNEL, lambda account: b2b, FakeRossum(index, rossum_survivors),
        since=SINCE, until=UNTIL, now=NOW, grace_minutes=30,
        uncovered=set(), ui_host="rossum.invalid", **kwargs,
    )
    return {r.einvoice_id: r for r in rows}


def test_a_resend_inside_the_report_is_not_actionable():
    rows = _reconcile(
        [_inv("1", number="INV-7"), _inv("2", number="INV-7")],
        {"1": [_ann(1)]},
    )

    assert rows["2"].note == "MISSING_RESENT"
    assert rows["2"].note in recon.ACTIONABLE_EXEMPT_NOTES
    assert rows["2"].annotation_link == rows["1"].annotation_link


def test_the_same_number_from_a_different_sender_stays_missing():
    rows = _reconcile(
        [_inv("1", number="INV-7"), _inv("2", number="INV-7", sender="Other Supplier")],
        {"1": [_ann(1)]},
    )

    assert rows["2"].note == "MISSING_IN_ROSSUM"


def test_a_copy_found_elsewhere_in_rossum_is_not_actionable():
    rows = _reconcile([_inv("2", number="INV-7")], {}, rossum_survivors={("INV-7", "10.0"): 77})

    assert rows["2"].note == "MISSING_FOUND_BY_NUMBER"
    assert rows["2"].note in recon.ACTIONABLE_EXEMPT_NOTES
    assert rows["2"].annotation_link == "https://rossum.invalid/document/77"


def test_a_row_past_the_number_search_cap_stays_missing():
    rows = _reconcile(
        [_inv("2", number="INV-7")], {}, rossum_survivors={("INV-7", "10.0"): 77},
        missing_verify_cap=0,
    )

    assert rows["2"].note == "MISSING_IN_ROSSUM"


def test_an_empty_source_row_gets_its_own_actionable_note():
    rows = _reconcile([_inv("2", number="", sender=None, total="0.0")], {})

    assert rows["2"].note == "MISSING_EMPTY_SOURCE"
    assert rows["2"].note not in recon.ACTIONABLE_EXEMPT_NOTES


def test_main_refuses_a_match_strategy_whose_field_no_schema_has(monkeypatch, capsys):
    """The search on an absent field matches nothing, so every row would stay
    MISSING_IN_ROSSUM with no sign that the check never worked."""
    monkeypatch.setenv("ROSSUM_TOKEN", "test-token")
    monkeypatch.setenv("B2B_API_KEY", "test-key")
    rossum_cls = _fake_rossum_factory(
        _HOOKS_ONE_CHANNEL, index={}, schema_fields={"document_id": "string"},
    )
    monkeypatch.setattr(recon, "RossumClient", rossum_cls)
    monkeypatch.setattr(recon, "B2brouterClient", _fake_b2b_factory({"800001": []}))

    rc = main(["--ui-host", "rossum.invalid", "--match-by", "number+total"])

    assert rc == 2
    assert "--total-field" in capsys.readouterr().err
