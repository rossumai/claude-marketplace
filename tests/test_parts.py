"""Guard: every part under plugins/rossum-sa/parts/ is well-formed.

Stdlib-only (json + pathlib + re), consistent with the other repo guards.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PARTS = ROOT / "plugins" / "rossum-sa" / "parts"
SKILLS = ROOT / "plugins" / "rossum-sa" / "skills"
AXES = {"capture", "matching", "validation", "export", "formula"}
MATURITIES = {"candidate", "reviewed", "standard"}
REQUIRED = {"name", "axis", "summary", "maturity", "params",
            "produces", "consumes", "provenance", "reference"}
OPTIONAL = {"notes"}
PARAM_TYPES = {"string", "number"}
_PLACEHOLDER = re.compile(r"«([^»]+)»")


def _fragment_parse_errors(name: str, f: Path, params: dict) -> list[str]:
    """A fragment must parse once its «param» seams are filled. The seams are
    deliberately invalid source (which is why parts/ is excluded from ruff),
    so this is where a fragment's actual syntax gets checked — not the linter.

    Fill is type-aware for JSON fragments, keyed off each param's declared
    `type` (see PARAM_TYPES): a `number` param must appear as a BARE seam
    (e.g. `"$gte": «threshold»`) and gets filled with the literal 0; a `string`
    param must appear QUOTED — either as the whole JSON string (`"«x»"`) or
    embedded inside a larger templated string (`"{payload.secrets.«x»}"`) — and
    gets filled with the literal text `x`. "Quoted" is determined by counting
    unescaped double quotes before the seam: an odd count means we are inside a
    JSON string at that point.

    Filling a `number` param that the fragment quotes silently produces a
    string compared against a numeric value (matches nothing, no error) — the
    exact defect this guard exists to catch — so that combination, and its
    mirror image (a `string` param used bare), are hard errors instead of
    being smoothed over into a parseable-but-wrong fill.
    """
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
        errors: list[str] = []

        def _fill(m: re.Match) -> str:
            pname = m.group(1)
            pmeta = params.get(pname)
            ptype = pmeta.get("type") if isinstance(pmeta, dict) else None
            quoted = raw.count('"', 0, m.start()) % 2 == 1
            if ptype == "number":
                if quoted:
                    errors.append(
                        f"{name}: {f.name} param «{pname}» is declared number but "
                        "the fragment quotes the seam — filling it yields a "
                        "string, which silently matches nothing"
                    )
                    return m.group(0)
                return "0"
            if ptype == "string":
                if not quoted:
                    errors.append(
                        f"{name}: {f.name} param «{pname}» is declared string but "
                        "the fragment uses a bare (unquoted) seam — filling it "
                        "yields invalid JSON / a bare identifier"
                    )
                    return m.group(0)
                return "x"
            # Not a declared param, or missing/bogus type — reported separately
            # by validate_part's own checks. Best-effort fill so this
            # function never crashes on an already-broken contract.
            return "x" if quoted else "null"

        filled = _PLACEHOLDER.sub(_fill, raw)
        if errors:
            return errors
        try:
            json.loads(filled)
        except json.JSONDecodeError as e:
            return [f"{name}: {f.name} is not valid JSON once seams are filled ({e})"]
    return []


def _io_grounding_errors(
    name: str, meta: dict, fragment_text: str, declared_params: set[str]
) -> list[str]:
    """`produces`/`consumes` must be post-fill schema field ids, grounded in the
    fragment — the contract stated in parts/README.md.

    Without this the field is decoration: it drifts from the config it claims to
    describe, and nothing can compute "A feeds B" because one part's
    `produces` never string-matches another's `consumes`. Each entry is either a
    literal id the fragment actually mentions, or a `«seam»` that is a declared
    param (so filling resolves it to a real id). Duplicates and blanks are bugs.
    """
    errs: list[str] = []
    for fld in ("produces", "consumes"):
        entries = meta.get(fld)
        if not isinstance(entries, list):
            continue  # shape already reported by the caller
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                errs.append(f"{name}: {fld} entry {entry!r} is not a non-empty string")
                continue
            if entry in seen:
                errs.append(f"{name}: {fld} lists {entry!r} twice")
            seen.add(entry)
            seams = _PLACEHOLDER.findall(entry)
            if seams:
                for seam in seams:
                    if seam not in declared_params:
                        errs.append(
                            f"{name}: {fld} entry «{seam}» is not a declared param, "
                            f"so filling the part never resolves it to a real id"
                        )
            elif entry not in fragment_text:
                errs.append(
                    f"{name}: {fld} claims {entry!r}, which the fragment never "
                    f"mentions — declare the id the fragment actually uses, or a "
                    f"«param» seam that resolves to it"
                )
    return errs


def part_dirs() -> list[Path]:
    return sorted(p.parent for p in PARTS.glob("*/*/part.json"))


def validate_part(d: Path) -> list[str]:
    errs: list[str] = []
    meta_path = d / "part.json"
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
    else:
        for pname, pmeta in meta["params"].items():
            ptype = pmeta.get("type") if isinstance(pmeta, dict) else None
            if ptype is None:
                errs.append(f"{d.name}: param «{pname}» missing required key 'type'")
            elif ptype not in PARAM_TYPES:
                errs.append(f"{d.name}: param «{pname}» has invalid type {ptype!r} "
                            f"(must be one of {sorted(PARAM_TYPES)})")
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
        params = meta.get("params", {})
        if not isinstance(params, dict):
            params = {}
        for f in fragments:
            errs += _fragment_parse_errors(d.name, f, params)
        errs += _io_grounding_errors(d.name, meta, text, declared)
    return errs


@pytest.mark.parametrize("d", part_dirs(), ids=lambda p: p.name)
def test_part_is_wellformed(d):
    errs = validate_part(d)
    assert not errs, "\n".join(errs)


def test_part_names_unique():
    names = [p.name for p in part_dirs()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate part names: {dupes}"


def test_category_qualified_reference_resolves_and_is_unique():
    """The recipe layer will reference a part category-qualified — `<axis>/<name>`
    — never by bare name, so resolving a reference is a direct path join with no
    glob and no ambiguity. This guard proves that form actually holds: every
    part's `f"{axis}/{name}"` must resolve straight to `parts/<axis>/<name>/part.json`,
    and no two parts may collide on the same qualified reference."""
    errs: list[str] = []
    seen: dict[str, Path] = {}
    for d in part_dirs():
        axis, name = d.parent.name, d.name
        ref = f"{axis}/{name}"
        if not (PARTS / axis / name / "part.json").is_file():
            errs.append(f"{ref}: does not resolve to {PARTS / axis / name / 'part.json'}")
        if ref in seen:
            errs.append(f"{ref}: category-qualified reference collides with {seen[ref]}")
        else:
            seen[ref] = d
    assert not errs, "\n".join(errs)


def test_fragment_parse_catches_broken_python(tmp_path):
    """Regression: the fill-then-parse check must reject a syntactically broken
    .py fragment (the safety ruff used to give us before parts/ was excluded)."""
    d = tmp_path / "matching" / "broken-bp"
    d.mkdir(parents=True)
    (d / "part.json").write_text(json.dumps({
        "name": "broken-bp", "axis": "matching", "summary": "x",
        "maturity": "candidate", "params": {}, "produces": [], "consumes": [],
        "provenance": "x", "reference": "mdh-reference",
    }), "utf-8")
    (d / "README.md").write_text("x", "utf-8")
    (d / "fragment.py").write_text("def f(:\n    pass\n", "utf-8")  # deliberate syntax error
    errs = validate_part(d)
    assert any("not valid Python" in e for e in errs), errs


def _write_bp(d: Path, params: dict, fragment_json: str) -> None:
    d.mkdir(parents=True)
    (d / "part.json").write_text(json.dumps({
        "name": d.name, "axis": "matching", "summary": "x",
        "maturity": "candidate", "params": params, "produces": [], "consumes": [],
        "provenance": "x", "reference": "mdh-reference",
    }), "utf-8")
    (d / "README.md").write_text("x", "utf-8")
    (d / "fragment.json").write_text(fragment_json, "utf-8")


def test_fragment_parse_catches_quoted_numeric_seam(tmp_path):
    """Regression for review finding 1: a numeric param whose seam is quoted
    fills to a STRING compared against a numeric value (e.g. Mongo's $gte) —
    silently matches nothing, no error. This must be a hard CI failure."""
    d = tmp_path / "matching" / "quoted-numeric-bp"
    _write_bp(
        d,
        params={"threshold": {"default": 0.8, "type": "number", "description": "x"}},
        fragment_json='{"$match": {"__score": {"$gte": "«threshold»"}}}',
    )
    errs = validate_part(d)
    assert any("declared number" in e and "quotes the seam" in e for e in errs), errs


def test_fragment_parse_catches_missing_type(tmp_path):
    """Every param must declare a type — the guard can't fill-by-type at all
    if the contract is silent on what a seam's value should be."""
    d = tmp_path / "matching" / "missing-type-bp"
    _write_bp(
        d,
        params={"dataset": {"required": True, "description": "x"}},
        fragment_json='{"source": {"dataset": "«dataset»"}}',
    )
    errs = validate_part(d)
    assert any("missing required key 'type'" in e for e in errs), errs


def test_fragment_parse_catches_bogus_type(tmp_path):
    """A param type outside PARAM_TYPES is rejected, not silently accepted."""
    d = tmp_path / "matching" / "bogus-type-bp"
    _write_bp(
        d,
        params={"dataset": {"required": True, "type": "collection", "description": "x"}},
        fragment_json='{"source": {"dataset": "«dataset»"}}',
    )
    errs = validate_part(d)
    assert any("invalid type" in e for e in errs), errs


def test_fragment_parse_accepts_correctly_typed_seams(tmp_path):
    """A bare numeric seam plus fully-typed params is the correct pattern and
    must produce no errors at all."""
    d = tmp_path / "matching" / "typed-ok-bp"
    _write_bp(
        d,
        params={
            "dataset": {"required": True, "type": "string", "description": "x"},
            "threshold": {"default": 0.8, "type": "number", "description": "x"},
        },
        fragment_json='{"source": {"dataset": "«dataset»"}, '
                      '"filter": {"$gte": «threshold»}}',
    )
    errs = validate_part(d)
    assert not errs, errs


def _index_rows(text: str) -> set[str]:
    """Part names in the FIRST column of the index's table rows.

    First column only: candidate descriptions cross-reference other parts by
    name ("Composes with `export-iterate-line-items`"), and a plain substring
    search would read those mentions as rows.
    """
    return set(re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|", text, re.MULTILINE))


def _index_text() -> str:
    """The autoloaded index — the only part artifact Claude reads without
    being asked, so drift here is drift in everything Claude believes exists."""
    return (SKILLS / "parts-index" / "SKILL.md").read_text("utf-8")


def test_index_lists_every_part_and_nothing_else():
    on_disk = {d.name for d in part_dirs()}
    listed = _index_rows(_index_text())
    missing = sorted(on_disk - listed)
    stale = sorted(listed - on_disk)
    assert not missing and not stale, (
        f"parts-index/SKILL.md is out of sync with the filesystem — "
        f"missing from the index: {missing}; listed but absent on disk: {stale}"
    )


def test_index_candidate_section_matches_maturity():
    """A `candidate` must be under the candidates heading (never auto-composed),
    and a `standard` must not be — the index's section IS the safety signal."""
    _, _, candidates = _index_text().partition("## Candidates")
    assert candidates, "parts-index/SKILL.md has no '## Candidates' section"
    candidate_rows = _index_rows(candidates)
    errs = []
    for d in part_dirs():
        meta = json.loads((d / "part.json").read_text("utf-8"))
        in_candidates = d.name in candidate_rows
        if meta.get("maturity") == "candidate" and not in_candidates:
            errs.append(f"{d.name}: maturity candidate but not under '## Candidates'")
        if meta.get("maturity") != "candidate" and in_candidates:
            errs.append(f"{d.name}: maturity {meta.get('maturity')!r} but listed "
                        f"under '## Candidates'")
    assert not errs, "\n".join(errs)
