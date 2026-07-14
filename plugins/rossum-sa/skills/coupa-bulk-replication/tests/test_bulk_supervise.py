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
                              username=None, password=None, limit=None)
    assert cmd[0] == sys.executable
    assert cmd[1] == "-u"
    assert cmd[2].endswith("coupa_bulk_import.py")
    assert cmd[3:] == ["--dataset", "users", "--config", "cfg.json"]


def test_build_child_cmd_full():
    cmd = cbi.build_child_cmd("users", "cfg.json", resume=True,
                              username="u@x.com", password="pw", limit=5)
    assert "--resume" in cmd
    assert cmd[cmd.index("--username") + 1] == "u@x.com"
    assert cmd[cmd.index("--password") + 1] == "pw"
    assert cmd[cmd.index("--limit") + 1] == "5"


import argparse


def _args(**kw):
    return argparse.Namespace(
        config=kw.get("config", "cfg.json"), resume=kw.get("resume", False),
        username=None, password=None, limit=None,
        poll_interval=kw.get("poll_interval", 0.05),
        max_restarts=kw.get("max_restarts", 2),
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

    def fake_build(dataset, config, *, resume, username, password, limit):
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
