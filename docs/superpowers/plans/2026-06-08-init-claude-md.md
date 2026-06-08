# init-claude-md Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new user-invocable `init-claude-md` skill to the `rossum-sa` plugin that inspects a pulled prd2 project locally and writes a project-specific `CLAUDE.md` so future Claude Code sessions immediately recognize the directory as a Rossum implementation and apply the right safety rules.

**Architecture:** Two-file skill mirroring the `dead-code` pattern. A deterministic Python inspector (`inspect.py`) walks the prd2 tree, emits JSON facts about the project, and a `SKILL.md` orchestrates: it runs the inspector, fills a CLAUDE.md template with the facts, handles conflict cases (no file / hand-written file / previously-generated file), and writes the result. Inspection is local-file-only; no remote API calls, no `prd2` invocations.

**Tech Stack:** Python 3.12 stdlib (no deps — matches existing detector pattern), Markdown for `SKILL.md` and the generated `CLAUDE.md`, shell for verification.

---

## File Structure

| Path | Responsibility |
|---|---|
| `plugins/rossum-sa/skills/init-claude-md/SKILL.md` | Trigger metadata, workflow, conflict handling, template-rendering instructions, inline best-practices rationale |
| `plugins/rossum-sa/skills/init-claude-md/inspect.py` | Pure inspector: walks a prd2 tree, returns JSON with project name, environments, queue list, hook list, schema stats, detected integration target. No file generation. |
| `plugins/rossum-sa/skills/init-claude-md/template.md` | The CLAUDE.md template with `{{placeholders}}` for the inspector's facts. Sections 1–9 wrapped in `<!-- BEGIN/END rossum-sa:init-claude-md auto-generated -->` markers; section 10 outside the markers as the human-edited area. |
| `plugins/rossum-sa/skills/init-claude-md/tests/` | Test directory |
| `plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py` | Unit tests for `inspect.py` against synthetic prd2 fixtures |
| `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/` | Synthetic prd2 trees (minimal, Coupa-flavored, SAP-flavored, unknown) |
| `README.md` (root) | Append `init-claude-md` row to the skills table; bump skill count |
| `plugins/rossum-sa/skills/implement/SKILL.md` | Phase 1 — add a step pointing at the new skill |

The skill follows the `dead-code` pattern: deterministic Python inspector + Markdown skill that consumes its output. The template lives in its own file to keep `SKILL.md` readable.

---

## Task 1: Skill scaffold + trigger metadata

**Files:**
- Create: `plugins/rossum-sa/skills/init-claude-md/SKILL.md`

- [ ] **Step 1: Create the skill directory and SKILL.md with frontmatter only**

Create the file with this exact content. The body will grow in later tasks; this task only nails down the trigger and metadata so the skill becomes discoverable.

```markdown
---
name: init-claude-md
description: Generate a project-specific CLAUDE.md for a pulled Rossum prd2 project so future Claude Code sessions immediately recognize the directory as a Rossum implementation and apply the right safety rules. Inspects environments, queues, hooks, schemas, and integration targets locally — no remote API calls. Triggers on "init claude md", "set up project context for Claude", "onboard this Rossum project to Claude Code", "generate CLAUDE.md", "claude.md for this project".
argument-hint: [path-to-prd2-project]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Initialize CLAUDE.md for a Rossum prd2 Project

> Path or context: $ARGUMENTS

_Workflow filled in by later plan tasks._
```

- [ ] **Step 2: Verify the skill is recognized by Claude Code**

Run:
```bash
ls plugins/rossum-sa/skills/init-claude-md/SKILL.md
head -10 plugins/rossum-sa/skills/init-claude-md/SKILL.md
```
Expected: the file exists, frontmatter prints. (Skill list registration is dynamic — no plugin.json edit needed; `evaluate-namings` and `dead-code` are discovered the same way.)

- [ ] **Step 3: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/SKILL.md
git commit -m "feat(rossum-sa): scaffold init-claude-md skill"
```

---

## Task 2: Inspector — project root + environment discovery

**Files:**
- Create: `plugins/rossum-sa/skills/init-claude-md/inspect.py`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/prd_config.yaml`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/dev-env/.gitkeep`

- [ ] **Step 1: Write the failing test for `discover_environments`**

Create `plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py`:

```python
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
```

- [ ] **Step 2: Create the minimal fixture**

Create `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/prd_config.yaml` with this content:

```yaml
project_name: minimal
directories:
  dev-env:
    api_base: https://elis.rossum.ai/api/v1
```

Create the placeholder directory so git keeps the empty env dir:

```bash
mkdir -p plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/dev-env
touch plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/dev-env/.gitkeep
```

- [ ] **Step 3: Run the test and verify it fails**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py -v
```
Expected: FAIL — `inspect.py` does not exist or has no implementation.

