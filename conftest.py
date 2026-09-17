"""Repo-wide test isolation.

The MCP server can now bootstrap its connection from the environment
(`server._autoconnect_from_env`), and several skill scripts already read
`ROSSUM_TOKEN` directly — `plugins/.../b2brouter-reconciliation/tests` has
been deleting it by hand for exactly that reason. So an SA running pytest on
the machine they also work Rossum from would otherwise have their own token
leak into the suite. Measured, with the variables exported and this file
removed: five failures in `tests/test_server_helpers.py`, plus one real network
call to the live API escaping
`test_every_auth_tool_directs_to_set_token_when_disconnected` — that test
patches the five `_http_*` helpers but not `_probe_token`, which builds its own
urllib request. One call, not one per tool: the bootstrap's own one-shot flag
latches after the first. Clearing the variables makes the runs identical on a
developer machine and in CI.

`B2B_API_KEY*` is cleared on the same argument — `recon.py` reads it straight
out of `os.environ`, and only two of its tests delete it by hand. Both those
suites live under `plugins/`, which is why this sits at the repo root rather
than in `tests/`.

Tests that exercise the bootstrap set the variables back with
`monkeypatch.setenv` and reset the one-shot `_autoconnect_attempted` flag.
"""
from __future__ import annotations

import pytest

_ROSSUM_ENV_VARS = (
    "ROSSUM_TOKEN",
    "ROSSUM_API_TOKEN",
    "ROSSUM_API_URL",
    "ROSSUM_BASE_URL",
    "ROSSUM_URL",
)


# recon.py takes every B2B_API_KEY<suffix> it can see, so the prefix is the unit.
_CREDENTIAL_ENV_PREFIXES = ("B2B_API_KEY",)


@pytest.fixture(autouse=True)
def _clear_rossum_env(monkeypatch):
    """Remove ambient credentials so no test can pick up a real one."""
    import os

    for name in _ROSSUM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in [n for n in os.environ
                 if n.startswith(_CREDENTIAL_ENV_PREFIXES)]:
        monkeypatch.delenv(name, raising=False)
