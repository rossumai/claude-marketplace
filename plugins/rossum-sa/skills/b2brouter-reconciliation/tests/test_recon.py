import csv
from datetime import datetime, timezone

from b2brouter import B2bError
from discovery import Channel
from match import B2bInvoice, RossumAnn
from recon import check_coverage, main, reconcile_channel

import recon

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 1, 18, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc)

CHANNEL = Channel(
    hook_id=11, name="Region A", queue_ids=(111,),
    account_ids=("900001", "900002"), b2b_base_url="https://app.example-router.net",
    active=True,
)


def _inv(eid, account="900001"):
    return B2bInvoice(
        einvoice_id=eid, account_id=account, number=f"N{eid}", sender="Example Supplier",
        total="10.0", currency="EUR", state="new",
        created_at="2026-01-19T10:00:00Z", ack_at="2026-01-19T10:05:00Z",
    )


class FakeB2b:
    def __init__(self, per_account, failing=()):
        self.per_account = per_account
        self.failing = set(failing)
        self.skipped_rows = {}

    def received_invoices(self, account_id, *, since, until):
        if account_id in self.failing:
            raise B2bError("HTTP 401")
        return self.per_account.get(account_id, [])

    def get_invoice(self, einvoice_id):
        return None


class FakeRossum:
    def __init__(self, index):
        self.index = index

    def einvoice_index(self, queue_ids, since):
        return self.index

    def lookup_einvoice(self, einvoice_id):
        return []

    def has_surviving_original(self, invoice_number):
        return False


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


def _fake_rossum_factory(hooks, index):
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

    return _FakeRossumClient


def _fake_b2b_factory(per_account, failing=()):
    class _FakeB2bClient:
        def __init__(self, api_key, base_url):
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

        def __init__(self, api_key, base_url):
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
