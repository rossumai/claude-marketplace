"""main()'s refusal guards, consolidated: every unsafe flag combination (and
the tokenless-config case) must exit with an error naming the conflict."""
import sys

import pytest

from bulk_helpers import write_config

import coupa_bulk_import as cbi

# (argv tail, config kwargs or None for no --config, message fragments)
GUARDS = [
    (["--smoke", "--supervise"], None, ["--smoke", "--supervise"]),
    (["--smoke", "--resume"], None, ["--smoke", "--resume"]),
    (["--smoke", "--probe"], None, ["--smoke", "--probe"]),
    (["--smoke", "2", "--limit", "5"], None, ["--smoke", "--limit"]),
    (["--supervise", "--limit", "1"], None, ["--limit", "--supervise"]),
    (["--supervise", "--state-file", "x.json"], None, ["--state-file", "--supervise"]),
    (["--probe", "--supervise"], None, ["--probe", "--supervise"]),
    # a smoke run must fit in one DS batch
    (["--smoke", "5"], {"ds_batch_size": 2}, ["--smoke", "ds_batch_size"]),
    # DS writes need a token or credentials; --probe (Coupa-only) is exempt
    ([], {"token": ""}, ["rossum.token", "--username"]),
    (["--workers", "2"], None, ["--workers", "--supervise"]),
    # NOTE: no --supervise here — pairing it would trip the older
    # probe/supervise guard first and assert the wrong message
    (["--workers", "2", "--probe"], None, ["--workers", "--probe"]),
    (["--workers", "2", "--smoke"], None, ["--workers", "--smoke"]),
    (["--id-range", "1:100", "--supervise"], None, ["--id-range", "--supervise"]),
    (["--id-range", "100:1"], None, ["--id-range"]),           # LO > HI
    # multi-dataset --id-range fires AFTER load_config -> needs a real config
    # with two datasets so keys resolves to more than one
    (["--id-range", "1:100"],
     {"datasets": {
         "users": {"endpoint": "api/users", "collection": "users",
                   "id_key": "id", "scope": "s", "fields": ["id"]},
         "suppliers": {"endpoint": "api/suppliers", "collection": "suppliers",
                       "id_key": "id", "scope": "s", "fields": ["id"]}}},
     ["--id-range", "single"]),
]


@pytest.mark.parametrize(
    "argv_tail,cfg_kw,fragments", GUARDS,
    ids=[" ".join(g[0]) or "tokenless-config" for g in GUARDS])
def test_main_refuses_unsafe_invocations(monkeypatch, tmp_path,
                                         argv_tail, cfg_kw, fragments):
    argv = ["coupa_bulk_import.py", *argv_tail]
    if cfg_kw is not None:
        argv += ["--config", str(write_config(tmp_path, **cfg_kw))]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    msg = str(exc.value)
    for fragment in fragments:
        assert fragment in msg


def test_config_workers_require_supervise(monkeypatch, tmp_path):
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "id", "scope": "s", "fields": ["id"],
                          "workers": 2}}
    argv = ["coupa_bulk_import.py",
            "--config", str(write_config(tmp_path, datasets=datasets))]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cbi.main()
    assert "workers" in str(exc.value) and "--supervise" in str(exc.value)
