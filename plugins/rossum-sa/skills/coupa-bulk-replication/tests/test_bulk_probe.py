"""--probe: exact counts via offset bisection + throughput sampling +
advisory workers suggestion (read-only, writes nothing)."""
import json

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import FakeCoupa, write_config


# ── _bisect_count (pure — unchanged machinery) ───────────────────────────────

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


# ── suggest_workers (pure) ───────────────────────────────────────────────────

def test_suggest_workers_scales_with_est_hours():
    # 4.77M at 20 rec/s ≈ 66h -> ceil(66/4)=17 -> capped at 8
    assert cbi.suggest_workers(4_770_000, 20.0) == 8
    # 4.77M at 700 rec/s ≈ 1.9h -> under target -> 1
    assert cbi.suggest_workers(4_770_000, 700.0) == 1
    # 200k at 10 rec/s ≈ 5.6h -> 2 by time, min-partition allows 4 -> 2
    assert cbi.suggest_workers(200_000, 10.0) == 2


def test_suggest_workers_min_partition_clamp():
    # 60k records: by_size = ceil(60k/50k) = 2, even if time wants more
    assert cbi.suggest_workers(60_000, 0.1) == 2


def test_suggest_workers_degenerate_inputs():
    assert cbi.suggest_workers(0, 100.0) == 1
    assert cbi.suggest_workers(1000, 0.0) == 1


# ── probe_datasets: anchor reuse + report ────────────────────────────────────

def _probe_env(monkeypatch, tmp_path, ids):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    coupa = FakeCoupa(ids)
    coupa.install(monkeypatch)
    return coupa


def test_probe_uses_anchor_from_state(monkeypatch, tmp_path, capsys):
    coupa = _probe_env(monkeypatch, tmp_path, list(range(1, 43)))
    anchors = []
    real = coupa.fetch_at_rank

    def spy(session, endpoint, anchor_ts, rank):
        anchors.append(anchor_ts)
        return real(session, endpoint, anchor_ts, rank)

    monkeypatch.setattr(cbi, "fetch_at_rank", spy)
    state = {"users": {"anchor_updated_at": "2026-05-01T00:00:00Z"}}
    cbi.probe_datasets(["users"], state)
    assert "42" in capsys.readouterr().out
    # the running job's anchor must be the one actually used to probe Coupa
    assert anchors and all(a == "2026-05-01T00:00:00Z" for a in anchors)


def test_probe_falls_back_to_per_dataset_state_file(monkeypatch, tmp_path, capsys):
    # A supervised run writes per-dataset state files; a --probe over the
    # shared (empty) state must still find the run's anchor + progress there.
    _probe_env(monkeypatch, tmp_path, list(range(1, 101)))
    (tmp_path / "coupa_import_state_users.json").write_text(json.dumps(
        {"users": {"anchor_updated_at": "2026-06-15T12:00:00Z",
                   "total_processed": 25}}))
    cbi.probe_datasets(["users"], {})
    out = capsys.readouterr().out
    assert "100" in out
    assert "25.0%" in out   # progress found in the fallback


def test_probe_prints_config_ready_suggestion(monkeypatch, tmp_path, capsys):
    # 60k records at a slow measured rate: by_size allows 2 workers,
    # by_time wants more -> suggestion 2 (see suggest_workers math)
    _probe_env(monkeypatch, tmp_path, list(range(1, 60_001)))
    monkeypatch.setattr(cbi, "measure_rate", lambda *a, **kw: 0.01)
    cbi.probe_datasets(["users"], {})
    out = capsys.readouterr().out
    assert '"users": 2' in out and "workers" in out
