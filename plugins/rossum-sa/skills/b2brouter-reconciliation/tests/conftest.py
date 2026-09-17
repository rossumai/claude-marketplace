"""Puts the skill dir on sys.path so tests import discovery/match/rossum/
b2brouter/recon directly, as flat sibling modules (not a package)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402  (must follow the sys.path insert)

import credentials  # noqa: E402
import recon  # noqa: E402


@pytest.fixture(autouse=True)
def _never_read_the_operators_own_credentials_file(monkeypatch, tmp_path):
    """No test may ever pick up the real credentials file.

    `main()` falls back to `DEFAULT_CREDENTIALS_PATH` whenever that file
    exists, and a credentials file overrides the environment WHOLESALE --
    so on the machine of anyone who has actually used this skill, a test
    that only sets ROSSUM_TOKEN/B2B_API_KEY silently runs on their real
    token and host instead. That is how a unit test ends up making a live
    authenticated request with production credentials, and it fails in the
    least visible way possible: the run still returns the exit code the
    test asserted, just for the wrong reason.

    Autouse, not per-test, because the hazard is the DEFAULT -- every test
    that calls `main()` without `--credentials` has it, including ones not
    written yet. Tests that exercise the default-path pickup on purpose
    monkeypatch this same name themselves, which still wins.
    """
    absent = tmp_path / "absent" / "credentials.json"
    # Both names, not just the one read today: `recon` imports the constant
    # by value, so patching only `credentials` would miss it -- and patching
    # only `recon` would miss a future reader that imports it from
    # `credentials` instead. The guard should not depend on which.
    monkeypatch.setattr(recon, "DEFAULT_CREDENTIALS_PATH", absent)
    monkeypatch.setattr(credentials, "DEFAULT_CREDENTIALS_PATH", absent)
