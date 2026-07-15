"""Partition planning: count-balanced id boundaries via rank probes,
pre-seeded state files, disjoint full coverage (data-integrity invariant)."""
import json

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import FakeCoupa, write_config


def _env(monkeypatch, tmp_path, ids):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    coupa = FakeCoupa(ids)
    coupa.install(monkeypatch)
    return coupa


GAPPY = ([*range(1, 401)] +                 # dense low ids
         [*range(100_000, 100_200)] +       # island
         [*range(9_000_000, 9_000_400)])    # dense high ids (1000 total)


def test_partitions_cover_exactly_and_balance(monkeypatch, tmp_path):
    monkeypatch.setattr(cbi, "MIN_PARTITION", 10)
    _env(monkeypatch, tmp_path, GAPPY)
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    assert [p["index"] for p in parts] == [1, 2, 3, 4]
    assert all(p["of"] == 4 for p in parts)
    # disjoint, ordered, covering: slice k is (id_gt, id_lte]
    for a, b in zip(parts, parts[1:]):
        assert a["id_lte"] == b["id_gt"]
    assert parts[0]["id_gt"] == 0
    assert parts[-1]["id_lte"] == max(GAPPY)
    # count-balanced despite the gaps: each slice holds ~250 of 1000 ids
    for p in parts:
        n = sum(1 for i in GAPPY if p["id_gt"] < i <= p["id_lte"])
        assert 240 <= n <= 260


def test_partitions_clamped_by_min_partition(monkeypatch, tmp_path):
    monkeypatch.setattr(cbi, "MIN_PARTITION", 600)
    _env(monkeypatch, tmp_path, GAPPY)      # 1000 ids -> ceil(1000/600) = 2 max
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 8)
    assert len(parts) == 2


def test_single_worker_returns_no_partitions(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, GAPPY)      # default MIN_PARTITION=50k -> clamp to 1
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    assert parts == []


def test_planning_aborts_when_dataset_shrinks(monkeypatch, tmp_path):
    # a record updated during the minutes-long bisection leaves the anchored
    # set; a rank probe past the new end must abort the plan, not crash or
    # seed a bad plan
    monkeypatch.setattr(cbi, "MIN_PARTITION", 10)
    coupa = _env(monkeypatch, tmp_path, GAPPY)
    real = coupa.fetch_at_rank

    def shrunk(session, endpoint, anchor_ts, rank):
        if rank == 250:                      # first internal boundary (1000/4)
            return []
        return real(session, endpoint, anchor_ts, rank)

    monkeypatch.setattr(cbi, "fetch_at_rank", shrunk)
    with pytest.raises(SystemExit) as exc:
        cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    assert "re-plan" in str(exc.value)


def test_validate_partition_set_refuses_partial_plan(monkeypatch, tmp_path):
    # supervisor killed after seeding p1of2 but before p2of2: resuming the
    # partial plan would silently never crawl partition 2's id range
    monkeypatch.chdir(tmp_path)
    part1 = {"index": 1, "of": 2, "id_gt": 0, "id_lte": 100}
    cbi.seed_partition_state("users", part1, "t",
                             cbi.partition_state_path("users", 1, 2))
    with pytest.raises(SystemExit) as exc:
        cbi.validate_partition_set("users", cbi.find_partition_states("users"))
    msg = str(exc.value)
    assert "1 of 2" in msg and "elete" in msg   # Delete/delete-to-re-plan recovery


def test_validate_partition_set_refuses_mixed_worker_counts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cbi.seed_partition_state("users", {"index": 1, "of": 2, "id_gt": 0, "id_lte": 100},
                             "t", cbi.partition_state_path("users", 1, 2))
    cbi.seed_partition_state("users", {"index": 1, "of": 4, "id_gt": 0, "id_lte": 50},
                             "t", cbi.partition_state_path("users", 1, 4))
    with pytest.raises(SystemExit) as exc:
        cbi.validate_partition_set("users", cbi.find_partition_states("users"))
    assert "worker counts" in str(exc.value)


def test_validate_partition_set_accepts_complete_plan(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    parts = [{"index": 1, "of": 2, "id_gt": 0, "id_lte": 100},
             {"index": 2, "of": 2, "id_gt": 100, "id_lte": 200}]
    for p in parts:
        cbi.seed_partition_state("users", p, "t",
                                 cbi.partition_state_path("users", p["index"], 2))
    validated = cbi.validate_partition_set(
        "users", cbi.find_partition_states("users"))
    assert [p["index"] for p, _ in validated] == [1, 2]


def test_seed_partition_state_shape_and_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    part = {"index": 2, "of": 3, "id_gt": 100, "id_lte": 200}
    path = cbi.partition_state_path("users", 2, 3)
    assert path.name == "coupa_import_state_users_p2of3.json"
    cbi.seed_partition_state("users", part, "2026-07-15T00:00:00Z", path)
    st = json.loads(path.read_text())["users"]
    assert st["last_id"] == 201                 # id_lte + 1: nothing fetched yet
    assert st["partition"] == part
    assert st["total_processed"] == 0 and st["total_inserted"] == 0
    assert "completed" not in st
    assert cbi.find_partition_states("users") == [path]
    assert cbi.find_partition_states("suppliers") == []
