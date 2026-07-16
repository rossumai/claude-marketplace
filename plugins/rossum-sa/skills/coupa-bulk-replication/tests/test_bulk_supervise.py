import pytest

import coupa_bulk_import as cbi


@pytest.fixture(autouse=True)
def _reset_datasets(monkeypatch):
    # supervise() now consults DATASETS (per-dataset workers); tests that
    # never load a config must not inherit another test's datasets
    monkeypatch.setattr(cbi, "DATASETS", {})


def test_decision_table():
    # (completed, child_alive, restarts, max_restarts) -> action
    assert cbi.decide(True,  True,  0, 3) == "done"      # state file wins over liveness
    assert cbi.decide(True,  False, 3, 3) == "done"
    assert cbi.decide(False, True,  0, 3) == "wait"
    assert cbi.decide(False, True,  3, 3) == "wait"      # alive is never killed
    assert cbi.decide(False, False, 0, 3) == "relaunch"
    assert cbi.decide(False, False, 2, 3) == "relaunch"
    assert cbi.decide(False, False, 3, 3) == "give_up"
    assert cbi.decide(False, False, 0, 0) == "give_up"   # max-restarts 0 = never relaunch


import json
import sys


def test_state_is_completed(tmp_path):
    p = tmp_path / "s.json"
    assert cbi.state_is_completed(p, "users") is False            # missing file
    p.write_text("not json{")
    assert cbi.state_is_completed(p, "users") is False            # malformed
    p.write_text(json.dumps({"users": {"total_processed": 5}}))
    assert cbi.state_is_completed(p, "users") is False            # no flag
    p.write_text(json.dumps({"users": {"completed": True}}))
    assert cbi.state_is_completed(p, "users") is True
    assert cbi.state_is_completed(p, "suppliers") is False        # other key
    p.write_text(json.dumps({"users": "in progress"}))            # wrong shape
    assert cbi.state_is_completed(p, "users") is False


def test_build_child_cmd():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=True,
                              username="u@x.com", password="pw")
    assert cmd[0] == sys.executable
    assert cmd[1] == "-u"                        # unbuffered → live child logs
    assert cmd[2].endswith("coupa_bulk_import.py")
    assert "--resume" in cmd
    assert cmd[cmd.index("--username") + 1] == "u@x.com"
    assert cmd[cmd.index("--password") + 1] == "pw"
    assert "--limit" not in cmd                  # refused with --supervise
    assert "--no-unique-index-ok" not in cmd     # only when explicitly given


def test_build_child_cmd_inherits_no_unique_index_ok():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=False,
                              username=None, password=None,
                              no_unique_index_ok=True)
    assert "--no-unique-index-ok" in cmd


import argparse


def _args(**kw):
    return argparse.Namespace(
        config=kw.get("config", "cfg.json"), resume=kw.get("resume", False),
        username=None, password=None, limit=None,
        poll_interval=kw.get("poll_interval", 0.05),
        max_restarts=kw.get("max_restarts", 2),
        no_unique_index_ok=kw.get("no_unique_index_ok", False),
        workers=kw.get("workers", None), rate=kw.get("rate", None),
    )


def _stub_completes(key):
    """Child cmd that writes a completed state file for `key` and exits 0."""
    code = (f"import json; json.dump({{'{key}': {{'completed': True}}}}, "
            f"open('coupa_import_state_{key}.json', 'w'))")
    return [sys.executable, "-c", code]


_STUB_DIES = [sys.executable, "-c", "import sys; print('boom'); sys.exit(1)"]


def _run_supervise(monkeypatch, tmp_path, cmd_plan, keys, **argkw):
    """cmd_plan: {dataset: [cmd_for_launch_0, cmd_for_launch_1, ...]}
    (last entry repeats). Returns (exit_code, launch log)."""
    monkeypatch.chdir(tmp_path)
    launches = []

    def fake_build(dataset, config, *, resume, username, password, **kw):
        n = sum(1 for d, _ in launches if d == dataset)
        launches.append((dataset, resume))
        plan = cmd_plan[dataset]
        return plan[min(n, len(plan) - 1)]

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    return cbi.supervise(keys, _args(**argkw)), launches


def test_supervise_happy_path(monkeypatch, tmp_path):
    code, launches = _run_supervise(
        monkeypatch, tmp_path, {"users": [_stub_completes("users")]}, ["users"])
    assert code == 0
    assert launches == [("users", False)]
    assert (tmp_path / "logs").is_dir()


