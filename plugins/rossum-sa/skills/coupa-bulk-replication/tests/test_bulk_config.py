import pytest
from bulk_helpers import write_config

import coupa_bulk_import as cbi


def test_credentials_are_stripped(tmp_path):
    path = write_config(tmp_path, client_id=" cid ", client_secret="sec\n", token=" tok\t")
    cbi.load_config(path)
    assert cbi.COUPA_CLIENT_ID == "cid"
    assert cbi.COUPA_CLIENT_SECRET == "sec"
    assert cbi.ROSSUM_TOKEN == "tok"


def test_duplicate_collection_rejected(tmp_path):
    path = write_config(tmp_path, datasets={
        "users": {"endpoint": "api/users", "collection": "shared",
                  "id_key": "id", "scope": "s", "fields": ["id"]},
        "suppliers": {"endpoint": "api/suppliers", "collection": "shared",
                      "id_key": "id", "scope": "s", "fields": ["id"]},
    })
    with pytest.raises(SystemExit) as exc:
        cbi.load_config(path)
    assert "shared" in str(exc.value)
    assert "users" in str(exc.value) and "suppliers" in str(exc.value)


def test_missing_collection_gives_clear_error(tmp_path):
    path = write_config(tmp_path, datasets={
        "users": {"endpoint": "api/users", "id_key": "id", "scope": "s", "fields": ["id"]},
        "suppliers": {"endpoint": "api/suppliers", "id_key": "id", "scope": "s",
                      "fields": ["id"]},
    })
    with pytest.raises(SystemExit) as exc:
        cbi.load_config(path)
    msg = str(exc.value)
    assert "users" in msg
    assert "has no collection set" in msg
    assert "''" not in msg  # not the confusing duplicate-collection message


def test_config_path_recorded(tmp_path):
    path = write_config(tmp_path)
    cbi.load_config(path)
    assert cbi.CONFIG_PATH == path
