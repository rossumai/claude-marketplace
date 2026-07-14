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