def test_supervise_relaunches_with_resume_then_completes(monkeypatch, tmp_path):
    code, launches = _run_supervise(
        monkeypatch, tmp_path,
        {"users": [_STUB_DIES, _stub_completes("users")]}, ["users"])
    assert code == 0
    assert launches == [("users", False), ("users", True)]   # relaunch carries --resume


def test_supervise_gives_up_after_max_restarts(monkeypatch, tmp_path):
    code, launches = _run_supervise(
        monkeypatch, tmp_path, {"users": [_STUB_DIES]}, ["users"], max_restarts=2)
    assert code == 1
    assert len(launches) == 3                                 # initial + 2 restarts


def test_supervise_resume_skips_completed(monkeypatch, tmp_path):
    (tmp_path / "coupa_import_state_users.json").write_text(
        json.dumps({"users": {"completed": True}}))
    code, launches = _run_supervise(
        monkeypatch, tmp_path,
        {"users": [_STUB_DIES], "suppliers": [_stub_completes("suppliers")]},
        ["users", "suppliers"], resume=True)
    assert code == 0
    assert launches == [("suppliers", True)]                  # users never launched


def test_supervise_passes_no_unique_index_ok_to_children(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    flags = []

    def fake_build(dataset, config, *, resume, username, password,
                   no_unique_index_ok=False, state_file=None, rate=None):
        flags.append(no_unique_index_ok)
        return _stub_completes(dataset)

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args(no_unique_index_ok=True))
    assert code == 0
    assert flags == [True]


def test_supervise_interrupt_during_startup_terminates_and_returns_130(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    slow_child = [sys.executable, "-c", "import time; time.sleep(60)"]
    calls = []
    spawned = []
    real_popen = cbi.subprocess.Popen

    def capturing_popen(*a, **kw):
        p = real_popen(*a, **kw)
        spawned.append(p)
        return p

    def fake_build(dataset, config, *, resume, username, password, **kw):
        calls.append(dataset)
        if len(calls) == 2:
            raise KeyboardInterrupt   # simulates SIGINT arriving mid-startup
        return slow_child

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    monkeypatch.setattr(cbi.subprocess, "Popen", capturing_popen)
    code = cbi.supervise(["users", "suppliers"], _args())
    assert code == 130
    # the first child must have been terminated, not orphaned
    assert len(spawned) == 1
    spawned[0].wait(timeout=5)          # deterministic: SIGTERM must land
    assert spawned[0].poll() is not None


# ── partition units ──────────────────────────────────────────────────────────

def _partitioned_env(monkeypatch, tmp_path, workers=2):
    """Config with users.workers=N; plan_partitions stubbed (no Coupa)."""
    from bulk_helpers import write_config
    monkeypatch.chdir(tmp_path)
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "id", "scope": "s", "fields": ["id"],
                          "workers": workers}}
    cbi.load_config(write_config(tmp_path, datasets=datasets))
    parts = [{"index": k, "of": workers,
              "id_gt": (k - 1) * 100, "id_lte": k * 100}
             for k in range(1, workers + 1)]
    monkeypatch.setattr(cbi, "plan_partitions",
                        lambda key, cfg, w: ("2026-07-15T00:00:00Z", parts))
    # preflight is Coupa/DS-touching — pinned by its own test in
    # test_bulk_partitions; lifecycle tests stub it out
    monkeypatch.setattr(cbi, "partitioned_preflight", lambda *a, **kw: None)
    return parts


def test_effective_workers_cli_overrides_config(monkeypatch, tmp_path):
    # an operator's emergency "--workers 1" must beat the config value
    _partitioned_env(monkeypatch, tmp_path, workers=2)
    assert cbi.effective_workers("users", None) == 2   # config value
    assert cbi.effective_workers("users", 1) == 1      # explicit serialize
    assert cbi.effective_workers("users", 8) == 8


def _stub_completes_partition(key, path):
    code = (f"import json; json.dump({{'{key}': {{'completed': True}}}}, "
            f"open('{path}', 'w'))")
    return [sys.executable, "-c", code]


