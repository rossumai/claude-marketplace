"""Credentials-file tests -- kept to the handful of invariants that can
actually fail in a way that matters (see docs/testing-skill-scripts.md):
this is config-loading for an otherwise read-only tool, not a bulk writer,
so it gets smoke coverage plus refusal guards, not exhaustive shape pins.

No test touches the network or the real home directory: every path is
under `tmp_path`, and the one test that exercises `recon.main`'s default-path
auto-detection monkeypatches `recon.DEFAULT_CREDENTIALS_PATH` rather than
`Path.home()`.
"""
import csv
import json
import stat

import pytest

import recon
from credentials import (
    PLACEHOLDER_MARK,
    CredentialsError,
    init_credentials,
    load_credentials_file,
)


# --- init_credentials --------------------------------------------------------

def test_template_is_valid_json_with_placeholders_and_owner_only_perms(tmp_path):
    path = tmp_path / "sub" / "credentials.json"

    init_credentials(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rossum"]["token"].startswith(PLACEHOLDER_MARK)
    assert data["b2brouter"]["keys"]["GROUP-1"].startswith(PLACEHOLDER_MARK)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_init_credentials_refuses_to_overwrite(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text('{"filled": "by the operator"}', encoding="utf-8")

    with pytest.raises(CredentialsError, match=str(path)):
        init_credentials(path)

    # Untouched -- the refusal must not clobber so much as a byte.
    assert path.read_text(encoding="utf-8") == '{"filled": "by the operator"}'


# --- load_credentials_file ---------------------------------------------------

def test_unfilled_required_placeholder_is_rejected(tmp_path):
    path = tmp_path / "credentials.json"
    init_credentials(path)  # token/ui_host left as placeholders

    with pytest.raises(CredentialsError, match="rossum.token"):
        load_credentials_file(path)


def test_main_refuses_to_run_with_an_unfilled_credentials_file(tmp_path, monkeypatch):
    """The CLI-level contract: an unfilled file must not half-run -- it exits
    with the SAME code the tool already uses for missing credentials (2),
    never 0 or 1."""
    monkeypatch.delenv("ROSSUM_TOKEN", raising=False)
    path = tmp_path / "credentials.json"
    init_credentials(path)  # token left as a placeholder

    rc = recon.main(["--credentials", str(path), "--show-discovery"])

    assert rc == 2


def test_filled_keys_used_and_placeholder_labelled_entries_skipped(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({
        "_readme": "ignored",
        "rossum": {
            "_comment": "ignored",
            "token": "real-token",
            "ui_host": "example-org.rossum.app",
        },
        "b2brouter": {
            "keys": {
                "GROUP-1": "real-key-value",
                "GROUP-2": f"{PLACEHOLDER_MARK}-B2BROUTER-KEY-FOR-THIS-GROUP-HERE--",
            },
        },
    }), encoding="utf-8")

    creds = load_credentials_file(path)

    assert creds.token == "real-token"
    assert creds.ui_host == "example-org.rossum.app"
    assert creds.keys == {"GROUP-1": "real-key-value"}


def test_underscore_keys_are_ignored_at_any_level(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({
        "_readme": "ignored",
        "rossum": {"_comment": "ignored", "token": "real-token"},
        "b2brouter": {
            "_comment": "ignored",
            "keys": {"_note": "should never become an account-group label",
                      "GROUP-1": "real-key-value"},
        },
    }), encoding="utf-8")

    creds = load_credentials_file(path)

    assert creds.keys == {"GROUP-1": "real-key-value"}


# --- wired into main() -------------------------------------------------------

_HOOKS = [{
    "id": 42, "name": "Region Z", "active": True,
    "queues": ["https://rossum.example.test/api/v1/queues/222"],
    "settings": {
        "b2b_router_account_id": ["800001"],
        "b2b_router_base_url": "https://app.example-router.net",
    },
}]


def test_main_uses_the_credentials_files_token_keys_and_ui_host(tmp_path, monkeypatch):
    """Proves the file is actually wired in, not merely parsed: env vars are
    deliberately absent/wrong, so a clean run and the right ui_host in the
    CSV can only come from the file."""
    monkeypatch.delenv("ROSSUM_TOKEN", raising=False)
    monkeypatch.delenv("B2B_API_KEY", raising=False)

    from datetime import datetime, timezone
    from match import RossumAnn

    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(json.dumps({
        "rossum": {"token": "file-token", "ui_host": "from-file.rossum.app"},
        "b2brouter": {"keys": {
            "GROUP-1": "file-key",
            "GROUP-2": f"{PLACEHOLDER_MARK}-B2BROUTER-KEY-FOR-THIS-GROUP-HERE--",
        }},
    }), encoding="utf-8")

    class _FakeRossumClient:
        def __init__(self, token, base_url):
            assert token == "file-token"

        def list_hooks(self):
            return _HOOKS

        def einvoice_index(self, queue_ids, since):
            return {"1": [RossumAnn(1, "exported", "einvoice1.pdf", True,
                                     "2026-01-19T10:00:00Z")]}

        def lookup_einvoice(self, einvoice_id):
            return []

        def has_surviving_original(self, invoice_number):
            return False

    class _FakeB2bClient:
        def __init__(self, api_key, base_url, api_version=None):
            assert api_key == "file-key"  # the placeholder-labelled key must
            self.skipped_rows = {}         # never reach a client at all

        def visible_account_ids(self):
            return {"800001"}

        def received_invoices(self, account_id, *, since, until):
            from match import B2bInvoice
            return [B2bInvoice(
                einvoice_id="1", account_id="800001", number="N1",
                sender="Example Supplier", total="10.0", currency="EUR",
                state="new", created_at="2026-01-19T10:00:00Z",
                ack_at="2026-01-19T10:05:00Z",
            )]

        def get_invoice(self, einvoice_id):
            return None

    monkeypatch.setattr(recon, "RossumClient", _FakeRossumClient)
    monkeypatch.setattr(recon, "B2brouterClient", _FakeB2bClient)

    out_path = tmp_path / "out.csv"
    rc = recon.main(["--credentials", str(creds_path), "--out", str(out_path)])

    assert rc == 0
    with out_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["note"] == "ok"
    assert "from-file.rossum.app" in rows[0]["annotation_link"]
