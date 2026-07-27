"""Partition planning: count-balanced id boundaries via rank probes,
pre-seeded state files, disjoint full coverage (data-integrity invariant)."""
import json

import pytest

import coupa_bulk_import as cbi
from bulk_helpers import FakeCoupa, write_config


def _env(monkeypatch, tmp_path, ids, extra_params=None, min_partition=None):
    monkeypatch.chdir(tmp_path)
    cbi.load_config(write_config(tmp_path, extra_params=extra_params,
                                 min_partition=min_partition))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    coupa = FakeCoupa(ids)
    coupa.install(monkeypatch)
    return coupa


GAPPY = ([*range(1, 401)] +                 # dense low ids
         [*range(100_000, 100_200)] +       # island
         [*range(9_000_000, 9_000_400)])    # dense high ids (1000 total)


def test_partitions_cover_exactly_and_balance(monkeypatch, tmp_path):
    # load_config (inside _env) now sets MIN_PARTITION from config too, so
    # the monkeypatch override must be applied AFTER _env, not before it
    _env(monkeypatch, tmp_path, GAPPY)
    monkeypatch.setattr(cbi, "MIN_PARTITION", 10)
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
    # FLOOR semantics: no partition ever holds fewer than MIN_PARTITION
    # records — 1000 ids / 500 floor = at most 2 workers, even when 8 asked
    _env(monkeypatch, tmp_path, GAPPY)
    monkeypatch.setattr(cbi, "MIN_PARTITION", 500)
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 8)
    assert len(parts) == 2
    for p in parts:
        n = sum(1 for i in GAPPY if p["id_gt"] < i <= p["id_lte"])
        # floor clamps the AVERAGE (C/W >= MIN_PARTITION); rank-boundary
        # rounding makes individual slices vary by at most one record
        assert abs(n - 500) <= 1


def test_single_worker_returns_no_partitions(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, GAPPY)      # default MIN_PARTITION=50k -> clamp to 1
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    assert parts == []


def test_config_min_partition_changes_the_planner_clamp(monkeypatch, tmp_path):
    # the config knob reaches plan_partitions with NO monkeypatch involved —
    # this is the path a real customer config exercises (the field case: at
    # 197k records the 50k default floor capped workers at 3; a config
    # override changes what "the floor" even means, without touching code)
    _env(monkeypatch, tmp_path, GAPPY, min_partition=10)
    anchor, parts = cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    assert len(parts) == 4


def test_plan_partitions_passes_dataset_extra_params_to_probes(monkeypatch, tmp_path):
    # THE REGRESSION TEST: partition boundaries must be computed over the
    # SAME filtered slice --probe counts and the run crawls — omitting
    # extra_params here would balance boundaries over the unfiltered (much
    # larger) population and hand partition children the wrong id ranges.
    extra = {"created-at[gt_or_eq]": "2026-01-01T00:00:00Z"}
    coupa = _env(monkeypatch, tmp_path, GAPPY, extra_params=extra)
    monkeypatch.setattr(cbi, "MIN_PARTITION", 10)
    rank_seen, page_seen = [], []
    real_rank, real_page = coupa.fetch_at_rank, coupa.fetch_page

    def rank_spy(session, endpoint, anchor_ts, rank, extra_params=None):
        rank_seen.append(extra_params)
        return real_rank(session, endpoint, anchor_ts, rank,
                         extra_params=extra_params)

    def page_spy(session, endpoint, fields, anchor_ts, *, before_id=None,
                id_gt=None, limit=None, extra_params=None):
        page_seen.append(extra_params)
        return real_page(session, endpoint, fields, anchor_ts, before_id=before_id,
                         id_gt=id_gt, limit=limit, extra_params=extra_params)

    monkeypatch.setattr(cbi, "fetch_at_rank", rank_spy)
    monkeypatch.setattr(cbi, "fetch_page", page_spy)
    cbi.plan_partitions("users", cbi.DATASETS["users"], 4)
    # covers BOTH the bisection lambda's rank probes and the per-boundary
    # rank probes in the edges loop — both go through fetch_at_rank
    assert rank_seen and all(e == extra for e in rank_seen)
    # the desc top-edge fetch (limit=1) must carry it too
    assert page_seen and all(e == extra for e in page_seen)


def test_partitioned_preflight_id_key_and_populated(monkeypatch, tmp_path):
    """Partition children always run --resume, so the supervisor's one-time
    preflight owns the fresh-run guards: a misconfigured id_key must abort
    BEFORE planning; a populated collection warns but proceeds."""
    from bulk_helpers import FakeDS
    monkeypatch.chdir(tmp_path)
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "number", "scope": "s", "fields": ["number"],
                          "workers": 2}}
    cbi.load_config(write_config(tmp_path, datasets=datasets))
    monkeypatch.setattr(cbi, "get_coupa_token", lambda scope: "t")
    coupa = FakeCoupa([1, 2, 3])            # records carry id but no 'number'
    coupa.install(monkeypatch)
    ds = FakeDS(id_key="number", preloaded=range(1, 200))

    with pytest.raises(SystemExit) as exc:
        cbi.partitioned_preflight("users", cbi.DATASETS["users"], ds)
    assert "number" in str(exc.value)

    datasets["users"]["id_key"] = "id"
    cbi.load_config(write_config(tmp_path, datasets=datasets))
    # populated collection: warns, does NOT raise
    cbi.partitioned_preflight("users", cbi.DATASETS["users"], ds)


def test_planning_aborts_when_dataset_shrinks(monkeypatch, tmp_path):
    # a record updated during the minutes-long bisection leaves the anchored
    # set; a rank probe past the new end must abort the plan, not crash or
    # seed a bad plan
    coupa = _env(monkeypatch, tmp_path, GAPPY)
    monkeypatch.setattr(cbi, "MIN_PARTITION", 10)
    real = coupa.fetch_at_rank

    def shrunk(session, endpoint, anchor_ts, rank, extra_params=None):
        if rank == 250:                      # first internal boundary (1000/4)
            return []
        return real(session, endpoint, anchor_ts, rank, extra_params=extra_params)

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
