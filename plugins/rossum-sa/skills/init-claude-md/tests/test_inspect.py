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


def test_tool_detected_prd2():
    out = run_inspect(FIXTURES / "minimal")
    assert out["tool"] == "prd2"


def test_non_prd2_reports_unsupported(tmp_path):
    out = run_inspect(tmp_path)  # empty dir, no prd_config.yaml
    assert out["tool"] == "unknown"
    assert out["supported"] is False


def test_subdir_layout_is_discovered():
    out = run_inspect(FIXTURES / "prd2-subdirs")
    assert out["environments"] == ["dev-env"]
    assert out["workspace_count"] == 1
    assert out["queue_count"] == 1
    assert out["queues"][0]["name"] == "Invoices"
    assert out["queues"][0]["workspace"] == "AP Workspace"
    assert out["queues"][0]["environment"] == "dev-env"
    assert out["hook_count"] == 1
    assert {h["name"] for h in out["hooks"]} == {"Validator"}


def test_degenerate_prd_config_reports_unsupported(tmp_path):
    # Umbrella-folder style config: a null directory key with empty org_id/api_base.
    (tmp_path / "prd_config.yaml").write_text(
        "directories:\n"
        "  null:\n"
        "    org_id:\n"
        "    api_base:\n"
        "    subdirectories:\n"
        "      null:\n"
        "        regex:\n"
    )
    out = run_inspect(tmp_path)
    assert out["tool"] == "unknown"
    assert out["supported"] is False


def test_env_facts_org_id_and_api_base_are_captured():
    out = run_inspect(FIXTURES / "prd2-subdirs")
    d = out["directories"][0]
    assert d["name"] == "dev-env"
    assert d["org_id"] == "100"
    assert d["api_base"] == "https://elis.rossum.ai/api/v1"


def test_env_facts_default_to_empty_when_missing():
    out = run_inspect(FIXTURES / "minimal")
    d = out["directories"][0]
    assert d["org_id"] == ""  # minimal fixture has api_base but no org_id
    assert d["api_base"] == "https://elis.rossum.ai/api/v1"


def test_env_facts_capture_non_eu1_cluster(tmp_path):
    # api_base discriminates region/cluster — exercise a non-EU1 host (shared EU2).
    (tmp_path / "prd_config.yaml").write_text(
        "directories:\n"
        "  prod:\n"
        "    org_id: '7'\n"
        "    api_base: https://shared-eu2.rossum.app/api/v1\n"
    )
    out = run_inspect(tmp_path)
    d = out["directories"][0]
    assert d["org_id"] == "7"
    assert d["api_base"] == "https://shared-eu2.rossum.app/api/v1"


def test_multi_org_and_subdir_discovery(tmp_path):
    # org-a declares two subdirectories; org-b declares none (falls back to <org>/).
    (tmp_path / "prd_config.yaml").write_text(
        "project_name: multi\n"
        "directories:\n"
        "  org-a:\n"
        "    org_id: '1'\n"
        "    api_base: https://elis.rossum.ai/api/v1\n"
        "    subdirectories:\n"
        "      s1:\n"
        "        regex: ''\n"
        "      s2:\n"
        "        regex: ''\n"
        "  org-b:\n"
        "    org_id: '2'\n"
        "    api_base: https://elis.rossum.ai/api/v1\n"
    )

    def _queue(rel_base, ws, q):
        qdir = tmp_path / rel_base / "workspaces" / ws / "queues" / q
        qdir.mkdir(parents=True)
        (qdir / "queue.json").write_text(json.dumps({"id": 1, "name": q}))
        (qdir.parent.parent / "workspace.json").write_text(json.dumps({"id": 1, "name": ws}))

    _queue("org-a/s1", "W1", "Q1")
    _queue("org-a/s2", "W2", "Q2")
    _queue("org-b", "W3", "Q3")  # no subdirectories declared -> fallback to org-b/

    out = run_inspect(tmp_path)
    assert out["environments"] == ["org-a", "org-b"]
    assert out["queue_count"] == 3
    assert out["workspace_count"] == 3
    assert {q["environment"] for q in out["queues"]} == {"org-a", "org-b"}
