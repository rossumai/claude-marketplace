"""--count: exact Coupa dataset counts via offset bisection (read-only)."""
import json

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import write_config


# ── _bisect_count (pure) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 2, 3, 1000])
def test_bisect_count_exact(n):
    probe = lambda off: off < n
    assert cbi._bisect_count(probe) == n


def test_bisect_count_large_stays_within_call_budget():
    # the design promise vs a linear scan: ~2*log2(n) probe calls, not n —
    # a regression here would hammer the Coupa API millions of times
    n = 4_770_123
    calls = {"n": 0}

    def probe(off):
        calls["n"] += 1
        return off < n

    assert cbi._bisect_count(probe) == n
    assert calls["n"] <= 50


# ── anchor selection (mid-run counts must match the running job) ─────────────

def _install_fake_fetch(monkeypatch, calls, virtual_count):
    def fake_fetch_page(session, endpoint, fields, offset, anchor_ts, limit=None):
        calls.append({"offset": offset, "anchor_ts": anchor_ts})
        return [{"id": 1}] if offset < virtual_count else []

    monkeypatch.setattr(cbi, "fetch_page", fake_fetch_page)


def test_count_datasets_uses_anchor_from_state(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=42)

    state = {"users": {"anchor_updated_at": "2026-05-01T00:00:00Z"}}
    cbi.count_datasets(["users"], state)

    assert "42" in capsys.readouterr().out
    # the running job's anchor must be the one actually used to probe Coupa
    assert all(c["anchor_ts"] == "2026-05-01T00:00:00Z" for c in calls)


def test_count_datasets_falls_back_to_per_dataset_state_file(monkeypatch, tmp_path, capsys):
    # A supervised run writes per-dataset state files; a --count over the
    # shared (empty) state must still find the run's anchor + progress there.
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    calls = []
    _install_fake_fetch(monkeypatch, calls, virtual_count=100)

    (tmp_path / "coupa_import_state_users.json").write_text(json.dumps(
        {"users": {"anchor_updated_at": "2026-06-15T12:00:00Z",
                   "total_processed": 25}}))

    cbi.count_datasets(["users"], {})  # shared state has no entry for users

    assert all(c["anchor_ts"] == "2026-06-15T12:00:00Z" for c in calls)
    assert "25.0%" in capsys.readouterr().out   # progress found in the fallback
