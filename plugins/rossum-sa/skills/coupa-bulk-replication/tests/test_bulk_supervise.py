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