def test_supervise_spawns_one_child_per_partition(monkeypatch, tmp_path):
    _partitioned_env(monkeypatch, tmp_path, workers=2)
    launched = []

    def fake_build(dataset, config, *, resume, username, password,
                   no_unique_index_ok=False, state_file=None, rate=None):
        # read the state AT SPAWN TIME — the plan must be pre-seeded before
        # any child starts (the stub child overwrites the file later)
        seeded = json.loads(state_file.read_text())[dataset]
        launched.append((dataset, resume, str(state_file), rate, seeded))
        return _stub_completes_partition(dataset, state_file)

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args())
    assert code == 0
    assert len(launched) == 2
    # partition children always launch with --resume (pre-seeded state is
    # the single source of truth for the plan)
    assert all(resume is True for _, resume, _, _, _ in launched)
    assert {p for _, _, p, _, _ in launched} == {
        "coupa_import_state_users_p1of2.json",
        "coupa_import_state_users_p2of2.json"}
    # per-child rate = COUPA_MAX_RPS / units = 20/2
    assert all(r == pytest.approx(10.0) for _, _, _, r, _ in launched)
    # plan was pre-seeded before spawning
    assert {s["partition"]["index"] for _, _, _, _, s in launched} == {1, 2}


def test_supervise_reuses_existing_partition_files_without_replanning(
        monkeypatch, tmp_path):
    parts = _partitioned_env(monkeypatch, tmp_path, workers=2)
    # simulate a previous run: seed both partition files, one completed
    for p in parts:
        path = cbi.partition_state_path("users", p["index"], p["of"])
        cbi.seed_partition_state("users", p, "2026-07-01T00:00:00Z", path)
    done = cbi.partition_state_path("users", 1, 2)
    st = json.loads(done.read_text())
    st["users"]["completed"] = True
    done.write_text(json.dumps(st))

    monkeypatch.setattr(cbi, "plan_partitions",
                        lambda *a: pytest.fail("must not re-plan"))

    launched = []
    rates = []

    def fake_build(dataset, config, *, resume, username, password,
                   no_unique_index_ok=False, state_file=None, rate=None):
        launched.append(str(state_file))
        rates.append(rate)
        return _stub_completes_partition(dataset, state_file)

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args(resume=True))
    assert code == 0
    assert launched == ["coupa_import_state_users_p2of2.json"]  # p1 skipped
    # the lone survivor gets the WHOLE aggregate — completed units must not
    # count in the rate split (they consume no budget)
    assert rates == [pytest.approx(20.0)]


def test_supervise_rate_override_splits_that_aggregate(monkeypatch, tmp_path):
    # --rate 10 must become the AGGREGATE for the run (config cap ignored):
    # an operator lowering the cap for live webhooks must actually lower it
    _partitioned_env(monkeypatch, tmp_path, workers=2)
    rates = []

    def fake_build(dataset, config, *, resume, username, password,
                   no_unique_index_ok=False, state_file=None, rate=None):
        rates.append(rate)
        return _stub_completes_partition(dataset, state_file)

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args(rate=10.0))
    assert code == 0
    assert rates == [pytest.approx(5.0)] * 2


def test_supervise_refuses_partitioning_over_unpartitioned_progress(
        monkeypatch, tmp_path):
    _partitioned_env(monkeypatch, tmp_path, workers=2)
    (tmp_path / "coupa_import_state_users.json").write_text(json.dumps(
        {"users": {"last_id": 500, "total_processed": 100}}))
    with pytest.raises(SystemExit) as exc:
        cbi.supervise(["users"], _args())
    assert "users" in str(exc.value)


def test_supervise_partition_exit_code_composition(monkeypatch, tmp_path):
    _partitioned_env(monkeypatch, tmp_path, workers=2)

    def fake_build(dataset, config, *, resume, username, password,
                   no_unique_index_ok=False, state_file=None, rate=None):
        if "p1of2" in str(state_file):
            return _stub_completes_partition(dataset, state_file)
        return _STUB_DIES

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args(max_restarts=0))
    assert code == 1                          # one partition gave up -> failure


def test_supervise_unexpected_exception_terminates_children_and_reraises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    slow_child = [sys.executable, "-c", "import time; time.sleep(60)"]
    calls = []
    spawned = []
    real_popen = cbi.subprocess.Popen

    def capturing_popen(*a, **kw):
        p = real_popen(*a, **kw)
        spawned.append(p)
        return p

    def fake_build(dataset, config, *, resume, username, password, **kw):
        calls.append(dataset)
        if len(calls) == 2:
            raise OSError("boom")
        return slow_child

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    monkeypatch.setattr(cbi.subprocess, "Popen", capturing_popen)
    with pytest.raises(OSError):
        cbi.supervise(["users", "suppliers"], _args())
    # the first child must have been terminated, not orphaned
    assert len(spawned) == 1
    spawned[0].wait(timeout=5)          # deterministic: SIGTERM must land
    assert spawned[0].poll() is not None