- [ ] **Step 4: Write the minimal inspector**

Create `plugins/rossum-sa/skills/init-claude-md/inspect.py`:

```python
#!/usr/bin/env python3
"""Inspect a Rossum prd2 project tree and emit JSON facts.

Usage: python3 inspect.py <project_dir>

Output: a single JSON object on stdout with project facts. Stderr is reserved
for errors. No remote API calls; pure local file reading. Designed to be
called by the init-claude-md skill, which renders the facts into CLAUDE.md.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse_prd_config(project_dir: Path) -> dict:
    """Parse prd_config.yaml without a yaml dependency.

    The file is tightly structured (project_name + directories map). We only
    need project_name and the top-level keys under directories. A minimal
    line-based parser is enough and avoids adding PyYAML as a dep.
    """
    cfg_path = project_dir / "prd_config.yaml"
    if not cfg_path.is_file():
        return {"project_name": project_dir.name, "environments": []}

    project_name = project_dir.name
    environments: list[str] = []
    in_directories = False
    for raw in cfg_path.read_text().splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if re.match(r"^project_name\s*:", line):
            project_name = line.split(":", 1)[1].strip().strip('"').strip("'")
            continue
        if re.match(r"^directories\s*:\s*$", line):
            in_directories = True
            continue
        if in_directories:
            # Top-level env entry: exactly 2 spaces of indent then `name:`.
            m = re.match(r"^  ([^\s:]+)\s*:\s*$", line)
            if m:
                environments.append(m.group(1))
            elif not line.startswith(" "):
                in_directories = False

    return {"project_name": project_name, "environments": environments}


def main(project_dir_str: str) -> None:
    project_dir = Path(project_dir_str).resolve()
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(2)

    facts = parse_prd_config(project_dir)
    json.dump(facts, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: inspect.py <project_dir>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
```

- [ ] **Step 5: Run the test and verify it passes**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/fixtures/minimal/
git commit -m "feat(rossum-sa): init-claude-md inspector reads prd_config.yaml"
```

---

## Task 3: Inspector — queue and workspace discovery

**Files:**
- Modify: `plugins/rossum-sa/skills/init-claude-md/inspect.py`
- Modify: `plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/prd_config.yaml`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/dev-env/workspaces/Workspace_111/workspace.json`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/dev-env/workspaces/Workspace_111/queues/Queue_222/queue.json`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/dev-env/workspaces/Workspace_111/queues/Queue_222/schema.json`

- [ ] **Step 1: Add failing test for queue/workspace discovery**

Append to `tests/test_inspect.py`:

```python
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
```

- [ ] **Step 2: Create the with-queues fixture**

```bash
mkdir -p plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/dev-env/workspaces/Workspace_111/queues/Queue_222
```

Create `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/prd_config.yaml`:

```yaml
project_name: with-queues
directories:
  dev-env:
    api_base: https://elis.rossum.ai/api/v1
```

Create `.../with-queues/dev-env/workspaces/Workspace_111/workspace.json`:

```json
{"id": 111, "name": "Italy (DEV)"}
```

Create `.../Workspace_111/queues/Queue_222/queue.json`:

```json
{"id": 222, "name": "Invoices IT (DEV)", "workspace": "https://elis.rossum.ai/api/v1/workspaces/111"}
```

Create `.../Queue_222/schema.json`:

```json
{
  "content": [
    {"category": "section", "id": "basic_info_section", "children": [
      {"category": "datapoint", "id": "document_id", "type": "string"},
      {"category": "datapoint", "id": "vendor_name", "type": "string"},
      {"category": "datapoint", "id": "amount_total", "type": "number"}
    ]}
  ]
}
```

- [ ] **Step 3: Run the test, verify it fails**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py::test_queues_and_workspaces_are_discovered -v
```
Expected: FAIL with `KeyError: 'workspace_count'`.

- [ ] **Step 4: Extend the inspector**

Edit `plugins/rossum-sa/skills/init-claude-md/inspect.py`. Add these helpers above `main`:

```python
FIELD_CATS = {"datapoint", "multivalue", "tuple", "button"}


def count_schema_fields(schema: dict) -> int:
    """Count leaf field nodes in a schema's content tree."""
    n = 0

    def walk(node: object) -> None:
        nonlocal n
        if isinstance(node, dict):
            if node.get("category") in FIELD_CATS:
                n += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema.get("content", []))
    return n


