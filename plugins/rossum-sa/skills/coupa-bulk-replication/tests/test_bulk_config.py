import json

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


def test_config_max_rps_default_and_override(tmp_path):
    cbi.load_config(write_config(tmp_path))
    assert cbi.COUPA_MAX_RPS == 20.0
    cfg = json.loads((tmp_path / "coupa_bulk_import.config.json").read_text())
    cfg["coupa"]["max_requests_per_second"] = 12
    p = tmp_path / "c2.json"
    p.write_text(json.dumps(cfg))
    cbi.load_config(p)
    assert cbi.COUPA_MAX_RPS == 12.0


@pytest.mark.parametrize("bad", [0, -1, "four", 2.5, True])
def test_config_rejects_bad_workers(tmp_path, bad):
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "id", "scope": "s", "fields": ["id"],
                          "workers": bad}}
    with pytest.raises(SystemExit) as exc:
        cbi.load_config(write_config(tmp_path, datasets=datasets))
    assert "workers" in str(exc.value)


def test_config_accepts_valid_workers(tmp_path):
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "id", "scope": "s", "fields": ["id"],
                          "workers": 4}}
    cbi.load_config(write_config(tmp_path, datasets=datasets))
    assert cbi.DATASETS["users"]["workers"] == 4


def test_config_rejects_dataset_extra_params_with_reserved_key(tmp_path):
    # a dataset's extra_params overriding a cursor/anchor/projection key
    # must fail loudly at startup, naming both the dataset and the key
    with pytest.raises(SystemExit) as exc:
        cbi.load_config(write_config(tmp_path, extra_params={"order_by": "name"}))
    msg = str(exc.value)
    assert "users" in msg
    assert "order_by" in msg


def test_config_accepts_valid_extra_params(tmp_path):
    extra = {"created-at[gt_or_eq]": "2026-01-01T00:00:00Z", "active": "true"}
    cbi.load_config(write_config(tmp_path, extra_params=extra))
    assert cbi.DATASETS["users"]["extra_params"] == extra


def test_config_min_partition_default_and_override(tmp_path):
    # top-level knob, sits beside ds_batch_size — same default/override
    # pattern as COUPA_MAX_RPS above
    cbi.load_config(write_config(tmp_path))
    assert cbi.MIN_PARTITION == 50_000
    cbi.load_config(write_config(tmp_path, min_partition=25_000))
    assert cbi.MIN_PARTITION == 25_000


@pytest.mark.parametrize("bad", [0, -1, "big", 2.5, True])
def test_config_rejects_bad_min_partition(tmp_path, bad):
    with pytest.raises(SystemExit) as exc:
        cbi.load_config(write_config(tmp_path, min_partition=bad))
    assert "min_partition" in str(exc.value)
