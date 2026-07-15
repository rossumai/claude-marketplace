import pytest

import coupa_bulk_import as cbi


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


def test_read_last_log_line(tmp_path):
    p = tmp_path / "x.log"
    assert cbi.read_last_log_line(p) == "(no log)"
    p.write_text("")
    assert cbi.read_last_log_line(p) == "(empty log)"
    p.write_text("first\nlast line\n")
    assert cbi.read_last_log_line(p) == "last line"


def test_build_child_cmd_minimal():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=False,
                              username=None, password=None)
    assert cmd[0] == sys.executable
    assert cmd[1] == "-u"
    assert cmd[2].endswith("coupa_bulk_import.py")
    assert cmd[3:] == ["--dataset", "users", "--config", "cfg.json"]


def test_build_child_cmd_full():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=True,
                              username="u@x.com", password="pw")
    assert "--resume" in cmd
    assert cmd[cmd.index("--username") + 1] == "u@x.com"
    assert cmd[cmd.index("--password") + 1] == "pw"
    assert "--limit" not in cmd


def test_build_child_cmd_inherits_no_unique_index_ok():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=False,
                              username=None, password=None,
                              no_unique_index_ok=True)
    assert "--no-unique-index-ok" in cmd


def test_build_child_cmd_omits_no_unique_index_ok_by_default():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=False,
                              username=None, password=None)
    assert "--no-unique-index-ok" not in cmd


import argparse


def _args(**kw):
    return argparse.Namespace(
        config=kw.get("config", "cfg.json"), resume=kw.get("resume", False),
        username=None, password=None, limit=None,
        poll_interval=kw.get("poll_interval", 0.05),
        max_restarts=kw.get("max_restarts", 2),
        no_unique_index_ok=kw.get("no_unique_index_ok", False),
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
                   no_unique_index_ok=False):
        flags.append(no_unique_index_ok)
        return _stub_completes(dataset)

    monkeypatch.setattr(cbi, "build_child_cmd", fake_build)
    code = cbi.supervise(["users"], _args(no_unique_index_ok=True))
    assert code == 0
    assert flags == [True]


def test_supervise_restores_sigterm_handler(monkeypatch, tmp_path):
    import signal
    before = signal.getsignal(signal.SIGTERM)
    _run_supervise(monkeypatch, tmp_path, {"users": [_stub_completes("users")]}, ["users"])
    assert signal.getsignal(signal.SIGTERM) is before


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


def test_main_rejects_supervise_with_limit(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--supervise", "--limit", "1"])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--limit" in str(exc.value)
    assert "--supervise" in str(exc.value)


def test_main_rejects_supervise_with_state_file(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["coupa_bulk_import.py", "--supervise", "--state-file", "x.json"])
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "--state-file" in str(exc.value)
    assert "--supervise" in str(exc.value)


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
