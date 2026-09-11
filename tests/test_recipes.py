"""Guard: the recipes layer stays publishable and internally consistent.

Mechanizes the tier rules in plugins/rossum-sa/recipes/README.md. The privacy guards matter most:
a recipe is public, and the corpus it is mined from is not.
"""
from __future__ import annotations

import json
import re

import repo_lib as R

RECIPES = R.ROOT / "plugins" / "rossum-sa" / "recipes"


def _recipe_files():
    return sorted(RECIPES.glob("*/recipe.json"))


def _recipes():
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in _recipe_files()]


def test_every_recipe_dir_has_both_files():
    for path in sorted(p for p in RECIPES.iterdir() if p.is_dir() and p.name != "tools"):
        assert (path / "recipe.json").is_file(), f"{path.name}: missing recipe.json"
        assert (path / "README.md").is_file(), f"{path.name}: missing README.md"


def test_recipes_parse_and_carry_required_keys():
    required = {"recipe", "status", "provenance", "shape", "phases", "intents"}
    for path, data in _recipes():
        missing = required - set(data)
        assert not missing, f"{path.parent.name}: missing keys {sorted(missing)}"


def test_status_is_known_and_supported_explains_itself():
    for path, data in _recipes():
        assert data["status"] in {"preferred", "supported"}, (
            f"{path.parent.name}: status {data['status']!r} is not preferred|supported "
            "(a candidate does not belong under recipes/)")


def test_provenance_is_a_count_not_a_list():
    """'derived from 7 implementations', never which."""
    for path, data in _recipes():
        prov = data["provenance"]
        for key in ("cluster_size", "intents_extracted_from"):
            assert isinstance(prov.get(key), int), (
                f"{path.parent.name}: provenance.{key} must be an integer count, never a list")


def test_published_shape_clears_the_deidentification_threshold():
    """n>=5 exists so a published shape is a pattern, not a recognisable customer system.

    That risk is carried by the CLUSTER — how many implementations share this shape — so the
    threshold is checked there. How many trees the intents were extracted from is a completeness
    question, handled by the disclosure guard below.
    """
    for path, data in _recipes():
        n = data["provenance"]["cluster_size"]
        assert n >= 5, (
            f"{path.parent.name}: cluster of {n} — below the n>=5 threshold, so publishing it "
            "describes specific customers rather than a pattern. Keep it as a candidate outside "
            "recipes/ until the cluster grows")


def test_partial_intent_mining_is_disclosed():
    """A recipe whose intents come from fewer than 5 trees must say so in its own file."""
    for path, data in _recipes():
        prov = data["provenance"]
        if prov["intents_extracted_from"] < 5:
            statement = prov.get("honest_statement", "")
            assert len(statement) > 80, (
                f"{path.parent.name}: intents extracted from {prov['intents_extracted_from']} "
                "implementation(s) — that limit must be stated in provenance.honest_statement so a "
                "reader does not mistake the intent set for the consensus of the whole cluster")


def test_no_worked_recipe_lands_here():
    """A worked recipe is customer data by construction."""
    for path, data in _recipes():
        blob = json.dumps(data)
        assert "resolved_decisions" not in blob, (
            f"{path.parent.name}: contains resolved_decisions — that is a worked recipe and belongs "
            "in the customer's own project")
        for binds in re.findall(r'"binds":\s*({[^{}]*})', blob):
            for value in json.loads(binds).values():
                assert isinstance(value, str) and value.startswith("<") and value.endswith(">"), (
                    f"{path.parent.name}: binds value {value!r} is not a <placeholder>")


def test_phases_declare_a_verify():
    for path, data in _recipes():
        for phase in data["phases"]:
            assert phase.get("verify") or phase.get("gap"), (
                f"{path.parent.name}/{phase['phase']}: needs a verify (or an explicit gap) — "
                "a phase without one cannot be proven done")


def test_extractor_is_stdlib_only():
    """Same contract as the MCP server: it must run anywhere, with no install step."""
    source = (RECIPES / "tools" / "shape_extract.py").read_text(encoding="utf-8")
    imports = set(re.findall(r"^\s*import\s+(\w+)", source, re.M))
    imports |= set(re.findall(r"^\s*from\s+(\w+)", source, re.M))
    allowed = {"json", "glob", "os", "re", "sys", "collections", "datetime", "argparse",
               "itertools", "pathlib", "hashlib"}
    assert imports <= allowed, f"non-stdlib imports in shape_extract.py: {sorted(imports - allowed)}"


def test_extractor_makes_no_network_calls():
    """It reads a local tree. A recipe corpus must never phone home.

    Checked on IMPORTS, not substrings: the extractor legitimately mentions urllib and requests
    inside regexes that detect outbound HTTP in the hook code it reads.
    """
    source = (RECIPES / "tools" / "shape_extract.py").read_text(encoding="utf-8")
    imports = set(re.findall(r"^\s*import\s+([\w.]+)", source, re.M))
    imports |= set(re.findall(r"^\s*from\s+([\w.]+)\s+import", source, re.M))
    networked = {i for i in imports
                 if i.split(".")[0] in {"urllib", "requests", "http", "socket", "httpx"}}
    assert not networked, f"shape_extract.py imports {sorted(networked)}; it must be read-only"


def test_recipes_are_consumed_by_a_skill():
    """A recipe layer nothing reads is documentation. Keep the consumers wired."""
    skills = R.ROOT / "plugins" / "rossum-sa" / "skills"
    for name in ("implement", "write-sow"):
        text = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        assert "recipes/" in text, (
            f"{name}/SKILL.md does not reference recipes/ — the layer has no reader")