def discover_queues(project_dir: Path, environments: list[str]) -> list[dict]:
    """Walk environments → workspaces → queues; collect facts per queue."""
    out: list[dict] = []
    for env in environments:
        env_dir = project_dir / env
        for q_json in sorted(env_dir.glob("workspaces/Workspace_*/queues/Queue_*/queue.json")):
            queue = json.loads(q_json.read_text())
            workspace_json = q_json.parent.parent.parent / "workspace.json"
            workspace_name = (
                json.loads(workspace_json.read_text()).get("name", "")
                if workspace_json.is_file() else ""
            )
            schema_path = q_json.parent / "schema.json"
            field_count = (
                count_schema_fields(json.loads(schema_path.read_text()))
                if schema_path.is_file() else 0
            )
            out.append({
                "name": queue.get("name", q_json.parent.name),
                "workspace": workspace_name,
                "environment": env,
                "schema_field_count": field_count,
            })
    return out
```

Replace the body of `main` with:

```python
def main(project_dir_str: str) -> None:
    project_dir = Path(project_dir_str).resolve()
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(2)

    facts = parse_prd_config(project_dir)
    queues = discover_queues(project_dir, facts["environments"])
    workspaces = {(q["environment"], q["workspace"]) for q in queues}
    facts["queues"] = queues
    facts["queue_count"] = len(queues)
    facts["workspace_count"] = len(workspaces)

    json.dump(facts, sys.stdout, indent=2)
    sys.stdout.write("\n")
```

- [ ] **Step 5: Run all tests, verify they pass**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py -v
```
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/fixtures/with-queues/
git commit -m "feat(rossum-sa): init-claude-md inspector walks workspaces/queues"
```

---

## Task 4: Inspector — hook discovery and integration detection

> **Note vs. spec:** The spec listed detection order as `Coupa → SAP → SFTP → Generic REST → unknown`. This plan adds **SFI** between `SAP` and `SFTP` because the spec also mentions `sfi-reference` as a conditional skill, and a project using Structured Formats Import is a distinct integration target worth surfacing. Order: `Coupa → SAP → SFI → SFTP → Generic REST → unknown`.



**Files:**
- Modify: `plugins/rossum-sa/skills/init-claude-md/inspect.py`
- Modify: `plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa/...`
- Create: `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/sap/...`

- [ ] **Step 1: Add failing tests for hook + integration detection**

Append to `tests/test_inspect.py`:

```python
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
```

- [ ] **Step 2: Create Coupa fixture**

```bash
mkdir -p plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa/dev-env/hooks/coupa_export
```

Create `tests/fixtures/coupa/prd_config.yaml`:

```yaml
project_name: coupa
directories:
  dev-env:
    api_base: https://elis.rossum.ai/api/v1
```

Create `tests/fixtures/coupa/dev-env/hooks/coupa_export.json`:

```json
{
  "id": 9001,
  "name": "coupa_export",
  "type": "function",
  "config": {"runtime": "python3.12"},
  "queues": ["https://elis.rossum.ai/api/v1/queues/222"]
}
```

Create `tests/fixtures/coupa/dev-env/hooks/coupa_export/code.py`:

```python
# Coupa Integration Baseline (CIB) export hook
def rossum_hook_request_handler(payload):
    return {"messages": [], "operations": []}
```

- [ ] **Step 3: Create SAP fixture**

```bash
mkdir -p plugins/rossum-sa/skills/init-claude-md/tests/fixtures/sap/dev-env/hooks/sap_idoc_export
```

Create `tests/fixtures/sap/prd_config.yaml`:

```yaml
project_name: sap
directories:
  dev-env:
    api_base: https://elis.rossum.ai/api/v1
```

Create `tests/fixtures/sap/dev-env/hooks/sap_idoc_export.json`:

```json
{
  "id": 9002,
  "name": "sap_idoc_export",
  "type": "function",
  "config": {"runtime": "python3.12"},
  "queues": []
}
```

Create `tests/fixtures/sap/dev-env/hooks/sap_idoc_export/code.py`:

```python
# Generates INVOIC02 IDOC for SAP middleware
def build_idoc(annotation):
    return "INVOIC02"
```

- [ ] **Step 4: Run the new tests, verify they fail**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py -v
```
Expected: the four new tests FAIL with `KeyError: 'hook_count'` or `KeyError: 'integration_target'`.

- [ ] **Step 5: Extend the inspector**

Edit `plugins/rossum-sa/skills/init-claude-md/inspect.py`. Add these helpers above `main`:

