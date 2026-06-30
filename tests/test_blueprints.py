"""Guard: every blueprint under plugins/rossum-sa/blueprints/ is well-formed.

Stdlib-only (json + pathlib + re), consistent with the other repo guards.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINTS = ROOT / "plugins" / "rossum-sa" / "blueprints"
SKILLS = ROOT / "plugins" / "rossum-sa" / "skills"
AXES = {"capture", "matching", "validation", "export", "formula"}
MATURITIES = {"candidate", "reviewed", "standard"}
REQUIRED = {"name", "axis", "summary", "maturity", "params",
            "produces", "consumes", "provenance", "reference"}
OPTIONAL = {"notes"}
_PLACEHOLDER = re.compile(r"«([^»]+)»")


def _fragment_parse_errors(name: str, f: Path) -> list[str]:
    """A fragment must parse once its «param» seams are filled. The seams are
    deliberately invalid source (which is why blueprints/ is excluded from ruff),
    so this is where a fragment's actual syntax gets checked — not the linter."""
    raw = f.read_text("utf-8")
    if f.suffix == ".py":
        # seams stand in for identifiers/string-keys → fill with the bare name
        filled = _PLACEHOLDER.sub(lambda m: m.group(1), raw)
        try:
            compile(filled, str(f), "exec")
        except SyntaxError as e:
            return [f"{name}: {f.name} is not valid Python once seams are filled "
                    f"(line {e.lineno}: {e.msg})"]
    elif f.suffix == ".json":
        # quoted seams → a string; any bare (e.g. numeric-position) seam → null
        filled = _PLACEHOLDER.sub("null", re.sub(r'"«[^»]+»"', '"x"', raw))
        try:
            json.loads(filled)
        except json.JSONDecodeError as e:
            return [f"{name}: {f.name} is not valid JSON once seams are filled ({e})"]
    return []


def blueprint_dirs() -> list[Path]:
    return sorted(p.parent for p in BLUEPRINTS.glob("*/*/blueprint.json"))


def validate_blueprint(d: Path) -> list[str]:
    errs: list[str] = []
    meta_path = d / "blueprint.json"
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        return [f"{meta_path}: invalid JSON ({e})"]

    keys = set(meta)
    for k in REQUIRED - keys:
        errs.append(f"{d.name}: missing required key {k!r}")
    for k in keys - REQUIRED - OPTIONAL:
        errs.append(f"{d.name}: unknown key {k!r}")
    if meta.get("axis") not in AXES:
        errs.append(f"{d.name}: bad axis {meta.get('axis')!r}")
    if meta.get("maturity") not in MATURITIES:
        errs.append(f"{d.name}: bad maturity {meta.get('maturity')!r}")
    if meta.get("name") != d.name:
        errs.append(f"{d.name}: name {meta.get('name')!r} != folder name")
    if d.parent.name != meta.get("axis"):
        errs.append(f"{d.name}: lives in {d.parent.name}/ but axis is {meta.get('axis')!r}")
    if not isinstance(meta.get("params"), dict):
        errs.append(f"{d.name}: params must be an object")
    for fld in ("produces", "consumes"):
        if not isinstance(meta.get(fld), list):
            errs.append(f"{d.name}: {fld} must be a list")

    if not (d / "README.md").exists():
        errs.append(f"{d.name}: missing README.md")

    ref = meta.get("reference", "")
    pack = ref.split("#", 1)[0]
    if not pack or not (SKILLS / pack).is_dir():
        errs.append(f"{d.name}: reference {ref!r} does not resolve to a skills/ pack")

    fragments = [p for p in d.iterdir() if p.name.startswith("fragment.")]
    if not fragments:
        errs.append(f"{d.name}: no fragment.* file")
    else:
        text = "".join(f.read_text("utf-8") for f in fragments)
        used = set(_PLACEHOLDER.findall(text))
        declared = set(meta.get("params", {}))
        for missing in declared - used:
            errs.append(f"{d.name}: param «{missing}» never used in fragment")
        for orphan in used - declared:
            errs.append(f"{d.name}: placeholder «{orphan}» not a declared param")
        for f in fragments:
            errs += _fragment_parse_errors(d.name, f)
    return errs


@pytest.mark.parametrize("d", blueprint_dirs(), ids=lambda p: p.name)
def test_blueprint_is_wellformed(d):
    errs = validate_blueprint(d)
    assert not errs, "\n".join(errs)


def test_blueprint_names_unique():
    names = [p.name for p in blueprint_dirs()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate blueprint names: {dupes}"


def test_fragment_parse_catches_broken_python(tmp_path):
    """Regression: the fill-then-parse check must reject a syntactically broken
    .py fragment (the safety ruff used to give us before blueprints/ was excluded)."""
    d = tmp_path / "matching" / "broken-bp"
    d.mkdir(parents=True)
    (d / "blueprint.json").write_text(json.dumps({
        "name": "broken-bp", "axis": "matching", "summary": "x",
        "maturity": "candidate", "params": {}, "produces": [], "consumes": [],
        "provenance": "x", "reference": "mdh-reference",
    }), "utf-8")
    (d / "README.md").write_text("x", "utf-8")
    (d / "fragment.py").write_text("def f(:\n    pass\n", "utf-8")  # deliberate syntax error
    errs = validate_blueprint(d)
    assert any("not valid Python" in e for e in errs), errs
