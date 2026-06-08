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


def test_queues_and_workspaces_are_discovered():
    out = run_inspect(FIXTURES / "with-queues")
    assert out["workspace_count"] == 1
    assert out["queue_count"] == 1
    assert out["queues"] == [
        {
            "name": "Invoices IT (DEV)",
            "workspace": "Italy (DEV)",
            "environment": "dev-env",
            "schema_field_count": 3,
        }
    ]


def test_hooks_are_discovered_with_runtime_and_type():
    out = run_inspect(FIXTURES / "coupa")
    assert out["hook_count"] >= 1
    names = {h["name"] for h in out["hooks"]}
    assert "coupa_export" in names
    coupa_hook = next(h for h in out["hooks"] if h["name"] == "coupa_export")
    assert coupa_hook["type"] == "function"
    assert coupa_hook["runtime"] == "python3.12"


def test_integration_target_detects_coupa():
    out = run_inspect(FIXTURES / "coupa")
    assert out["integration_target"] == "Coupa"


def test_integration_target_detects_sap():
    out = run_inspect(FIXTURES / "sap")
    assert out["integration_target"] == "SAP"


def test_integration_target_unknown_when_no_signal():
    out = run_inspect(FIXTURES / "minimal")
    assert out["integration_target"] == "unknown"