```python
COUPA_PATTERNS = (
    re.compile(r"\bCIB\b"),
    re.compile(r"\bcoupa\b", re.IGNORECASE),
    re.compile(r"Coupa Integration Baseline", re.IGNORECASE),
)
SAP_PATTERNS = (
    re.compile(r"\bINVOIC0[12]\b"),
    re.compile(r"\bIDOC\b"),
    re.compile(r"\bSAP\b"),
)
SFI_PATTERNS = (
    re.compile(r"structured.formats.import", re.IGNORECASE),
    re.compile(r"\bZUGFeRD\b", re.IGNORECASE),
    re.compile(r"\bX-Rechnung\b", re.IGNORECASE),
)
SFTP_PATTERNS = (
    re.compile(r"\bfile-storage-export\b"),
    re.compile(r"\bsftp://", re.IGNORECASE),
)


def discover_hooks(project_dir: Path, environments: list[str]) -> list[dict]:
    """Discover hooks under <env>/hooks/*.json."""
    out: list[dict] = []
    for env in environments:
        hooks_dir = project_dir / env / "hooks"
        if not hooks_dir.is_dir():
            continue
        for hook_json in sorted(hooks_dir.glob("*.json")):
            hook = json.loads(hook_json.read_text())
            runtime = ((hook.get("config") or {}).get("runtime")) or ""
            out.append({
                "name": hook.get("name", hook_json.stem),
                "type": hook.get("type", ""),
                "runtime": runtime,
                "environment": env,
                "queue_count": len(hook.get("queues") or []),
            })
    return out


def detect_integration_target(project_dir: Path, environments: list[str]) -> str:
    """First match wins: Coupa → SAP → SFI → SFTP → REST → unknown.

    Scans hook JSON, hook .py files, and any top-level export-pipeline configs.
    """
    blobs: list[str] = []
    for env in environments:
        env_dir = project_dir / env
        if not env_dir.is_dir():
            continue
        for path in env_dir.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".py"}:
                try:
                    blobs.append(path.read_text(errors="ignore"))
                except OSError:
                    continue
    haystack = "\n".join(blobs)

    if any(p.search(haystack) for p in COUPA_PATTERNS):
        return "Coupa"
    if any(p.search(haystack) for p in SAP_PATTERNS):
        return "SAP"
    if any(p.search(haystack) for p in SFI_PATTERNS):
        return "SFI"
    if any(p.search(haystack) for p in SFTP_PATTERNS):
        return "SFTP"
    # Generic REST detection: presence of a call_api block in any hook config.
    if re.search(r'"call_api"\s*:', haystack):
        return "Generic REST"
    return "unknown"
```

Extend `main` to populate the new fields. Replace the `facts.update` block with:

```python
    queues = discover_queues(project_dir, facts["environments"])
    hooks = discover_hooks(project_dir, facts["environments"])
    workspaces = {(q["environment"], q["workspace"]) for q in queues}
    facts["queues"] = queues
    facts["queue_count"] = len(queues)
    facts["workspace_count"] = len(workspaces)
    facts["hooks"] = hooks
    facts["hook_count"] = len(hooks)
    facts["integration_target"] = detect_integration_target(project_dir, facts["environments"])
```

- [ ] **Step 6: Run all tests, verify they pass**

Run:
```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py -v
```
Expected: all six tests PASS.

- [ ] **Step 7: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/test_inspect.py \
        plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa/ \
        plugins/rossum-sa/skills/init-claude-md/tests/fixtures/sap/
