"""Unit tests for inspect.py — synthetic prd2 trees under tests/fixtures/."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "inspect.py"
FIXTURES = HERE / "fixtures"


def run_inspect(project_dir: Path) -> dict:
    """Run inspect.py against a fixture and return parsed JSON."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(project_dir)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def test_minimal_project_returns_project_name_and_environments():
    out = run_inspect(FIXTURES / "minimal")
    assert out["project_name"] == "minimal"
    assert out["environments"] == ["dev-env"]
