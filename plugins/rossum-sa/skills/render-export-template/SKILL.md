---
name: render-export-template
description: Author, render, and iterate on Rossum Custom Format Templating export templates — the legacy Jinja2 templates stored inside an export hook's settings.export_configs that turn an annotation into a flat file, CSV, XML, EDI, or custom JSON. Pull a template out of a hook into a local file, render it faithfully against a real annotation to preview the exact export output, and generate hook settings back from an edited template. Use whenever the user wants to edit, test, preview, debug, or build an export template, mentions file_content_template / file_content_template_multiline / export_reference_key, or says things like "render the export template for hook X", "test my export template against annotation Y", "pull the export template", "why does my export file come out wrong", "change the export format". This is the legacy template-based export — NOT the JSON-stage Request Processor (for that, see export-pipeline-reference).
argument-hint: [hook-id-or-url] [annotation-id-or-url] [--key=<export_reference_key>] [--env=<name>]
allowed-tools: Read, Edit, Write, Grep, Glob, Bash, Agent
---

# Render & Iterate on a Custom Format Templating Export Template

You are a Rossum.ai Solution Architect working on an **export template** — the Jinja2 template that the legacy *Custom Format Templating* export step renders into the file a downstream system ingests (flat file, CSV, XML, EDI, custom JSON). The whole reason this is hard by hand: the template lives as escaped lines inside a hook's `settings`, and the only way to know what it actually emits is to render it against a real annotation. Rendering JSON-via-Jinja is unforgiving — one misplaced comma from a `loop.last` guard, one wrong `| tojson` escape, and the export is invalid. So the core of this skill is a **faithful render loop**: pull the template into a local file, render it against a real document with the real Jinja2 engine, eyeball the exact output, edit, repeat — then push the finished template back into the hook.

> Hook / annotation / context: $ARGUMENTS

## When to use this skill

Pick this up automatically when **any** of the following holds:

- The user wants to **edit, preview, test, debug, or build** an export template, or asks "what does my export file look like", "why is the export malformed", "change the export format / mapping".
- The request mentions `export_configs`, `file_content_template`, `file_content_template_multiline`, `export_reference_key`, or "Custom Format Templating".
- The user says "render the export template for hook X", "test this template against annotation Y", "pull the export template into a file", or any equivalent.

**Disambiguation — read this before starting.** Rossum has two unrelated "export" mechanisms:

- **Custom Format Templating** (this skill): a Jinja2 *template* renders the annotation to a file. The template sits in `settings.export_configs[].file_content_template_multiline`.
- **Request Processor** (NOT this skill): a multi-stage JSON pipeline that calls external APIs — no Jinja2 templates. If the hook's settings have `stages`/`requests`/`call_api`, this is the wrong skill → use `export-pipeline-reference`.

If you are unsure which one a hook uses, `rossum_get_hook` and look at the shape of `settings`. For the **format/Jinja2 details** of Custom Format Templating itself, lean on `rossum-reference`; this skill is the *workflow*, not the format spec.

## Safety: remote-write confirmation gate

<HARD-GATE>
Two of the three verbs are read-only. The only operation that **changes a remote Rossum environment** is pushing generated settings back into a hook. Before ANY `rossum_patch_hook` (or `prd2 push`) that writes export settings, you MUST:

1. **Show exactly what will change** — hook id, target environment, the `export_reference_key`, and a diff or summary of the template lines being written.
2. **Wait for an explicit "yes"** — never batch a push in with other steps.
3. **Never push to a production hook.** If the hook belongs to a `prod` queue, stop and ask for a sandbox/UAT hook instead. If you cannot tell the environment, ask before writing.

`rossum_get_hook`, `rossum_extract_export_template`, `rossum_generate_export_settings`, `rossum_generate_export_payload`, and running the local render script are all read-only / local — no confirmation needed.
</HARD-GATE>

## How an export hook stores the template

The template is **not** a single string. It lives under `settings.export_configs` — an **array**, because one hook can emit several files:

```jsonc
{
  "export_configs": [
    {
      "export_reference_key": "coupa_invoice",        // names this output
      "content_encoding": "utf-8",
      "file_content_template_multiline": [             // the template, one array entry per line
        "{",
        "  \"invoice-number\": \"{{ field.document_id }}\",",
        "  \"invoice-lines\": [",
        "  {% for item in field.line_items %}{ ... }{% if not loop.last %},{% endif %}{% endfor %}]",
        "}"
      ]
    }
  ]
}
```

- Join `file_content_template_multiline` with `\n` to reconstruct the template.
- Older hooks may instead carry a single-string `file_content_template` — read both, but **write** the multiline form.
- If `export_configs` has more than one entry and the user did not say which, **list the `export_reference_key`s and ask** which one to work on.

## The render context — what the template can reference

The render engine builds exactly two top-level variables and nothing else:

- **`field`** — the annotation flattened to `{schema_id: value}`. Simple datapoints become their value (the `normalized_value` if present, else `value`); a tuple becomes a nested dict; a multivalue becomes a list. So `field.currency` is a scalar and `field.line_items` is a **list of dicts**.
- **`payload`** — the raw export payload, e.g. `payload['annotation']['automated']`.

Inside `{% for item in field.line_items %}`, `item` is one line-item dict, so `item.item_description` etc. resolve. There are **no custom filters** — `| default(0, true)` and `| tojson` are Jinja2 built-ins. If a template references something outside `field`/`payload`, that is a template bug, not an engine gap.

## The three verbs

### 1. extract — hook → local file

Pull the live template into a local file you can edit.

```
rossum_extract_export_template(hookId=<id>, exportReferenceKey="<key>")   # key optional if only one config
```

