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
    (["--smoke", "--count"], None, ["--smoke", "--count"]),
    (["--smoke", "2", "--limit", "5"], None, ["--smoke", "--limit"]),
    (["--supervise", "--limit", "1"], None, ["--limit", "--supervise"]),
    (["--supervise", "--state-file", "x.json"], None, ["--state-file", "--supervise"]),
    (["--count", "--supervise"], None, ["--count", "--supervise"]),
    # a smoke run must fit in one DS batch
    (["--smoke", "5"], {"ds_batch_size": 2}, ["--smoke", "ds_batch_size"]),
    # DS writes need a token or credentials; --count (Coupa-only) is exempt
    ([], {"token": ""}, ["rossum.token", "--username"]),
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
