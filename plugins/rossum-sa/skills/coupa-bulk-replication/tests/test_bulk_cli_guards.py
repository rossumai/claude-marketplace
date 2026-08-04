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
    (["--id-range", "1:100", "--probe"], None, ["--id-range", "--probe"]),
    (["--id-range", "1:100", "--smoke"], None, ["--id-range", "--smoke"]),
    (["--id-range", "100:1"], None, ["--id-range"]),           # LO > HI
    (["--rate", "0"], None, ["--rate"]),
    (["--probe", "--limit", "5"], None, ["--limit", "--probe"]),
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


def test_supervised_child_of_partitioned_dataset_survives_the_workers_guard(
        monkeypatch, tmp_path):
    """A child spawned for a workers>1 dataset must be able to actually START.

    Regression: build_child_cmd used to omit --workers, so every partition
    child re-read the config, saw workers > 1, tripped the guard above and
    exited 1 on startup -- the supervisor then crash-looped it to give-up.
    No dataset whose CONFIG set workers > 1 could run, making the partitioning
    feature (probe suggestions, plan_partitions, the per-child rate split)
    unreachable by its documented route. (`--supervise --workers N` over a
    config without workers keys was unaffected: the guard reads the config
    value and is skipped when --workers is passed.)

    The two halves were each covered in isolation -- build_child_cmd's output
    and the guard's message -- but nothing asserted they compose. This feeds
    one into the other, which is the only way to catch it. Both asserts below
    are load-bearing: reverting --workers 1 fails the argv-shape one, and
    widening the guard to `if not args.supervise:` fails the composition one.
    """
    datasets = {"users": {"endpoint": "api/users", "collection": "users",
                          "id_key": "id", "scope": "s", "fields": ["id"],
                          "workers": 4}}
    cfg = str(write_config(tmp_path, datasets=datasets))
    state = tmp_path / "coupa_import_state_users_p1of4.json"

    # both shapes the supervisor spawns: a partition child (own state file)
    # and a whole-dataset child. Each used to trip the guard.
    for state_file in (state, None):
        cmd = cbi.build_child_cmd("users", cfg, resume=True, username=None,
                                  password=None, state_file=state_file,
                                  rate=1.0)

        # the child says what it is: a serial crawler, never partitioning again
        assert cmd[cmd.index("--workers") + 1] == "1"

        # and that argv must not trip main()'s guard. Slice off the interpreter
        # prefix by locating the script itself rather than by a fixed index.
        script_at = next(i for i, a in enumerate(cmd)
                         if a.endswith("coupa_bulk_import.py"))
        monkeypatch.setattr(sys, "argv",
                            ["coupa_bulk_import.py", *cmd[script_at + 1:]])
        sentinel = RuntimeError("reached the import path")

        def _boom(*a, **kw):
            raise sentinel

        # stop right after the guard so the test never touches Coupa or DS
        monkeypatch.setattr(cbi, "import_dataset", _boom)
        with pytest.raises(RuntimeError) as exc:
            cbi.main()
        assert exc.value is sentinel, (
            f"child argv (state_file={state_file!r}) was rejected before "
            "reaching the import path -- the workers guard fired on a "
            "supervisor-spawned child")
