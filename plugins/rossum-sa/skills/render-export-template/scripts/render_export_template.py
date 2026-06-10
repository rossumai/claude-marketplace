#!/usr/bin/env python3
"""Faithfully render a Rossum Custom Format Templating export template.

Pure, offline preview of what the legacy "Custom Format Templating" export step would
emit for a given annotation. Reads a Jinja2 template file and an export payload JSON
file (as returned by the rossum_generate_export_payload MCP tool, or any saved
payload), builds the same {field, payload} context the export hook sees, runs stock
Jinja2, and prints the rendered output to stdout.

Context model (matches the Rossum export hook exactly):
  field   — the annotation content flattened to {schema_id: value}. Datapoints become
            their value (normalized_value if present, else value); a tuple becomes a
            nested dict; a multivalue becomes a list (so field.line_items is a list of
            line-item dicts, and `item.*` resolves inside `{% for item in field.line_items %}`).
  payload — the raw export payload (e.g. payload['annotation']['automated']).

No custom Jinja2 filters are registered — `default`, `tojson`, etc. are built-ins.

Usage:
  python3 render_export_template.py --template path/to/template.j2 --payload path/to/payload.json

The content-flattening logic is adapted from rossumai/export-template-helper (MIT).
"""
import argparse
import json
import os
import sys

try:
    from jinja2 import Environment, FileSystemLoader
except ModuleNotFoundError:
    sys.stderr.write(
        "jinja2 is not importable by this Python interpreter.\n"
        "Either install it (pip install jinja2) or run this script with a venv that has it:\n"
        "  python3 -m venv ~/.cache/rossum-sa/eth-venv\n"
        "  ~/.cache/rossum-sa/eth-venv/bin/pip install jinja2\n"
        "  ~/.cache/rossum-sa/eth-venv/bin/python <this script> --template ... --payload ...\n"
    )
    sys.exit(2)


def build_context_dict(annotation):
    """Flatten an annotation content tree into {schema_id: value}."""
    context = {}
    for section in annotation["content"]:
        context.update(_build_node_dict(section["children"]))
    return context


def _build_node_dict(children):
    return {node["schema_id"]: _build_node_value(node) for node in children}


def _build_node_value(element):
    category = element.get("category")
    if category == "datapoint":
        content = element.get("content") or {}
        normalized = content.get("normalized_value")
        return normalized if normalized else content.get("value")
    if category == "tuple":
        return _build_node_dict(element.get("children", []))
    if category == "multivalue":
        children = element.get("children", [])
        if not children:
            return []
        # A multivalue of datapoints (simple list field) vs. of tuples (line items).
        if children[0].get("category") == "datapoint":
            return _build_node_dict(children)
        return [_build_node_value(node) for node in children]
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Render a Rossum Custom Format Templating export template against a payload."
    )
    parser.add_argument("--template", required=True, help="Path to the Jinja2 template file.")
    parser.add_argument("--payload", required=True, help="Path to the export payload JSON file.")
    args = parser.parse_args()

    try:
        with open(args.payload, encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError as exc:
        sys.stderr.write(f"Could not read payload {args.payload!r}: {exc}\n")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Payload {args.payload!r} is not valid JSON: {exc}\n")
        sys.exit(1)

    if not isinstance(payload, dict) or "annotation" not in payload:
        sys.stderr.write(
            "Payload has no top-level 'annotation' key — expected a hook generate_payload response "
            "of the form {\"annotation\": {\"content\": [...]}}.\n"
        )
        sys.exit(1)

    template_dir = os.path.dirname(os.path.abspath(args.template)) or "."
    template_name = os.path.basename(args.template)
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    field = build_context_dict(payload["annotation"])
    sys.stdout.write(template.render(field=field, payload=payload))


if __name__ == "__main__":
    main()
