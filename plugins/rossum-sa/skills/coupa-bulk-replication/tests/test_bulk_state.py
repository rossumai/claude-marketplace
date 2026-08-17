import pytest

import coupa_bulk_import as cbi


def test_save_state_round_trips_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "state.json"
    state = {"users": {"offset": 5000, "completed": False}}
    cbi.save_state(state, path)
    assert cbi.load_state(path) == state
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_load_state_missing_file_returns_empty_dict(tmp_path):
    path = tmp_path / "nope.json"
    assert cbi.load_state(path) == {}


def test_load_state_truncated_json_raises_systemexit_naming_path(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"users": {"off')
    with pytest.raises(SystemExit) as exc:
        cbi.load_state(path)
    assert str(path) in str(exc.value)