git commit -m "feat(rossum-sa): init-claude-md inspector detects hooks and integration target"
```

---

## Task 5: CLAUDE.md template

**Files:**
- Create: `plugins/rossum-sa/skills/init-claude-md/template.md`

- [ ] **Step 1: Create the template**

Create `plugins/rossum-sa/skills/init-claude-md/template.md` with this exact content. `{{placeholders}}` are filled by `SKILL.md` from the inspector's JSON output. The `<!-- BEGIN/END -->` markers bracket the auto-generated portion; section 10 sits outside the markers as the human-edited area.

````markdown
<!-- BEGIN rossum-sa:init-claude-md auto-generated -->
# CLAUDE.md

> This file gives Claude Code project context. It is auto-generated by `/rossum-sa:init-claude-md`. The block between the BEGIN/END markers is rewritten on re-run. Notes below the markers are preserved.

## 1. Project Overview

This is a Rossum.ai prd2 implementation project for the `{{environments_joined}}` environment(s). It contains **{{queue_count}} queues** across **{{workspace_count}} workspaces** and **{{hook_count}} hooks**. Primary integration target: **{{integration_target}}**.

Optimizes for: accurate document extraction, reliable export to the integration target, and maintainable hook code.

## 2. Tech Stack

- Rossum.ai platform (queues, schemas, hooks, annotations, MDH)
- `prd2` v2 — project deployment CLI
- Python 3.12 — function hook runtime (via TxScript API)
- MongoDB / Atlas Search — Master Data Hub and Data Storage
- JSON schema configs — queues, hooks, schemas

**Do not use:**
- `prd` v1 (deprecated; always use `prd2`)
- Editing the `code` field inside hook JSON (edit the `.py` file — `prd2 push` syncs it in)
- Editing the `formula` property in `schema.json` (edit the `formula.py` file)
- Hardcoded credentials in code

## 3. Architecture

Pulled directory layout:

```
{{tree_listing}}
```

Where new things go:
- New hooks → `<env>/hooks/<hook-name>/`
- New formulas → `<env>/workspaces/.../queues/.../formulas/<field>.py`
- Deploy files → `deploy/` or project root, named `deploy_<source>_to_<target>.yaml`

## 4. Coding Conventions

- Edit `.py` files. Never edit the `code` field in hook JSON or the `formula` property in `schema.json` — `prd2 push` syncs `.py` files into JSON automatically.
- MDH-populated fields must be `type: "enum"` with `ui_configuration.type: "data"`. A `string` field silently drops MDH values.
- Use the TxScript API (`TxScript`, field accessors, automation blockers) for function hooks; see `rossum-sa:txscript-reference`.
- Never call write APIs (`rossum_create_*`, `rossum_patch_*`, `rossum_delete_*`, `data_storage_*` writes, `prd2 push`, `prd2 deploy`) without explicit user approval.
- Keep schema field IDs stable across pushes — changing them breaks annotations.

## 5. Commands

- Pull current state: `prd2 pull`
- Push staged changes (non-interactive): `prd2 push --indexed-only -f`
- Deploy to another environment: `prd2 deploy -f <deploy-file>.yaml`
- Purge: `prd2 purge`

Push and deploy require explicit user approval — see Safety Rules below.

## 6. Safety Rules

Read-only operations are fine without confirmation: `prd2 pull`, all `rossum_list_*` and `rossum_get_*` MCP tools, `data_storage_find` and `data_storage_aggregate`, `rossum_whoami`.

All writes require explicit user "yes" before execution: this includes `prd2 push`, `prd2 deploy`, every `rossum_create_*` / `rossum_patch_*` / `rossum_delete_*` MCP tool, and every Data Storage write (`insert`, `update`, `delete`, `replace`, `bulk_write`, `drop`).

Never batch multiple write operations into one approval. Describe each one and wait for a clear yes.

## 7. File Placement Rules

- Reuse existing hooks before creating new ones.
- One formula per file under `formulas/`.
- Keep schema field IDs stable across pushes.
- Deploy files in `deploy/` or root, named `deploy_<source>_to_<target>.yaml`.

## 8. Testing & Quality

- After any change intended to be behavior-preserving: run `/rossum-sa:test-behavioral-equivalence`.
- For full audits: `/rossum-sa:analyze`.
- For pruning unused configs: `/rossum-sa:dead-code`.
- To verify naming conventions: `/rossum-sa:evaluate-namings`.

## 9. Recommended Skills

Always relevant:
- `rossum-sa:rossum-reference` — platform reference
- `rossum-sa:prd-reference` — prd2 CLI reference
- `rossum-sa:txscript-reference` — function hook API

Conditionally relevant (based on what was detected in this project):
{{conditional_skills_block}}

<!-- END rossum-sa:init-claude-md auto-generated -->

## 10. Project-Specific Notes

<!-- This section is human-edited. The auto-generator above does not modify it on re-run. Add anything unique to this implementation: gotchas, customer-specific rules, recent incidents, open questions. -->

_Add project-specific notes here._
````

- [ ] **Step 2: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/template.md
git commit -m "feat(rossum-sa): init-claude-md CLAUDE.md template"
```

---

## Task 6: SKILL.md workflow — orchestrate inspector + template

**Files:**
- Modify: `plugins/rossum-sa/skills/init-claude-md/SKILL.md`

- [ ] **Step 1: Replace the skeleton with the full workflow**

Overwrite `plugins/rossum-sa/skills/init-claude-md/SKILL.md` with this content:

````markdown
---
name: init-claude-md
description: Generate a project-specific CLAUDE.md for a pulled Rossum prd2 project so future Claude Code sessions immediately recognize the directory as a Rossum implementation and apply the right safety rules. Inspects environments, queues, hooks, schemas, and integration targets locally — no remote API calls. Triggers on "init claude md", "set up project context for Claude", "onboard this Rossum project to Claude Code", "generate CLAUDE.md", "claude.md for this project".
argument-hint: [path-to-prd2-project]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Initialize CLAUDE.md for a Rossum prd2 Project

> Path or context: $ARGUMENTS

## What this skill does

Generates a `CLAUDE.md` at the root of a pulled prd2 project so future Claude Code sessions know it's a Rossum implementation and apply the right safety rules (edit `.py` not JSON, never push/deploy without approval, MDH targets are enums).

The auto-generated portion is wrapped in `<!-- BEGIN/END rossum-sa:init-claude-md auto-generated -->` markers. Re-running this skill rewrites only the marked block; the "Project-Specific Notes" section below the markers is preserved.

## Why a project-specific CLAUDE.md

Anthropic recommends `CLAUDE.md` for project memory — it is auto-loaded at the start of each Claude Code session. Without one, every session has to rediscover Rossum conventions (edit `.py` not JSON, prd2 commands, safety rules, where queues/hooks/formulas live). A pre-filled file removes that friction.