Returns the joined template string, its `export_reference_key`, and `content_encoding`. Write it to a local `.j2` file (suggest `<reference_key>.j2`) so you can edit and version it. If the tool reports multiple configs and no key was given, show the keys and ask.

### 2. render — template + annotation → faithful output

This is the heart of the loop. Two steps: get the payload (via the live connection), then render locally with the real engine.

```
rossum_generate_export_payload(hookId=<id>, annotationId=<id>)   # -> the export payload JSON
```

Write that payload to a temp file (e.g. `/tmp/eth-payload-<aid>.json`), then run the bundled render script:

**The payload is sensitive.** A `generate_payload` response contains `rossum_authorization_token` and the hook's `secrets`. Write it to a temp file, never echo it into the conversation, and delete it when done. The rendered *output* is safe (it only contains what the template emits), but the payload file is not.

```
python3 "<this-skill-dir>/scripts/render_export_template.py" \
    --template <your-template>.j2 \
    --payload  /tmp/eth-payload-<aid>.json
```

The script prints the rendered file to stdout — this is **exactly** what the export would produce. If the template emits JSON, pipe it through `python3 -m json.tool` to confirm it parses (a comma/`loop.last` mistake fails here loudly).

**jinja2 availability.** The script needs `jinja2` importable. Try the command above first; if it fails with `ModuleNotFoundError: jinja2`, create a one-time cached venv and use its interpreter:

```
python3 -m venv ~/.cache/rossum-sa/eth-venv && ~/.cache/rossum-sa/eth-venv/bin/pip -q install jinja2
~/.cache/rossum-sa/eth-venv/bin/python "<this-skill-dir>/scripts/render_export_template.py" --template ... --payload ...
```

Reuse that venv on later renders. Never add `jinja2` to the MCP server — it is deliberately dependency-free.

**Offline mode.** If the user hands you a saved payload JSON instead of a live annotation, skip `rossum_generate_export_payload` and point `--payload` straight at their file.

### 3. generate — local file → hook settings

When the template is right, turn the edited file back into hook settings.

```
rossum_generate_export_settings(templatePath="<your-template>.j2", exportReferenceKey="<key>", contentEncoding="utf-8")
```

Returns the `export_configs` JSON block. To apply it, merge it into the hook's `settings` and push **through the hard-gate**:

- In a **prd2 project**, prefer editing the local hook JSON and `prd2 push` (per the project's `CLAUDE.md` workflow) over a direct API patch.
- Otherwise, `rossum_patch_hook(hookId=<id>, settings=<merged settings>)` — only after showing the change and getting a "yes".

## The iteration loop

Repeat until the rendered output matches the target format, then push once at the end:

1. **Extract** the current template to a local file (or start a new file if building from scratch).
2. **Render** it against a representative annotation. Read the actual output. If JSON, validate it parses.
3. **Diff against the goal.** State it plainly: "target format wants X, render produced Y." Common gaps: trailing/leading commas in loops (`{% if not loop.last %}`), unescaped strings (needs `| tojson`), empty values (needs `| default(...)`), date/number formatting.
4. **Edit the local file** and re-render. Stay in this local loop — no remote writes while iterating.
5. **When correct, generate** settings and push **once**, through the hard-gate. Re-extract or re-render against the live hook to confirm the round-trip if the user wants belt-and-suspenders.

Keep a representative annotation handy — ideally one with line items, an empty optional field, and a special character, so the loop exercises the loop/`default`/`tojson` paths.

## Gotchas

- **Commas are the #1 failure.** JSON emitted from a `{% for %}` needs `{% if not loop.last %},{% endif %}` between items and care around optional `{% if %}` blocks. Always validate rendered JSON by parsing it.
- **`tojson` for any string that may contain quotes/newlines/unicode** — `"x": {{ item.description | tojson }}` (note: no surrounding quotes, `tojson` adds them). Hand-quoting `"{{ ... }}"` breaks on special characters.
- **`normalized_value` vs `value`.** The flattener prefers `normalized_value`, so dates/amounts render in their normalized form — the same value the real export sees. If a template looks "wrong", check whether you expected the raw OCR string.
- **Multiple `export_configs`.** Don't guess — list the keys and ask. Generate/extract operate on one key at a time.
- **Write the multiline form.** `generate` emits `file_content_template_multiline`; even if you extracted a legacy single-string `file_content_template`, write back multiline.
- **`content_encoding` matters for non-UTF8 targets** (some EDI/legacy systems want `latin-1` etc.). Preserve what you extracted unless the user wants it changed.
- **The render script is local and pure** — it makes no Rossum calls. All Rossum HTTP goes through the MCP tools, so auth lives in one place (the MCP connection).
- **`generate_payload` returns secrets.** Its response carries `rossum_authorization_token` and the hook's `secrets` alongside the annotation content. Treat the saved payload file as a credential: temp-only, never echoed, deleted after rendering.

## When to stop / hand off

- **Output matches the target and the push succeeded** → confirm with the user, done.
- **The hook turns out to be a Request Processor** (no `export_configs`) → hand off to `export-pipeline-reference`.
- **You need to verify the change across many documents**, not just one → after pushing, hand off to `test-behavioral-equivalence`.
- **The mismatch is upstream data, not the template** (a field is empty because a prior hook/formula didn't populate it) → that's an `iterate` problem on that hook/formula, not a template fix.

## Important

- Render is **always** the real engine — never hand-render JSON in your head and call it the output.
- Two verbs are read-only; only the push writes — and every push passes through the hard-gate, never to prod.
- One `export_reference_key` at a time; ask when there are several.
- Keep the edit→render loop local and fast; touch the remote hook only to extract at the start and push at the end.
