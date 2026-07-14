"""Tests for --count: exact Coupa dataset counts via offset bisection."""
import json
import re
import sys

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import write_config


# ── _bisect_count (pure) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 2, 3, 1000])
def test_bisect_count_exact(n):
    probe = lambda off: off < n
    assert cbi._bisect_count(probe) == n


def test_bisect_count_large_stays_within_call_budget():
    n = 4_770_123
    calls = {"n": 0}

    def probe(off):
        calls["n"] += 1
        return off < n

    assert cbi._bisect_count(probe) == n
    assert calls["n"] <= 50


# ── fetch_page limit kwarg ────────────────────────────────────────────────────

def test_fetch_page_adds_limit_param_when_given(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    class FakeSession:
        def get(self, url, params=None, verify=None, timeout=None):
            captured["params"] = params
            return FakeResp()

    cbi.fetch_page(FakeSession(), "api/users", ["id"], 0, "2026-01-01T00:00:00Z", limit=1)
    assert captured["params"]["limit"] == 1


def test_fetch_page_omits_limit_param_by_default(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    class FakeSession:
        def get(self, url, params=None, verify=None, timeout=None):
            captured["params"] = params
            return FakeResp()

    cbi.fetch_page(FakeSession(), "api/users", ["id"], 0, "2026-01-01T00:00:00Z")
    assert "limit" not in captured["params"]


# ── count_datasets wiring ──────────────────────────────────────────────────────

def _install_fake_fetch(monkeypatch, calls, virtual_count):
    def fake_fetch_page(session, endpoint, fields, offset, anchor_ts, limit=None):
        calls.append({"endpoint": endpoint, "offset": offset,
                      "anchor_ts": anchor_ts, "limit": limit})
        return [{"id": 1}] if offset < virtual_count else []

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch_page)


def test_count_datasets_uses_anchor_from_state_and_prints_count(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=42)

    state = {"users": {"anchor_updated_at": "2026-05-01T00:00:00Z"}}
    cbi.count_datasets(["users"], state)

    out = capsys.readouterr().out
    assert "42" in out
    assert "anchor: 2026-05-01T00:00:00Z" in out
    # anchor from state must be the one actually used to probe Coupa
    assert all(c["anchor_ts"] == "2026-05-01T00:00:00Z" for c in calls)
    # every probe call goes through limit=1
    assert all(c["limit"] == 1 for c in calls)


def test_count_datasets_generates_anchor_when_state_has_none(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=5)

    cbi.count_datasets(["users"], {})

    out = capsys.readouterr().out
    assert "5" in out
    anchor_match = re.search(r"anchor: (\S+)", out)
    assert anchor_match
    generated = anchor_match.group(1)
    # generated anchor must be an ISO-8601 UTC 'Z' stamp, and must be the
    # one actually passed as the probe filter
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", generated)
    assert all(c["anchor_ts"] == generated for c in calls)


def test_count_datasets_prints_percent_processed_when_state_has_total(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=100)

    state = {"users": {"anchor_updated_at": "2026-05-01T00:00:00Z",
                       "total_processed": 50}}
    cbi.count_datasets(["users"], state)

    out = capsys.readouterr().out
    assert "50.0%" in out


def test_count_datasets_omits_percent_when_no_total_processed(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=100)

    cbi.count_datasets(["users"], {})

    out = capsys.readouterr().out
    assert "%" not in out


# ── CLI guard ──────────────────────────────────────────────────────────────────

def test_main_rejects_count_with_supervise(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--count", "--supervise"])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--count" in str(exc.value)
    assert "--supervise" in str(exc.value)