Best-practices sections (from the source article that motivated this skill):
1. Project Overview — what the implementation is, what it optimizes for
2. Tech Stack — explicit list of what's in use and what to avoid
3. Architecture — real directory layout, where new things go
4. Coding Conventions — hard rules (not vague "write clean code")
5. Commands — exact commands, not paraphrases
6. Safety Rules — confirmation gates for destructive ops
7. File Placement Rules — when to create new files vs. edit existing
8. Testing & Quality — how "done" is validated
9. Recommended Skills — pointers to relevant `rossum-sa:*` skills
10. Project-Specific Notes — human-curated area

## Workflow

### Step 1: Resolve the project directory

If `$ARGUMENTS` is a path, use it. Otherwise use the current working directory. Confirm it looks like a prd2 project:

- Required: `prd_config.yaml` at the root.
- Strongly suggested: at least one environment subdirectory listed in that file with `workspaces/` underneath.

If `prd_config.yaml` is missing, stop and tell the user: "This does not look like a prd2 project (no `prd_config.yaml` found). Run `prd2 pull` first or pass the project path explicitly."

### Step 2: Run the inspector

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/inspect.py <project_dir>
```

Capture stdout as JSON. The inspector returns:

```
{
  "project_name": "...",
  "environments": ["dev-env", "prod-env"],
  "workspace_count": N,
  "queue_count": M,
  "queues": [{"name": "...", "workspace": "...", "environment": "...", "schema_field_count": K}, ...],
  "hook_count": H,
  "hooks": [{"name": "...", "type": "...", "runtime": "...", "environment": "...", "queue_count": Q}, ...],
  "integration_target": "Coupa" | "SAP" | "SFI" | "SFTP" | "Generic REST" | "unknown"
}
```

### Step 3: Render the template

Read `${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/template.md`. Replace placeholders:

| Placeholder | Value source |
|---|---|
| `{{environments_joined}}` | `", ".join(facts["environments"])` |
| `{{queue_count}}` | `facts["queue_count"]` |
| `{{workspace_count}}` | `facts["workspace_count"]` |
| `{{hook_count}}` | `facts["hook_count"]` |
| `{{integration_target}}` | `facts["integration_target"]` (if `"unknown"`, append a "fill in — integration target not auto-detected" note) |
| `{{tree_listing}}` | Output of `tree -L 4 -I '__pycache__|node_modules' <project_dir>` truncated to ~40 lines. If `tree` is not installed, fall back to `find <project_dir> -maxdepth 4 -type d \| sort \| head -40`. |
| `{{conditional_skills_block}}` | See Step 4 |

### Step 4: Build the conditional skills block

Always include the three baseline references already mentioned in section 9 above the placeholder. Then, based on the inspector's signals, append:

- If `integration_target == "Coupa"` → `- \`rossum-sa:coupa-baseline-reference\` — Coupa Integration Baseline (CIB)`
- If `integration_target == "SAP"` → `- \`rossum-sa:sap-reference\` — SAP integration patterns`
- If `integration_target == "SFI"` → `- \`rossum-sa:sfi-reference\` — Structured Formats Import`
- If any hook name matches `/mdh|master.data|matching/i` OR any hook `.py` file contains `MatchConfig` → `- \`rossum-sa:mdh-reference\` — Master Data Hub matching`
- If any hook `.py` or JSON contains `"call_api"` → `- \`rossum-sa:export-pipeline-reference\` — Request Processor`

If no conditional skills apply, write a single line: `_No conditional references — none of Coupa, SAP, SFI, MDH, or Request Processor signals were detected._`

### Step 5: Write or merge the file

Check whether `<project_dir>/CLAUDE.md` exists:

| Case | Action |
|---|---|
| File does not exist | Write the rendered template. Confirm with the user the path of the written file. |
| File exists and contains both `<!-- BEGIN rossum-sa:init-claude-md auto-generated -->` and the matching END marker | Replace only the content between the markers. Preserve everything else verbatim. |
| File exists, no markers | Show the user a diff of the proposed additions (the full template). Ask: overwrite / append the auto-generated block to the end / abort. Default suggestion: **append**. Wait for explicit choice. |

Never silently overwrite hand-written content.

### Step 6: Show the result and recommend next steps

After writing, print:

- Path written
- One-paragraph summary of what landed in CLAUDE.md (env names, queue count, hook count, detected integration target)
- Suggested follow-up: "Open `CLAUDE.md` and fill in section 10 (Project-Specific Notes) with anything unique to this implementation. Then commit the file."

Do **not** auto-commit. The user decides when to commit a memory file.

## Notes

- The inspector is local-only — no Rossum API calls, no `prd2` invocations.
- The skill itself only reads/writes local files. No network.
- Re-running on a project that already has a generated CLAUDE.md only refreshes the marked section; user notes below stay intact.
- If the integration target comes back `unknown`, the generated file says so explicitly — review and fill in if you know the answer.
````

- [ ] **Step 2: Verify the file renders sensibly**

Run:
```bash
head -20 plugins/rossum-sa/skills/init-claude-md/SKILL.md
wc -l plugins/rossum-sa/skills/init-claude-md/SKILL.md
```
Expected: frontmatter intact at the top, file under ~200 lines.

- [ ] **Step 3: Commit**

```bash
git add plugins/rossum-sa/skills/init-claude-md/SKILL.md
git commit -m "feat(rossum-sa): init-claude-md workflow that renders CLAUDE.md from inspector facts"
```

---

## Task 7: End-to-end smoke test on a fixture

**Files:**
- (No code changes — manual verification step)

- [ ] **Step 1: Generate a CLAUDE.md against the Coupa fixture**

Manually drive the skill's workflow against the test fixture to confirm the end-to-end flow works. Run the inspector and capture its output:

```bash
python3 plugins/rossum-sa/skills/init-claude-md/inspect.py \
  plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa
```

Expected: JSON with `"project_name": "coupa"`, `"integration_target": "Coupa"`, `"hook_count": 1`.

- [ ] **Step 2: Verify the template renders**

Confirm the template file is well-formed Markdown and contains all placeholders documented in `SKILL.md` Step 3:

```bash
grep -o '{{[a-z_]*}}' plugins/rossum-sa/skills/init-claude-md/template.md | sort -u
```

Expected (exactly these, no extras):
```
{{conditional_skills_block}}
{{environments_joined}}
{{hook_count}}
{{integration_target}}
{{queue_count}}
{{tree_listing}}
{{workspace_count}}
```

If any unexpected placeholder appears, fix the template before continuing — they will surface in the user's CLAUDE.md as literal text.

- [ ] **Step 3: Invoke the skill in a fresh Claude Code session**

In a separate Claude Code session, run:
```
/rossum-sa:init-claude-md plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa
```

Verify:
- The skill creates `plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa/CLAUDE.md` (or asks before overwriting if one already exists).
- The generated file mentions "Coupa" as integration target, "1 queues" (or "0 queues" if the Coupa fixture has none — that's fine), and "1 hooks".
- The BEGIN/END markers are present.
- Section 10 ("Project-Specific Notes") is below the END marker.

- [ ] **Step 4: Delete the generated file and commit if anything changed**

```bash
rm -f plugins/rossum-sa/skills/init-claude-md/tests/fixtures/coupa/CLAUDE.md
git status
```

If the fixture is unchanged (expected), there's nothing to commit. If any skill/template/inspector file changed during the manual test, commit those fixes:

```bash
git add plugins/rossum-sa/skills/init-claude-md/
git commit -m "fix(rossum-sa): init-claude-md smoke-test corrections"
```

---

## Task 8: Cross-references — update implement skill and README files

**Files:**
- Modify: `plugins/rossum-sa/skills/implement/SKILL.md`
- Modify: `README.md`
- Modify: `README-internal.md`

- [ ] **Step 1: Add an init-claude-md step to the implement skill's Phase 1**

Open `plugins/rossum-sa/skills/implement/SKILL.md` and find the line:

```
3. **`prd2 pull`** to get the current state of the environment. This is a read-only operation.
```

Replace it with:

```
3. **`prd2 pull`** to get the current state of the environment. This is a read-only operation.

4. **Generate `CLAUDE.md` for the project** — run `/rossum-sa:init-claude-md` so future sessions in this directory recognize it as a Rossum implementation and apply the right safety rules. This is optional but recommended for any project that will be touched by Claude Code multiple times.
```

Then renumber the line that previously said `4. **Review what exists.** ...` to `5. **Review what exists.** ...`.

- [ ] **Step 2: Add the skill to README.md**

Open `README.md`. Find the line near the top that says `N skills · M reference packs · K MCP tools` (currently `8 skills · 9 reference packs · 64 MCP tools` on line 5 — but the displayed count is stale relative to the actual table, so don't trust the hard-coded "8"). Bump the skill count by exactly one (e.g. `8` → `9`, or whatever the current value is → that value + 1). Do not retroactively fix the existing skew — that's out of scope.

Then in the skills table for `rossum-sa` (the table starting around line 56), add this row immediately before the `coupa-bulk-replication` row at the bottom:

```
| `/rossum-sa:init-claude-md [path]` | Generate a project-specific `CLAUDE.md` for a pulled prd2 project so future Claude Code sessions recognize it as a Rossum implementation |
```

- [ ] **Step 3: Add a note to README-internal.md**

Open `README-internal.md`. Append a new section at the bottom:

```markdown
## init-claude-md

`/rossum-sa:init-claude-md` writes a project-specific `CLAUDE.md` from a local inspection of a pulled prd2 project. The auto-generated portion is bracketed by `<!-- BEGIN/END rossum-sa:init-claude-md auto-generated -->` markers — re-running the skill refreshes only that block.

If you add fields to `inspect.py`, also update the placeholder list and the rendering step in `SKILL.md` and the `template.md` file. Inspector schema must stay in sync with what the skill expects.
```

- [ ] **Step 4: Verify the count was bumped by exactly one**

Run:
```bash
grep -c "^| \`/rossum-sa:" README.md
grep "skills ·" README.md
```

Expected: the table row count is one more than the previous baseline. (The headline number in README.md is known to be stale vs. the actual table; just confirm you bumped by one — not that the two numbers agree.)

- [ ] **Step 5: Commit**

```bash
git add plugins/rossum-sa/skills/implement/SKILL.md README.md README-internal.md
git commit -m "docs(rossum-sa): cross-reference init-claude-md in implement skill and READMEs"
```

---

## Task 9: Plugin version bump

**Files:**
- Modify: `plugins/rossum-sa/.claude-plugin/plugin.json`
- Modify: `plugins/rossum-sa/mcp-servers/rossum-api/server.py` (only the `serverInfo` version string)

The CLAUDE.md rule ("Version strings must stay in sync") applies any time the plugin gains a feature. Bump from `0.16.0` to `0.17.0` (minor — new skill is a feature, no breaking change).

- [ ] **Step 1: Bump plugin.json**

Edit `plugins/rossum-sa/.claude-plugin/plugin.json`. Change:

```json
  "version": "0.16.0",
```

to:

```json
  "version": "0.17.0",
```

- [ ] **Step 2: Find the matching version in server.py**

Run:
```bash
grep -n 'serverInfo\|"version"' plugins/rossum-sa/mcp-servers/rossum-api/server.py | head
```

Expected: finds the `serverInfo` dict with a `"version": "0.16.0"` line.

- [ ] **Step 3: Bump server.py version**

Use Edit to change the `"version": "0.16.0"` line inside the `serverInfo` dict to `"version": "0.17.0"`. (Use a sufficiently unique `old_string` — include surrounding `serverInfo` context if needed to disambiguate from other version strings.)

- [ ] **Step 4: Verify both files match**

Run:
```bash
grep '"0.17.0"' plugins/rossum-sa/.claude-plugin/plugin.json
grep '"0.17.0"' plugins/rossum-sa/mcp-servers/rossum-api/server.py
```

Expected: each command prints exactly one matching line.

- [ ] **Step 5: Commit**

```bash
git add plugins/rossum-sa/.claude-plugin/plugin.json plugins/rossum-sa/mcp-servers/rossum-api/server.py
git commit -m "chore(rossum-sa): bump version to 0.17.0 for init-claude-md skill"
```

---

## Final verification

- [ ] **Step 1: All tests pass**

```bash
python3 -m pytest plugins/rossum-sa/skills/init-claude-md/tests/ -v
```

Expected: 6 tests PASS, 0 failures.

- [ ] **Step 2: Skill discoverable**

Confirm the skill listing includes `init-claude-md` (in a fresh Claude Code session or via the plugin manifest path):

```bash
ls plugins/rossum-sa/skills/init-claude-md/
```

Expected:
```
SKILL.md
inspect.py
template.md
tests/
```

- [ ] **Step 3: Smoke test against the real project tree**

If a real prd2 project is available locally, run:

```bash
python3 plugins/rossum-sa/skills/init-claude-md/inspect.py <real_project_dir>
```

Confirm the JSON output looks sensible (correct env names, plausible queue/hook counts, integration_target either correctly detected or `"unknown"`). This is exploratory — no assertions, just sanity-check.

- [ ] **Step 4: Final commit if anything moved during verification**

```bash
git status
```

If clean, the plan is fully implemented. If anything changed, commit it under a descriptive message.

---

## Notes for the implementer

- **Follow the dead-code pattern.** That skill is the closest analog: deterministic Python inspector + Markdown skill that consumes its output. Don't invent new conventions.
- **No remote API calls.** This skill is local-only. If you find yourself reaching for `rossum_*` MCP tools, you're off the path.
- **Idempotency is enforced by markers.** The `<!-- BEGIN/END -->` comments are the source of truth for what's auto-generated. Anything outside them is the user's.
- **No PyYAML dependency.** The mini-parser in `inspect.py` is intentional — keeps the plugin zero-dep, matching the MCP server's posture.
- **Tree command fallback.** Some users won't have `tree` installed (especially on minimal CI containers). The `find`-based fallback in Step 3 of Task 6 is required.
