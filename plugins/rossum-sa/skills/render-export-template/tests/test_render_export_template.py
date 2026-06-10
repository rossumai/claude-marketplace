"""Tests for the render-export-template skill.

Two layers, mirroring the regression-prone parts the reviewer flagged:

* The bundled ``render_export_template.py`` is run as a subprocess against a
  synthetic payload fixture (no secrets), asserting the rendered output is valid
  and that the fragile bits behave — ``{% for %}`` / ``loop.last`` comma control,
  ``| tojson`` escaping, and ``| default`` for empty values.
* The MCP server's ``_template_text_to_multiline`` helper (the inverse of
  extract's ``"\n".join``) is imported directly and checked for LF/CRLF/trailing
  newline handling, so a round-trip extract -> edit -> generate stays line-stable.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RENDER = HERE.parent / "scripts" / "render_export_template.py"
FIXTURES = HERE / "fixtures"
SERVER = HERE.parent.parent.parent / "mcp-servers" / "rossum-api" / "server.py"


# --- render script (subprocess) -------------------------------------------------

def _render(template: Path, payload: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(RENDER), "--template", str(template), "--payload", str(payload)],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


@pytest.fixture(scope="module")
def rendered():
    pytest.importorskip("jinja2")  # render needs Jinja2; skip cleanly if absent
    return _render(FIXTURES / "sample_template.j2", FIXTURES / "sample_payload.json")


def test_render_output_is_valid_json(rendered):
    json.loads(rendered)  # raises if a stray comma / bad escape crept in


def test_loop_emits_each_line_item_without_trailing_comma(rendered):
    data = json.loads(rendered)
    assert len(data["lines"]) == 2  # both items, no empty trailing element


def test_tojson_escapes_quotes_in_strings(rendered):
    data = json.loads(rendered)
    assert data["lines"][0]["desc"] == 'A "quoted" item'


def test_default_filter_fills_empty_numeric(rendered):
    data = json.loads(rendered)
    assert data["total"] == 0  # amount_total was empty -> default(0, true)


def test_flatten_reads_header_and_payload(rendered):
    data = json.loads(rendered)
    assert data["doc"] == "INV-1"
    assert data["currency"] == "USD"
    assert data["automated"] is True  # came from payload['annotation']['automated']


# --- generate_export_settings line splitting (imported helper) ------------------

def _load_server():
    spec = importlib.util.spec_from_file_location("rossum_api_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_splits_on_newlines():
    server = _load_server()
    assert server._template_text_to_multiline("a\nb\nc") == ["a", "b", "c"]


def test_drops_single_trailing_empty_line():
    server = _load_server()
    assert server._template_text_to_multiline("a\nb\n") == ["a", "b"]


def test_tolerates_crlf():
    server = _load_server()
    assert server._template_text_to_multiline("a\r\nb\r\n") == ["a", "b"]


def test_round_trips_with_join():
    server = _load_server()
    lines = ["{", '  "x": 1,', '  "y": [1, 2]', "}"]
    assert server._template_text_to_multiline("\n".join(lines)) == lines
