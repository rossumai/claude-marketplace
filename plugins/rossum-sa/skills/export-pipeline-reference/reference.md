# Request Processor — Complete Configuration Reference

A flexible, multi-stage engine for integrating Rossum with external APIs. Configure complex export workflows using JSON settings — no code required. Runs as a single serverless function hook, replacing the legacy multi-hook export pipeline.

> **Provenance.** Every behavioural claim below was verified against the engine implementation
> shipped by the current Store template, not against prose docs. Where behaviour is non-obvious the
> responsible internal function is named (`fill_template`, `resolve_var`, `Requester.execute`,
> `TokenCache`, `ResponseHandler.handle_response`) — these are the names that show up in hook-log
> tracebacks, so they are what you grep for when debugging. Read the engine's `config.code` off a
> deployed hook before changing this doc; other descriptions of the extension are known to drift
> from it (e.g. `response.raw`, see [Response Handlers](#response-handlers)).
>
> This doc describes the engine shipped by the **current Store template**. An already-deployed hook may
> be running an older build — check before applying anything here, see
> [the engine version is frozen at creation](#hook-level-the-engine-version-is-frozen-at-creation).

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Settings Structure](#settings-structure)
4. [Variable Templating](#variable-templating)
5. [Evaluate Phase](#evaluate-phase)
6. [Get Content Phase](#get-content-phase)
7. [Call API Phase](#call-api-phase)
8. [Authentication](#authentication)
9. [Response Handlers](#response-handlers)
10. [Advanced Features](#advanced-features)
11. [Failure Semantics](#failure-semantics)
12. [Common Patterns](#common-patterns)
13. [SFTP Export Pattern](#sftp-export-pattern)
14. [Complete Examples](#complete-examples)
15. [Migration from Pipeline v1](#migration-from-pipeline-v1)
16. [Field Reference](#field-reference)
17. [Re-testing an Export](#re-testing-an-export)
18. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

The Request Processor executes **stages** sequentially. Each stage has three optional phases:

```
Stage 1 ──> Stage 2 ──> Stage 3 ──> ...
  │           │           │
  ├─ evaluate (conditions — skip stage if any fail)
  ├─ get_content (fetch data, store in property context)
  └─ call_api (HTTP requests with response handling)
```

**Key characteristics:**
- **Single hook execution** — runs as one hook, not a chain of sequential hooks
- **Three-phase stages** — evaluate → get_content → call_api (all optional)
- **Property context** — intermediate data stored and passed between stages
- **Token caching** — OAuth tokens cached in hook secrets, auto-refreshed on 401

---

## Prerequisites

The Request Processor runs as a serverless function hook. Several org-level and hook-level settings must be correct before any `settings` JSON can work — symptoms manifest as timeouts, missing payload fields, or generic export errors that look nothing like configuration mistakes.

### Org-level: external egress restriction

External HTTPS egress from hook Lambdas may be disabled at the organization level — on some orgs, hooks can only call back into the Rossum API by default, and all other outbound traffic is silently dropped at the TCP layer.

**Symptom:** Connect timeouts to every non-Rossum host (OAuth providers, customer APIs, third-party services). No HTTP error — the request never establishes a connection.

**Fix:** Contact Rossum support to enable external egress for the organization. Hooks require redeploy after the change to inherit the new permission.

To confirm the diagnosis before opening a ticket: add a temporary `call_api` stage targeting `https://httpbin.org/get`. A Lambda timeout with no HTTP status (`FunctionException: Read timeout on endpoint URL...`) confirms egress is blocked; a 200 response means the issue is elsewhere.

### Hook-level: `token_owner` for Rossum API access

`token_owner` is **not optional** — it is the engine's very first check. `rossum_hook_request_handler`
begins with `if not payload.get("rossum_authorization_token"): raise ValueError(...)`, and the token
backs every Rossum-side operation: the `.@` fetch, `get_content` relation lookups, token-cache
persistence (`update_hook_secrets`), and `document_relation` response handlers.

**Symptom:** `ValueError: Rossum authorization token is required.` — raised before any stage runs, so
no request is attempted and no config error is reported.

**Fix:** Set `token_owner` to a user URL when creating the hook, or PATCH it onto an existing hook.

### Hook-level: the engine version is frozen at creation

**A hook keeps the engine code it was created with.** Installing a newer Store template elsewhere does
not upgrade an existing hook, and neither does a `prd2 push` that leaves `config.code` alone. So "what
the current engine does" and "what this hook does" are different questions — a `settings` config that
works against a freshly installed hook can fail on one deployed months earlier, with no version number
anywhere to warn you.

Read the hook's `config.code` and grep for a marker of the behaviour you depend on:

| Marker in `config.code` | Build supports |
|-------------------------|----------------|
| `item_request.fill_template` | per-item filling of `url` / `headers` / `params` (not just the body) |
| `from collections.abc import Sequence` | element-wise expansion of a multivalue `iterate_over` |
| `ignore_timeout` | `timeout` and `ignore_timeout` on a request |
| `oauth2_private_certificate` | the OAuth 1.0a and private-certificate auth types |

An older build fails in two specific ways that look like config bugs rather than version skew:

- A per-item placeholder in `url` / `headers` raises `ResolveVarError` — the older code filled those
  **once** with a context that had no item, then re-filled only `content` per item.
- `iterate_over` on a multivalue field made **one** request with the whole container as the item,
  instead of one request per element — the older normalization only wrapped non-lists, it did not
  expand other sequences. This one is silent: no error, just a single wrong call.

The fix is to replace the hook's `config.code` with the current template's, not to work around it in
`settings`.

### Events other than `annotation_content`

The engine is event-agnostic. If `payload["event"] != "annotation_content"` it fetches
`payload["annotation"]["content"]` itself (`GET`, 20 s timeout), writes it back into the payload and
rewrites `payload["event"]` to `annotation_content` before handing the payload to `TxScript.from_payload`.

So the same hook config works on `export`, `invocation` (manual/scheduled), and other events — the
payload does not need to carry annotation content. The cost is one extra Rossum API call per run,
and a failure here raises before any stage executes.

### Hook-level: `sideload: ["schemas"]`

The Request Processor requires schema sideloading on the hook. (Response handlers also depend on the schema being present — see the [`schema_id` target callout](#response-handlers) below.)

**Symptom:** `PayloadError: Schema sideloading must be enabled!`

**Fix:** Add `"sideload": ["schemas"]` to the hook config.

The same prerequisite applies to any hook using TxScript, where clearing it is a common and
expensive mistake. `txscript-reference` → *Hook object prerequisites* owns the full write-up:
why the only visible symptom is empty fields, where the traceback actually lives, and the
retry amplification.

---

## Settings Structure

The top-level settings object:

```json
{
  "settings": {
    "stages": [
      {
        "evaluate": [ /* optional conditions */ ],
        "get_content": [ /* optional data retrieval */ ],
        "call_api": [ /* optional API calls */ ]
      }
    ],
    "debugging": false
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `stages` | Array | Yes | Ordered list of Stage objects |
| `debugging` | Boolean | No | Enable debug logging (default: false) |

---

## Variable Templating

Use `{variable.path}` syntax to inject dynamic values anywhere in the configuration.

### Available Contexts

| Context | Prefix | Description | Example |
|---------|--------|-------------|---------|
| **Payload** | `payload.` | Raw webhook data | `{payload.annotation.id}` |
| **Fields** | `field.` | Extracted field data | `{field.invoice_number.value}` |
| **Property** | `property.` | Data from `get_content` or response handlers | `{property.po_data}` |
| **Sequence** | `sequence` | Current iteration index (0-based) | `{sequence}` |
| **Token** | `token` | Auth token from `auth` config | `{token}` |
| **Item** | *`iteration_item_name`* | Current item during `iterate_over` (default `item`) | `{item.value}` |

`sequence` and `token` exist **only while filling the `request` object** — `sequence` is `0` outside
iteration. They are *not* available in `auth`: the auth block is filled before the token exists, with
`payload` / `field` / `property` only, and because auth fills with `raise_exceptions=True`, a
`{token}` or `{sequence}` placeholder there aborts the whole `call_api` entry. `evaluate` conditions
and `get_content` queries likewise see only `payload` / `field` / `property`.

### Value Resolution Rules

`fill_template` treats a string one of two ways, and the distinction decides the **Python type** that
reaches `requests`:

| Form | Rule | Result |
|------|------|--------|
| **Exact match** — the placeholder is the whole string (surrounding whitespace allowed) | returns the resolved value **with its original type** | `"{payload.n}"` → `5` (int), `"{payload.d}"` → `{"a": 1}` (dict), `"{payload.b}"` → `b"..."` (bytes), unresolved → `None` |
| **Composite** — any other text around the placeholder | `str(value)` per placeholder, and `None` → `""` | `"N={payload.n}"` → `"N=5"`, `"x={payload.nul}y"` → `"xy"` |

This is why a JSON body can carry real numbers, objects and binary content (`"amount": "{field.total.value}"`
as an exact match preserves the value), and why a composite URL never contains the literal `None`
— a missing value silently collapses to an empty segment instead.

### `{field.x.value}` vs `{field.x}`

Both resolve, and they are **not** the same:

| Expression | Resolves to | Notes |
|------------|-------------|-------|
| `{field.x.value}` | the raw captured string — **always `str`**, whatever the schema field's type | `"1234.50"` for a number field. This is the safe default. |
| `{field.x}` | the txscript typed proxy (`StringValue`, `NumberValue`, `DateValue`, …) | `1234.5` (float-like) for a number field; a `str` subclass for a string field |

In composite strings the two are interchangeable (both stringify). They diverge on **exact match**,
where `{field.x}` hands a typed proxy straight to the request — harmless for `content`, but it means
a numeric field renders as `1234.5` (not `"1234.50"`), and it changes how `evaluate` compares the
value (see [Status Code Comparison](#status-code-comparison-fails)).

**Prefer `{field.x.value}`** unless you specifically want the typed value in a JSON body.

### URL Auto-Fetching

When a variable resolves to a URL, use `.@` to fetch its content:

```json
// Returns URL string (no fetch)
"{payload.document.url}"

// Fetches the URL, returns the full object
"{payload.document.url.@}"

// Fetches and accesses nested property
"{payload.document.content.@.datapoints[0].value}"

// Fetch annotation content
"{payload.annotation.content.@}"
```

**Rules:**
- `.url` ending → returns URL string (no fetch)
- `.@` operator → fetches the URL content (mandatory for accessing properties of fetched objects)
- **If the value is not a URL at all**, `.@` is simply consumed and the value passes through unchanged — no fetch, no error.
- **Restricted to the hook's parent domain.** `resolve_var.is_same_origin` compares the *last two* dot-separated labels of the URL host against those of the hook's `base_url` (e.g. a hook on `elis.rossum.ai` can fetch anything ending in `rossum.ai`, including `api.elis.rossum.ai`; it cannot fetch `httpbin.org` or `*.rossum.app`). Use a `call_api` stage with an explicit request to reach third-party hosts.
- **What a cross-origin `.@` does depends on where the template is filled** — and in conditions it does not fetch at all:

| Where | Fetches? | Cross-origin `.@` result |
|-------|----------|--------------------------|
| `auth` and `request` templates | yes | raises `ResolveVarError` → the whole `call_api` entry is aborted and logged, `exception_occurred` set |
| `get_content` `explicit` queries | yes | resolves to `None` silently |
| `evaluate` and response-handler conditions | **never** | `.@` is consumed and the **raw URL string** passes through unchanged |

Conditions are the exception because they are filled without an API client at all, so `.@` is inert
there regardless of origin — `{"payload.document.content.@": {"$ne": null}}` tests the URL string, not
the document. Fetch in a `get_content` rule first and test the resulting `property`.

Outside conditions, the same split governs **every** unresolvable expression, not just `.@`: a typo in
a `request` URL placeholder aborts that API call loudly, while the same typo in a `get_content`
`explicit` query yields `None` quietly.

### Function Wrappers

| Function | Description | Example |
|----------|-------------|---------|
| `base64` | Base64 encode a value | `{base64(payload)}` |

```json
// Base64 encode the entire payload
"payload": "{base64(payload)}"

// Base64 encode binary content
"file_content": "{base64(payload.document.content.@)}"
```

- Bytes values are encoded directly
- Other types are converted to string first, then encoded
- Returns `None` if the inner expression cannot be resolved

---

## Evaluate Phase

Check conditions before running a stage. If any condition fails, the entire stage is skipped.

```json
"evaluate": [
  {
    "name": "condition_description",
    "condition": { /* MongoDB-style filter query */ }
  }
]
```

### Filter Query Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$eq` | Equals | `{"field.status.value": {"$eq": "approved"}}` |
| `$ne` | Not equals | `{"field.total.value": {"$ne": null}}` |
| `$gt` / `$gte` | Greater than (or equal) | `{"field.amount.value": {"$gt": 1000}}` |
| `$lt` / `$lte` | Less than (or equal) | `{"field.amount.value": {"$lte": 5000}}` |
| `$in` / `$nin` | In / not in list | `{"field.status.value": {"$in": ["draft", "pending"]}}` |
| `$exists` | Field exists | `{"field.po_number.value": {"$exists": true}}` |
| `$regex` | Regex match | `{"field.email.value": {"$regex": "@gmail\\.com$"}}` |
| `$size` | Array length — takes a plain int or a nested operator dict | `{"field.line_items": {"$size": {"$gt": 0}}}` |
| `$and` / `$or` | Logical operators | `{"$and": [{...}, {...}]}` |

`$and` and `$or` are the **only** logical operators — there is no `$not` or `$nor`. Invert by
choosing the opposite comparison (`$ne`, `$nin`, `$exists: false`).

**Operator edge cases** (from `LOCAL_FILTER_OPERATORS` / `evaluate_filter`):

- `$gt` / `$gte` / `$lt` / `$lte` return **false** if either side is `None` — they never raise and never compare `None`.
- `$regex` returns false unless the resolved value is a `str` (a number field referenced without `.value` will not match).
- `$in` / `$nin` need an operand that supports `in`; anything else is false.
- `$exists: true` means "resolved to something other than `None`"; `$exists: false` matches both a missing path and a `None` value. An **empty** field resolves to `""`, which *exists* — pair the two: `{"$exists": true, "$ne": ""}`.
- Multiple operators in one condition dict are ANDed.
- An operator the engine doesn't know fails the condition (no error) — a typo like `$gte:` vs `$get:` silently skips the stage.

### Conditions are templated first

`check_condition` runs `fill_template` over the condition **before** evaluating it, so placeholders
work on the right-hand side. This lets you compare two dynamic values without a helper field:

```json
"evaluate": [
  {
    "name": "matches_expected_supplier",
    "condition": {"field.supplier_id.value": {"$eq": "{property.expected_supplier_id}"}}
  }
]
```

**An unresolvable placeholder on the right-hand side becomes `None`, not `""`** — conditions fill with
`raise_exceptions=False`, and a placeholder that is the whole string takes the exact-match path, which
returns the resolved value (here `None`) rather than interpolating. That has a trap:

| Left side | Right side | Result |
|-----------|------------|--------|
| resolves | unresolvable → `None` | `$eq` false — the stage skips, which is usually what you wanted |
| **also unresolvable → `None`** | unresolvable → `None` | `$eq` **true** — `None == None`, so the condition **passes** |

So a typo in *both* sides of a comparison — or a stage guarding on two fields that are both absent —
silently *fires* instead of skipping. This is the opposite of the safe default, and nothing is logged.

Guard against it by asserting existence separately rather than relying on a comparison:

```json
"evaluate": [
  {"name": "supplier_present", "condition": {"field.supplier_id.value": {"$exists": true, "$ne": ""}}},
  {"name": "matches_expected", "condition": {"field.supplier_id.value": {"$eq": "{property.expected_supplier_id}"}}}
]
```

Conditions in a composite string (`"prefix-{property.x}"`) do interpolate and yield `""` for an
unresolved placeholder — only the whole-string form produces `None`.

### Examples

```json
// Simple condition
"evaluate": [
  {
    "name": "check_invoice_exists",
    "condition": {
      "field.invoice_number.value": {"$exists": true, "$ne": ""}
    }
  }
]

// Complex condition
"evaluate": [
  {
    "name": "check_amount_and_status",
    "condition": {
      "$and": [
        {"field.total_amount.value": {"$gt": 1000}},
        {"field.status.value": {"$eq": "approved"}}
      ]
    }
  }
]

// Check previous stage result
"evaluate": [
  {
    "name": "api1_succeeded",
    "condition": {
      "field.api1_status_code": {"$in": ["200", "201"]}
    }
  }
]
```

→ Drop-in blueprint: `export-evaluate-guard` (blueprints/export/).

---

## Get Content Phase

Fetch data from relations or fields and store it in `property` for later use.

```json
"get_content": [
  {
    "name": "stored_name",
    "source": "source_type",
    "query": { /* depends on source */ }
  }
]
```

### Source Types

Seven sources are accepted (`GetContentPhase.source`):

| Source | Lists | Fetches documents? |
|--------|-------|--------------------|
| `document_relation` | `GET /document_relations?annotation=<id>` | no — metadata only |
| `document_relation_content` | same | yes — **every** document in each matched relation |
| `relation` | `GET /relations?annotation=<id>` | no |
| `relation_content` | same | yes — one entry per related annotation |
| `parent_relation` | `GET /relations?parent=<id>&type=attachment` | no |
| `parent_relation_content` | same | yes — one entry per related annotation |
| `explicit` | nothing — pure templating | only via `.@` |

Relation lists are fetched **lazily and cached per run**, so several `get_content` rules against the
same source cost one API call total.

#### `document_relation_content` (Recommended — most common)

Fetches document relations AND retrieves the actual document metadata/content.

```json
{
  "name": "invoice_payload",
  "source": "document_relation_content",
  "query": {"key": {"$eq": "create_draft"}}
}
```

Access content: `{property.invoice_payload.content.@}`
Access filename: `{property.invoice_payload.original_file_name}`

**Regex matching** for multiple relations:
```json
{
  "name": "additional_attachments",
  "query": {"key": {"$regex": "^attachment_email_attachments_\\d{8,10}(?:_\\d+)?$"}},
  "source": "document_relation_content"
}
```

**Every** document in each matched relation's `documents[]` is fetched (`get_content` loops
`for document_url in result["documents"]`) and the results are flattened into one list. A single
relation holding three documents therefore yields three entries — which is what makes
`iterate_over: "property.<name>"` upload all of them (see [Iteration Over Document Relations](#iteration-over-document-relations)).

Each entry is the **document** object (`original_file_name`, `content`, `mime_type`, …), not the
relation — relation keys/IDs are not carried over. Use `document_relation` when you need those.

#### `document_relation`

Returns relation metadata only (IDs, URLs, keys) — without fetching document content.

```json
{
  "name": "po_relation",
  "source": "document_relation",
  "query": {"key": {"$eq": "purchase_order"}}
}
```

Use when you need relation IDs or to check if a relation exists.

#### `relation`

Fetches annotation relations (metadata only).

```json
{
  "name": "parent_annotation",
  "source": "relation",
  "query": {"type": {"$eq": "parent"}}
}
```

#### `relation_content`

Same list as `relation`, then enriched: each related **annotation** in the relation becomes its own
entry, carrying the relation's own keys plus `annotation`, `document`, and `content`.

```json
{
  "name": "sibling_docs",
  "source": "relation_content",
  "query": {"type": {"$eq": "duplicate"}}
}
```

Access: `{property.sibling_docs.original_file_name}` is **not** available here — the document object
is nested. Use `{property.sibling_docs.document.original_file_name}` and
`{property.sibling_docs.content.@}` (`content` is a URL; `.@` fetches the bytes).

The enrichment batches its lookups — all annotations in one `GET /annotations?id=…`, all documents in
one `GET /documents?id=…` — so cost is two calls regardless of how many relations matched.

#### `parent_relation` / `parent_relation_content`

Looks **up** the tree instead of down: `GET /relations?parent=<this annotation id>&type=attachment`,
i.e. relations where the current annotation is the parent. The current annotation is stripped out of
each relation's `annotations[]`, so only the other side remains.

```json
{
  "name": "child_attachments",
  "source": "parent_relation_content",
  "query": {}
}
```

`parent_relation` returns metadata only; `parent_relation_content` enriches exactly like
`relation_content` (same `annotation` / `document` / `content` shape, same batched lookups).

#### `explicit`

Direct field/payload access with templating.

```json
{
  "name": "email_content_url",
  "source": "explicit",
  "query": ["{payload.document.email}/content"]
}
```

Multiple values:
```json
{
  "name": "metadata",
  "source": "explicit",
  "query": [
    "{field.vendor_name.value}",
    "{field.invoice_date.value}",
    "{payload.annotation.id}"
  ]
}
```

### Result Shape Rule

- **Exactly 1 match** → `property.name` is a single object (not a list)
- **Multiple matches** → `property.name` is a list
- **No match** → `property.name` is an empty list `[]`

This auto-unwrapping is the single biggest footgun in `get_content`: a config written and tested
against a document with two attachments (`{property.att[0].original_file_name}`) breaks on a document
with one, and vice versa. `iterate_over` is immune — it normalises a bare object back into a one-item
list — so **prefer iterating over indexing** whenever the match count can vary.

---

## Call API Phase

Execute HTTP requests with dynamic data.

```json
"call_api": [
  {
    "name": "api_call_name",
    "auth": { /* optional */ },
    "request": { /* required */ },
    "priority_response_handlers": [ /* optional */ ],
    "response_handlers": [ /* optional */ ]
  }
]
```

### Request Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | String | Yes | API endpoint URL. Supports templating. |
| `method` | String | Yes | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `content` | Any | No | Request body. Format depends on `content_type`. |
| `content_type` | String | No | `json`, `form`, `files`, or `multipart`. **Default: none** — see below. |
| `headers` | Object | No | Custom headers. Supports templating. |
| `params` | Object | No | Query-string parameters. Supports templating. Also signed into the OAuth 1.0a base string. |
| `timeout` | Integer | No | Per-request timeout in seconds. **Default: 10.** |
| `ignore_timeout` | Boolean | No | Treat a timeout as skippable instead of a pipeline error. Default `false`. |
| `iterate_over` | String | No | Path to a list to iterate over. |
| `iteration_item_name` | String | No | Variable name for current item (default: `item`). |

The **10-second default `timeout`** catches people out: an ERP that takes 15 s to create an invoice
fails every time with a timeout, no HTTP status, and (without `ignore_timeout`) a generic pipeline
error. Raise `timeout` on any call to a system known to be slow.

### Content Types

`Requester.execute` dispatches on `content_type` and maps `content` onto exactly one `requests`
argument:

| `content_type` | Sent as | `requests` arg | Use for |
|----------------|---------|----------------|---------|
| `json` | `application/json` (set by `requests`) | `json=` — **re-serializes** `content` | Structured data |
| `form` | `application/x-www-form-urlencoded` | `data=` | Simple key-value pairs |
| `files` | `multipart/form-data` | `files=` | File uploads |
| `multipart` | `multipart/form-data` | `files=` + `data=` — list/tuple values become files, everything else form fields | Mixed files and data fields |
| *omitted / `null`* | **whatever you set in `headers`** | `data=` — `content` passed through untouched | Pre-built raw bodies |

Note there is no separate "default" branch: `form` and *omitted* take the same `data=` path. The
difference is intent, and it matters for strings.

### Raw bodies: omit `content_type`

Because an omitted `content_type` routes `content` to `data=` unchanged, and an **exact-match**
placeholder preserves the value's original type, you can send a body that was built elsewhere
**verbatim** — set the `Content-Type` header yourself:

```json
{
  "name": "patch_resource",
  "request": {
    "url": "https://api.example.com/v1/resources/{field.resource_id.value}",
    "method": "PATCH",
    "content": "{field.patch_body.value}",
    "headers": {
      "Content-Type": "application/json-patch+json",
      "Authorization": "Bearer {token}"
    }
  }
}
```

Here `field.patch_body` holds a pre-serialized JSON-Patch array (built by a formula or an upstream
hook) and it hits the wire exactly as stored.

**Do not set `content_type: "json"` for this.** That routes the body to `json=`, which serializes it
*again* — a string body arrives as a JSON **string** containing your JSON (`"[{\"op\":\"replace\"…}]"`)
rather than as an array, and the target API rejects it. The rule:

- body is a **dict/list** you want serialized → `content_type: "json"`
- body is **already a string** in its final form → omit `content_type`, set `Content-Type` in `headers`

### Request Examples

**GET:**
```json
{
  "name": "get_vendor",
  "request": {
    "url": "https://api.example.com/vendors/{field.vendor_id.value}",
    "method": "GET"
  }
}
```

**POST with JSON:**
```json
{
  "name": "create_invoice",
  "request": {
    "url": "https://api.example.com/invoices",
    "method": "POST",
    "content_type": "json",
    "content": {
      "invoice_number": "{field.invoice_number.value}",
      "amount": "{field.total_amount.value}"
    },
    "headers": {"Accept": "application/json"}
  }
}
```

**POST with property content** (pass through fetched payload):
```json
{
  "name": "create_draft",
  "request": {
    "url": "{field.api_url.value}",
    "method": "POST",
    "content": "{property.create_draft.content.@}",
    "content_type": "json",
    "headers": {
      "Accept": "application/json",
      "Content-Type": "application/json",
      "Authorization": "Bearer {token}"
    }
  }
}
```

**File upload:**
```json
{
  "name": "upload_pdf",
  "request": {
    "url": "https://api.example.com/upload",
    "method": "POST",
    "content_type": "files",
    "content": {
      "file": [
        "{payload.document.original_file_name}",
        "{payload.document.content.@}"
      ]
    }
  }
}
```

Format: `{"field_name": ["filename", "content", "optional_mime_type"]}`

**Multipart (files + data):**
```json
{
  "name": "upload_with_metadata",
  "request": {
    "url": "https://api.example.com/attachments",
    "method": "POST",
    "content_type": "multipart",
    "content": {
      "attachment[file]": [
        "{payload.document.original_file_name}",
        "{payload.document.content.@}",
        "application/pdf"
      ],
      "attachment[intent]": "Supplier"
    }
  }
}
```

In multipart: list/tuple values are sent as files, string/other values as form fields.

---

## Authentication

`auth` accepts one of **three** strategies, selected by a `type` discriminator:

| `type` | Strategy | `{token}` available? | 401 retry? |
|--------|----------|----------------------|------------|
| `bearer` *(default — `type` may be omitted)* | Fetch a token from `url`, cache it, you place it in a header | yes | yes |
| `oauth1` | OAuth 1.0a TBA — signs an `Authorization: OAuth …` header per request | **no** | **no** |
| `oauth2_private_certificate` | OAuth 2.0 client credentials via a JWT client assertion signed with a private certificate | yes | yes |

`type` is optional only for `bearer`; the other two require it. Whichever you pick, `auth` templates
are filled with `raise_exceptions=True` — an unresolvable placeholder in `auth` aborts the whole
`call_api` entry before any request is sent.

### OAuth Bearer Token

```json
{
  "name": "create_invoice",
  "auth": {
    "url": "https://api.example.com/oauth/token",
    "method": "POST",
    "content_type": "form",
    "content": {
      "grant_type": "client_credentials",
      "client_id": "{field.oauth_client_id.value}",
      "client_secret": "{payload.secrets.client_secret}"
    },
    "credential_key": "access_token"
  },
  "request": {
    "url": "https://api.example.com/invoices",
    "method": "POST",
    "headers": {
      "Authorization": "Bearer {token}"
    }
  }
}
```

### Auth Object Fields (`bearer`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | String | No | `bearer` (the default when omitted) |
| `url` | String | Yes | Auth endpoint URL. Supports templating. Also derives the cache key. |
| `method` | String | Yes | HTTP method for auth request. |
| `content_type` | String | No | `json` → `json=`; anything else (incl. omitted) → `data=`. |
| `content` | Object | No | Auth request body (credentials). |
| `headers` | Object | No | Custom headers for auth request. |
| `params` | Object | No | Query parameters for auth request. |
| `credential_key` | String | Yes | Dot-path to token in response (e.g., `access_token` or `data.token`). |

The auth request always uses the fixed 10-second default timeout — `timeout` on the `request` object
does not apply to it, and `ignore_timeout` never suppresses an auth timeout.

If `credential_key` does not resolve in the response, the engine raises `ResolveVarError` and logs
`Auth token not found at key '<key>'` — check that log line before assuming the credentials are wrong.

### OAuth 1.0a (`type: "oauth1"`)

For APIs using token-based auth with per-request HMAC signatures (e.g. NetSuite SuiteTalk REST).
There is **no token endpoint and no `{token}` variable** — the engine computes an
`Authorization: OAuth …` header for each request from the request's own method, URL and `params`,
and injects it just before sending (so during `iterate_over` each per-item URL is signed correctly).

```json
"auth": {
  "type": "oauth1",
  "consumer_key": "{payload.secrets.consumer_key}",
  "consumer_secret": "{payload.secrets.consumer_secret}",
  "token_key": "{payload.secrets.token_key}",
  "token_secret": "{payload.secrets.token_secret}",
  "realm": "{field.account_id.value}",
  "signature_method": "HMAC-SHA256"
}
```

`signature_method` is `HMAC-SHA256` (default) or `HMAC-SHA1`. `realm` appears in the header but is
excluded from the signature base string. **A 401 is not retried** — with per-request signatures a 401
means bad credentials or clock skew, not an expired token.

### OAuth 2.0 with a private certificate (`type: "oauth2_private_certificate"`)

Client-credentials flow where the client secret is replaced by a JWT client assertion signed with your
private key. Behaves like `bearer` downstream: exposes `{token}`, cached, retried on 401.

```json
"auth": {
  "type": "oauth2_private_certificate",
  "url": "https://api.example.com/oauth2/v1/token",
  "client_id": "{payload.secrets.client_id}",
  "certificate_id": "{payload.secrets.certificate_id}",
  "private_certificate": "{payload.secrets.private_certificate}",
  "scope": ["rest_webservices"],
  "signature_algorithm": "PS256"
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `client_id` | — | becomes the JWT `iss` |
| `certificate_id` | — | becomes the JWT header `kid` |
| `private_certificate` | — | unencrypted RSA key in PEM, PKCS#1 or PKCS#8 |
| `scope` | `["rest_webservices"]` | list or string |
| `signature_algorithm` | `PS256` | `PS256/384/512`, `RS256/384/512` |
| `assertion_expiry` | `3600` | JWT lifetime, seconds |
| `audience` | the token `url` | JWT `aud` |
| `credential_key` | `access_token` | dot-path in the token response |
| `grant_type` | `client_credentials` | |
| `client_assertion_type` | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` | |
| `additional_claims` | `{}` | merged into the JWT last — can override the defaults above |
| `additional_token_params` | `{}` | merged into the token request body last |

**Passphrase-protected / encrypted private keys are rejected** (`encrypted private keys are not
supported by the built-in signer`) — the signer is dependency-free and handles unencrypted RSA only.
Store the PEM in hook secrets and reference it, never inline.

### Token Behavior

For `bearer` and `oauth2_private_certificate` (`TokenCache`):

- **Cache location** — `payload["secrets"]`, under the derived key
  `_request_processor_token_cache_<sanitized_host>_<hash>`, where `<hash>` is the first 16 hex chars of
  `sha256(json.dumps(auth_config, sort_keys=True))` and `<sanitized_host>` is the auth URL's hostname
  with non-alphanumerics replaced by `_`, truncated to 32 chars.
- **Persistence** — on a cache miss the fetched token is written into `payload["secrets"]` *and*
  PATCHed onto the hook (`PATCH /hooks/{id}` with the full secrets dict), so it survives across
  function executions. If that PATCH fails it is only logged as a warning — the run continues with an
  in-memory token and re-fetches next time.
- **Cache key is computed after templating.** The hash covers the *resolved* auth config, so two
  `call_api` entries whose auth blocks differ only in a placeholder that resolves to the same value do
  share a token. Conversely, changing anything real (a scope, a header, the URL) mints a separate
  cache entry.
- **401 handling** — the cached token is dropped from `payload["secrets"]`, a fresh one is fetched, the
  current request is re-rendered with the new `{token}` and retried **once**. During `iterate_over`,
  later items pick up the refreshed token as they are rendered. A second 401 is passed through to the
  response handlers.
- Available as `{token}` in `request` templates (`url`, `headers`, `params`, `content`).
- You **must** place it yourself: `"Authorization": "Bearer {token}"`. The engine never sets an
  Authorization header for token-based auth.

> The invalidation on 401 removes the key from the in-memory payload only — it does not immediately
> PATCH the deletion. A hook that 401s and then dies before the refreshed token is persisted will
> re-fetch on the next run rather than serving a stale token.

Because the pipeline **writes the cached token into hook secrets at runtime**, the
hook's `secrets_schema` must use the **open string-map shape** — declare the static
credential keys for `__change_me__` prefill and keep `additionalProperties` open for the
cache keys (a closed `"additionalProperties": false` schema would reject the token write
on the first refresh):

```json
"secrets_schema": {
  "type": "object",
  "properties": {
    "client_secret": { "type": "string", "minLength": 1, "description": "OAuth client secret" }
  },
  "additionalProperties": { "type": "string" }
}
```

→ Drop-in blueprint: `export-oauth-token-cache` (blueprints/export/).

---

## Response Handlers

Process API responses and extract/store data.

### Response Handler Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `condition` | Object | No | Filter query — handler only runs if condition passes. Evaluated **after** extraction. |
| `target_type` | String | Yes | `schema_id`, `property`, or `document_relation`. |
| `target_key` | String | Yes | Field name, property key, or relation key. Supports `{sequence}`. |
| `value` | ValueConfig | No* | How to extract value. *Required for `schema_id` and `property`. |

### Target Types

| Type | Description | Notes |
|------|-------------|-------|
| `schema_id` | Write to a document field | Dicts stored as JSON string. Repeated writes to one key collapse to a list — see [Iteration](#iteration-over-lists) |
| `property` | Store in property context | Available in later stages via `{property.key}`. Overwrites a duplicate key and logs a warning |
| `document_relation` | Save response as new Rossum document | Creates/updates relation with given key |

### `{sequence}` in `target_key`

`target_key` is run through `.format(sequence=…)`, where `sequence` is the current item index during
`iterate_over` and `0` otherwise. Use it to fan results out to separate fields:

```json
{"target_type": "schema_id", "target_key": "line_result_{sequence}", "value": {"response.body": {"$jmespath": "id"}}}
```

> **Caveat for `document_relation`.** The `{sequence}` substitution reaches the uploaded document's
> *name* but **not** the relation's `key` — the relation is created with the raw, unformatted
> `target_key`. So `target_key: "resp_{sequence}"` during a 3-item iteration produces three documents
> named `resp_0_response`, `resp_1_response`, `resp_2_response`, all attached to a single relation
> whose key is the literal string `resp_{sequence}` — and because the relation is *replaced* each
> time, only the last document survives. Use a static `target_key` for `document_relation` targets.

### Handler conditions can test the extracted value

Extraction runs first, and the result is exposed to the condition as `found_value`. This is the
cleanest way to write "only store it if we actually got something":

```json
{
  "condition": {"found_value": {"$exists": true, "$ne": ""}},
  "target_type": "schema_id",
  "target_key": "remote_id",
  "value": {"response.body": {"$jmespath": "id"}}
}
```

Conditions can also reference any `response.*` path, and are templated before evaluation like
`evaluate` conditions. A handler whose condition fails returns "not handled" — which for
`priority_response_handlers` means the next handler gets a turn.

> **Important — `schema_id` targets must already exist in the schema.** Response handlers with `target_type: "schema_id"` use Python `setattr` on the annotation field tree. If `target_key` names a field that does not exist, the handler raises `AttributeError`, `exception_occurred` becomes true, and the export fails with the generic Automation Blocker message `"Some exception occurred during during export pipeline"` *(verbatim — the duplicated "during" is in the source)* — with no hint about which field is missing.
>
> Before referencing fields like `api1_status_code` or `api1_response_body` in a response handler, add them as schema fields with `ui_configuration.type: "data"` (hidden from the UI). The `"data"` type also avoids the "field has no extraction source" validation warning that `"captured"` would trigger for hook-populated fields.

### Value Extractors (ValueConfig)

Format: `{"context_path": {"$operator": "query"}}`

| Operator | Use For | Example |
|----------|---------|---------|
| `$jmespath` | JSON responses | `"$jmespath": "data.items[0].name"` |
| `$xmlpath` | XML responses | `"$xmlpath": ".//status"` |
| `$regex` | Text/HTML responses | `"$regex": "Order: (\\d+)"` |

Exactly one operator per `value` — zero or two raises a config error. The shorthand
`{"<context_path>": {"$op": "…"}}` must be a single-key dict, and no extra keys are accepted.

**Operator specifics:**

- `$xmlpath` **requires an XML response.** If the response `Content-Type` does not contain `xml` the
  handler raises `ValueError: $xmlpath operator can only be used with XML responses`. Multiple matching
  elements return a **list** of their texts; an element with no text returns its serialized XML.
- `$regex` stringifies a non-string target first, so it also works on `response.body` dicts. It returns
  capture **group 1** if the pattern has one, otherwise the whole match; no match → `None`.
- `$jmespath` on a missing path returns `None` rather than erroring.

**Context paths** (the keys of the response object the engine builds):

| Path | Type | Description |
|------|------|-------------|
| `response.status_code` | Integer | HTTP status code |
| `response.headers` | Object | Normalized headers (`-` → `_`, lowercased) |
| `response.headers_raw` | List | Header name/value tuples, original casing |
| `response.body` | Any | Parsed body — **only present for recognised content types**, see below |
| `response.text` | String | Response as text |
| `response.content` | Bytes | Raw response bytes |
| `response.url` | String | Final URL (after redirects) |
| `response.ok` | Boolean | True if status < 400 |
| `response.reason` | String | HTTP reason phrase (`OK`, `Not Found`, …) |
| `response.elapsed` | Float | Request duration in seconds |
| `response.encoding` | String | Response encoding |
| `response.cookies` | Object | Response cookies |
| `response` | Object | All of the above as one object |

> **`response.raw` does not exist.** The key is `headers_raw`. `response.raw` (and
> `{"response": {"$jmespath": "raw"}}`) silently resolves to `None` — it does not error, so a handler
> written against it stores an empty field and looks like an API problem. Some other descriptions of
> this extension still list `response.raw`; the engine has only `headers_raw`.

**Body parsing by content type:**

| `Content-Type` header | `response.body` |
|-----------------------|-----------------|
| contains `application/json` | JSON object/array |
| contains `xml` | the XML **string** (use `$xmlpath`) |
| contains `text` | plain text string |
| contains `unknown`, **or the header is absent** | **not set at all** |
| anything else | raw bytes |

When `body` is not set, what `response.body` resolves to depends on which path reads it:

- **In a `value` extractor** — `{}` (an empty dict), because the context walk substitutes an empty dict
  for each missing segment. A `$jmespath` against it returns `{}`/`None` rather than failing.
- **In a `condition`** — `None`, because conditions resolve the path directly with no such
  substitution. So `{"response.body": {"$exists": false}}` is the way to test for a bodyless response;
  `{"$eq": {}}` will not match.

A response with no `Content-Type` header is common for `204 No Content` and some file-upload endpoints:
read `response.text`, `response.status_code` or `response.ok` instead of `response.body`.

### Response Handler Examples

```json
// Extract nested JSON value
{
  "target_type": "schema_id",
  "target_key": "vendor_country",
  "value": {"response.body": {"$jmespath": "vendor.address.country"}}
}

// Save status code
{
  "target_type": "schema_id",
  "target_key": "api_status_code",
  "value": {"response": {"$jmespath": "status_code"}}
}

// Save entire response body
{
  "target_type": "schema_id",
  "target_key": "full_response",
  "value": {"response.body": {"$jmespath": "@"}}
}

// Conditional handler — only on success
{
  "condition": {"response.status_code": {"$eq": 200}},
  "target_type": "schema_id",
  "target_key": "success_message",
  "value": {"response.body": {"$jmespath": "message"}}
}

// Custom response structure (note: headers_raw, not raw)
{
  "target_type": "schema_id",
  "target_key": "api_metadata",
  "value": {"response": {"$jmespath": "{status_code: status_code, headers: headers, raw: headers_raw}"}}
}

// Store in property for later stages
{
  "target_type": "property",
  "target_key": "vendor_data",
  "value": {"response.body": {"$jmespath": "@"}}
}

// Save as document relation
{
  "target_type": "document_relation",
  "target_key": "api_response"
}

// Extract from XML
{
  "target_type": "schema_id",
  "target_key": "order_status",
  "value": {"response.body": {"$xmlpath": ".//order/status"}}
}

// Extract with regex (capturing group returns first group)
{
  "target_type": "schema_id",
  "target_key": "confirmation_number",
  "value": {"response.body": {"$regex": "Confirmation: ([A-Z0-9]+)"}}
}
```

### Priority Response Handlers

For advanced early-exit behavior. Stop after first successful handler.

```json
{
  "priority_response_handlers": [
    {
      "condition": {"response.status_code": {"$eq": 200}},
      "target_type": "schema_id",
      "target_key": "success_data",
      "value": {"response.body": {"$jmespath": "data"}}
    },
    {
      "condition": {"response.status_code": {"$eq": 404}},
      "target_type": "schema_id",
      "target_key": "error_message",
      "value": {"response.body": {"$jmespath": "error"}}
    }
  ]
}
```

**Execution order:** Priority handlers run first (stop at first match), then regular `response_handlers` always run all.

---

## Advanced Features

### Iteration Over Lists

Execute the same API call for each item in a list.

```json
{
  "name": "process_line_items",
  "request": {
    "url": "https://api.example.com/items",
    "method": "POST",
    "content_type": "json",
    "iterate_over": "field.line_items",
    "iteration_item_name": "line_item",
    "content": {
      "sku": "{line_item.sku.value}",
      "quantity": "{line_item.quantity.value}",
      "index": "{sequence}"
    }
  }
}
```

**How it works:**
1. `iterate_over` resolves to a list (e.g., `field.line_items` or `property.loaded_items`)
2. One request per item in the list
3. Current item available as `{line_item}` (or custom `iteration_item_name`)
4. `{sequence}` is the 0-based iteration index
5. Global variables (`{field.*}`, `{payload.*}`) still accessible

### The item is available across the whole request

**The per-item variable resolves in `url`, `headers`, `params` **and** `content`** — the engine copies
the whole request template and re-fills every one of those fields with that item's context on each
pass. Per-item endpoints are therefore first-class:

```json
{
  "name": "patch_each_line",
  "request": {
    "url": "https://api.example.com/v1/orders/{item.order_id.value}",
    "method": "PATCH",
    "content_type": "json",
    "iterate_over": "field.line_items",
    "content": {"quantity": "{item.item_qty.value}"},
    "headers": {"X-Line-Index": "{sequence}", "Authorization": "Bearer {token}"}
  }
}
```

> **This changed, and hooks do not auto-upgrade.** Earlier engine versions filled `url` and `headers`
> **once**, with a context that did not contain the item — a per-item placeholder in the URL path raised
> `ResolveVarError` and the whole `call_api` entry failed. If you are reading an older config that
> hoisted per-item values into the body and used a static collection URL, that workaround is no longer
> necessary *on a current build*. Before relying on this, confirm the hook actually runs a build that
> supports it — see [the engine version is frozen at creation](#hook-level-the-engine-version-is-frozen-at-creation).

Two consequences worth knowing:

- Values are substituted into the URL by plain string interpolation, with **no percent-encoding**. An
  item value containing `?`, `#`, or `/` will change the URL's structure rather than being escaped.
  Only put values you trust the shape of into a URL path.
- A `.@` fetch expression in `url` or `headers` is re-evaluated **per item**, so it fires N times. Keep
  `.@` in `content`, or resolve it once in a `get_content` stage and reference the `property`.

### What `item` actually is

`iterate_over` accepts more than a plain list — the engine normalises the resolved value: a list is
used as-is; any other sequence (which is what txscript multivalue fields are) is expanded
element-wise; a single object is wrapped as a one-item list; `None` or empty logs a warning and skips
the call entirely. What each `item` *is* depends on what you point at, and getting this wrong is the
most common iteration error:

| `iterate_over` | Each `item` is | Access a value with |
|----------------|----------------|---------------------|
| A **table** (multivalue of tuples), e.g. `field.line_items` | the row/tuple | `{item.<column_schema_id>.value}` |
| A **simple multivalue** (children are datapoints), e.g. `field.tags` | the datapoint itself | `{item.value}` |
| A **child column**, e.g. `field.tag` | — | **must** append `.all_values`: `field.tag.all_values`, then `{item.value}` |
| A list of dicts from `property` / `payload` | the dict | `{item.<key>}` |

**Table (multivalue of tuples)** — `item` is the row, so you name the column:

```json
{
  "iterate_over": "field.line_items",
  "content": {"sku": "{item.item_sku.value}", "qty": "{item.item_qty.value}"}
}
```

`{item.value}` on a table row raises `ResolveVarError` — a row has no single value.

**Simple multivalue** — `item` is already the datapoint, so there is no column to name:

```json
{
  "url": "https://api.example.com/v1/tags/{item.value}",
  "iterate_over": "field.tags"
}
```

`{item.<child_schema_id>.value}` here — e.g. `{item.tag.value}` when the multivalue is `tags` and its
child datapoint is `tag` — **raises** `ResolveVarError` (an `AttributeError` on the child's schema id).
This is the mirror image of the table case and the two are easy to confuse: the container's schema id
is what you iterate, and for a simple multivalue the child's schema id must **not** appear in the
placeholder. A bare `{item}` also works and stringifies the value.

**Child column** — pointing `iterate_over` at the column inside a multivalue (`field.tag` rather than
`field.tags`) fails with `TypeError: The multivalue field value is not a list by itself, use the
.all_values property to access the list`, which aborts the whole `call_api` entry. Add `.all_values`
(`field.tag.all_values`) and each `item` is a datapoint, accessed as `{item.value}`.

### Multiple values to a single field

If more than one response handler write lands on the same `schema_id` **during a run**, the engine
collects them and writes a **list** at the end instead of letting the last one win:

| Writes to one `target_key` | Value handed to the field |
|----------------------------|---------------------------|
| 1 | the scalar, e.g. `"ID1"` |
| 3 | the Python list `["ID1", "ID2", "ID3"]`, in request order |

This is what makes "POST each line item, keep every returned id" a two-line config. Three caveats, and
the first one bites hardest:

- **The list is coerced by the target field's schema type — you do not get a list back.** The engine
  hands a Python list to a datapoint, and the datapoint stringifies it:

  | Target field type | Stored / read back as |
  |-------------------|------------------------|
  | `string` | the Python **repr**: `"['ID1', 'ID2', 'ID3']"` — single quotes, not JSON, not parseable by a JSON reader |
  | `number` | `.value` is the same repr string, but reading the field back gives **`None`** — effectively corrupt |

  So this is only safe for a **string** field you intend to read as opaque text. If anything downstream
  (a formula, a rule, an export template) has to parse the individual ids, do **not** rely on the
  collapse — fan out with `target_key: "..._{sequence}"`, or capture into `property` and build the
  payload yourself.

- **It is per-run, not per-iteration.** Two *different* stages whose handlers target the same
  `schema_id` also produce a list (`["A", "B"]`) — even with no `iterate_over` anywhere. If you want
  the second stage to overwrite the first, use different fields, or route one through `property`.
- The count depends on how many requests actually reached a handler, so a document with one line item
  yields a scalar `"ID1"` and a document with two yields the repr `"['ID1', 'ID2']"`. Anything reading
  that field downstream must tolerate both shapes. Use `target_key: "..._{sequence}"` instead when you
  need a stable shape.

→ Drop-in blueprint: `export-iterate-line-items` (blueprints/export/).

### Iteration Over Document Relations

Upload each related document:

```json
{
  "get_content": [
    {
      "name": "additional_attachments",
      "query": {"key": {"$regex": "^attachment_\\d+$"}},
      "source": "document_relation_content"
    }
  ],
  "call_api": [
    {
      "name": "upload_attachments",
      "request": {
        "url": "https://api.example.com/attachments",
        "method": "POST",
        "iterate_over": "property.additional_attachments",
        "iteration_item_name": "item",
        "content_type": "multipart",
        "content": {
          "file": ["{item.original_file_name}", "{item.content.@}"],
          "metadata": "{\"source\": \"rossum\"}"
        }
      }
    }
  ]
}
```

### Store Response as Document Relation

```json
{
  "target_type": "document_relation",
  "target_key": "api_response"
}
```

Creates a Rossum document named `{target_key}_response`, stores response body as content (`text/plain`), creates/updates relation with key. If relation with key already exists, replaces old document.

Access later:
```json
{
  "name": "previous_response",
  "source": "document_relation_content",
  "query": {"key": {"$eq": "api_response"}}
}
```

### Tolerating Timeouts (`ignore_timeout`)

Set `ignore_timeout: true` on a **request** to make a timeout non-fatal:

```json
{
  "name": "optional_enrichment",
  "request": {
    "url": "https://api.example.com/v1/enrich/{field.doc_id.value}",
    "method": "GET",
    "timeout": 5,
    "ignore_timeout": true
  }
}
```

The engine catches the timeout, logs a warning, and moves on without setting the error flag — so the
export is not blocked by a best-effort call.

Scope, precisely:

- **Timeouts only.** Both connect and read timeouts are suppressed; a plain connection error, DNS
  failure or TLS error is *not* — those still record a pipeline error.
- **Request only.** A timeout while fetching an `auth` token is always reported. `ignore_timeout` also
  does not extend to the 401-retry's *own* failures beyond its timeout.
- **Per item.** During `iterate_over` only the timed-out item is skipped; remaining items still run.
- No response handler runs for a skipped request, so any field it would have written stays empty —
  guard downstream logic on the field being empty rather than assuming the call happened.

---

## Failure Semantics

Worth being explicit, because most of it is silent:

| Situation | Engine behaviour | Pipeline error? |
|-----------|------------------|-----------------|
| **Non-2xx response** (400/404/500…) | Response handlers run **normally** on the error response | **No** |
| Request transport failure (connection error, non-ignored timeout) | logged, that request skipped, loop continues to the next item | Yes |
| Timeout with `ignore_timeout: true` | logged as a warning, that request skipped | No |
| Unresolvable placeholder in `auth` or `request` | `ResolveVarError`, the whole `call_api` entry is abandoned | Yes |
| `iterate_over` resolves to `None` / empty | warning, the `call_api` entry is skipped | No |
| Exception inside a response handler (e.g. unknown `schema_id`) | logged with the full response | Yes |
| `evaluate` condition false | stage skipped | No |
| **Exception in `get_content` or in evaluating a condition** | **propagates out — the hook fails hard** | **No message at all** |

**Non-2xx responses are recorded, never raised.** A `500` with body `{"error": "boom"}` will happily
populate your `status_code` and `error` fields and finish the run clean. Nothing in the engine inspects
`response.ok` on your behalf — if a failed export must block the document, you have to make it do so:
capture the status code into a field and gate the next stage (`evaluate`) on it, and/or add a Rule that
fires on the failure field.

When any of the "Yes" rows occur, one error message is appended to the annotation at the end of the
run — verbatim:

```
Some exception occurred during during export pipeline - request processor run. Check log for details.
```

*(The duplicated "during" is in the source.)* It carries no indication of which stage, call, or field
failed — the hook log is the only diagnostic. Set `"debugging": true` to have every response logged
with status and body.

**Only the `call_api` phase is protected.** Inside it errors are contained: a failed request skips to
the next item, a failed `call_api` entry skips to the next entry, and later stages still execute. That
means a pipeline can partially succeed — e.g. create a remote record but fail to attach the scan — so
put ordering-sensitive calls behind `evaluate` conditions on the previous stage's captured status code.

The `evaluate` and `get_content` phases have **no such protection** — neither is wrapped in a
`try`/`except`. An exception there (a relations lookup that errors, a malformed `$regex` in a query, a
`$size` against something unmeasurable) propagates straight out of the handler: the hook fails at the
platform level, **no stages after it run, and no error message is attached to the annotation** — not
even the generic one above. The symptom is a hook that appears to have done nothing, with the traceback
visible only in the hook log. This is the one failure mode that can leave an export silently
un-attempted, so keep `get_content` queries simple and validate them before relying on them.

---

## Common Patterns

### Pattern: Create, Upload Scan, Submit

```json
{
  "settings": {
    "stages": [
      {
        "get_content": [
          {"name": "invoice_payload", "source": "document_relation_content", "query": {"key": {"$eq": "create_draft"}}}
        ],
        "call_api": [{
          "name": "create_draft",
          "auth": { /* OAuth config */ },
          "request": {
            "url": "{field.api_base_url.value}/invoices",
            "method": "POST",
            "content": "{property.invoice_payload.content.@}",
            "content_type": "json",
            "headers": {"Authorization": "Bearer {token}", "Accept": "application/json"}
          },
          "response_handlers": [
            {"target_type": "schema_id", "target_key": "api_status_code", "value": {"response": {"$jmespath": "status_code"}}},
            {"target_type": "schema_id", "target_key": "invoice_id", "value": {"response.body": {"$jmespath": "id"}}}
          ]
        }]
      },
      {
        "evaluate": [
          {"name": "created_ok", "condition": {"field.api_status_code": {"$in": ["200", "201"]}}}
        ],
        "call_api": [{
          "name": "upload_scan",
          "auth": { /* same OAuth */ },
          "request": {
            "url": "{field.api_base_url.value}/invoices/{field.invoice_id.value}/image_scan",
            "method": "PUT",
            "content_type": "files",
            "content": {"file": ["{payload.document.original_file_name}", "{payload.document.content.@}"]},
            "headers": {"Authorization": "Bearer {token}"}
          }
        }]
      },
      {
        "evaluate": [
          {"name": "created_ok", "condition": {"field.api_status_code": {"$in": ["200", "201"]}}}
        ],
        "call_api": [{
          "name": "submit",
          "auth": { /* same OAuth */ },
          "request": {
            "url": "{field.api_base_url.value}/invoices/{field.invoice_id.value}/submit",
            "method": "PUT",
            "headers": {"Authorization": "Bearer {token}"}
          }
        }]
      }
    ]
  }
}
```

→ Drop-in blueprint: `export-create-upload-submit` (blueprints/export/).

### Pattern: Fetch Related Data and Validate

```json
{
  "settings": {
    "stages": [
      {
        "get_content": [
          {"name": "purchase_order", "source": "document_relation_content", "query": {"key": {"$eq": "po_data"}}}
        ],
        "call_api": [{
          "name": "validate_against_po",
          "request": {
            "url": "https://api.example.com/validate",
            "method": "POST",
            "content_type": "json",
            "content": {
              "invoice_number": "{field.invoice_number.value}",
              "po_number": "{property.purchase_order.po_number}",
              "amount": "{field.total_amount.value}"
            }
          },
          "response_handlers": [
            {"target_type": "schema_id", "target_key": "validation_result", "value": {"response.body": {"$jmespath": "valid"}}}
          ]
        }]
      }
    ]
  }
}
```

### Pattern: Upload Email EML

```json
{
  "evaluate": [
    {"name": "email_exists", "condition": {"payload.document.email": {"$exists": true, "$ne": ""}}}
  ],
  "get_content": [
    {"name": "email_content_url", "source": "explicit", "query": ["{payload.document.email}/content"]}
  ],
  "call_api": [{
    "name": "attach_email_eml",
    "request": {
      "url": "https://api.example.com/attachments",
      "method": "POST",
      "content_type": "files",
      "content": {
        "file": [
          "annotation_{payload.annotation.id}_email.eml",
          "{property.email_content_url.@}",
          "message/rfc822"
        ]
      }
    }
  }]
}
```

### Pattern: Pass Data Between Stages via Property

```json
{
  "settings": {
    "stages": [
      {
        "call_api": [{
          "name": "get_vendor_info",
          "request": {"url": "https://api.example.com/vendors/{field.vendor_id.value}", "method": "GET"},
          "response_handlers": [
            {"target_type": "property", "target_key": "vendor_data", "value": {"response.body": {"$jmespath": "@"}}}
          ]
        }]
      },
      {
        "call_api": [{
          "name": "create_invoice_with_vendor",
          "request": {
            "url": "https://api.example.com/invoices",
            "method": "POST",
            "content_type": "json",
            "content": {
              "vendor_name": "{property.vendor_data.name}",
              "vendor_code": "{property.vendor_data.code}",
              "amount": "{field.total_amount.value}"
            }
          }
        }]
      }
    ]
  }
}
```

### Pattern: Error Handling

```json
"response_handlers": [
  {
    "condition": {"response.ok": {"$eq": true}},
    "target_type": "schema_id",
    "target_key": "export_status",
    "value": {"response.body": {"$jmespath": "status"}}
  },
  {
    "condition": {"response.ok": {"$eq": false}},
    "target_type": "schema_id",
    "target_key": "export_error",
    "value": {"response.body": {"$jmespath": "error"}}
  }
]
```

---

## SFTP Export Pattern

Export files to SFTP using Rossum's `file-storage-export` service:

```json
{
  "settings": {
    "stages": [
      {
        "call_api": [
          {
            "name": "push_to_sftp",
            "request": {
              "url": "{payload.base_url}/svc/file-storage-export/api/v1/direct_export",
              "method": "POST",
              "headers": {
                "Authorization": "Bearer {payload.rossum_authorization_token}"
              },
              "content": {
                "request_id": "{payload.request_id}",
                "timestamp": "{payload.timestamp}",
                "hook": "{payload.hook}",
                "action": "manual",
                "event": "invocation",
                "base_url": "{payload.base_url}",
                "settings": {
                  "credentials": {
                    "host": "your-sftp-server.example.com",
                    "port": 22,
                    "username": "sftp_user",
                    "type": "sftp",
                    "sftp_version": 3
                  },
                  "export_rule": {
                    "path_to_directory": "/upload",
                    "filename_collision": {
                      "replace": true
                    }
                  }
                },
                "secrets": {
                  "password": "{payload.secrets.password}",
                  "ssh_key": "{payload.secrets.ssh_key}",
                  "type": "sftp"
                },
                "payload": "{base64(payload)}",
                "filename": "invoice_{field.document_id.value}"
              },
              "content_type": "json"
            },
            "response_handlers": [
              {
                "value": {"response": {"$jmespath": "status_code"}},
                "target_key": "sftp_status_code",
                "target_type": "schema_id"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

**Key points:**
- **Static fields** (`action`, `event`, `request_id`, `timestamp`, `hook`, `base_url`) must be passed through from the payload exactly as shown
- **Secrets** (`password` and/or `ssh_key`) are stored in the hook's secrets configuration
- **base64 encoding** via `{base64(payload)}` is required by the file-storage-export service
- **Credentials**: `host`, `port`, `username`, `type` ("sftp"), and `sftp_version` in the `credentials` object
- **Export rule**: `path_to_directory` sets the target path, `filename_collision.replace` controls overwrite behavior
- **Filename**: custom filename template (without extension — extension comes from the document)

→ Drop-in blueprint: `export-sftp-via-file-storage` (blueprints/export/).

---

## Complete Examples

### Real-World: Coupa Integration (5-stage)

A production configuration that creates a Coupa invoice, uploads scans, attaches URL, uploads related documents, and attaches email:

> **Reproduced as deployed, including one habit worth not copying.** Every field reference below is
> written bare (`{field.coupa_invoice_id}`, `{field.oauth_url}`) rather than with `.value`. That works
> here only because those fields are string-typed — the bare form resolves to a typed proxy, so if any
> of them were ever changed to a number field the `evaluate` conditions comparing against `"200"` /
> `""` would silently stop matching and the whole chain would go quiet. In new configs write
> `{field.x.value}`; see [`{field.x.value}` vs `{field.x}`](#fieldxvalue-vs-fieldx).

```json
{
  "stages": [
    {
      "get_content": [
        {"name": "create_draft", "source": "document_relation_content", "query": {"key": {"$eq": "create_draft"}}}
      ],
      "call_api": [{
        "name": "create_draft",
        "auth": {
          "url": "{field.oauth_url}",
          "method": "POST",
          "content_type": "form",
          "content": {
            "scope": "core.invoice.create core.invoice.read core.invoice.write",
            "client_id": "{field.oauth_client_id}",
            "grant_type": "client_credentials",
            "client_secret": "{payload.secrets.client_secret}"
          },
          "credential_key": "access_token"
        },
        "request": {
          "url": "{field.create_draft_url}",
          "method": "POST",
          "content": "{property.create_draft.content.@}",
          "content_type": "json",
          "headers": {"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer {token}"}
        },
        "response_handlers": [
          {"target_type": "schema_id", "target_key": "api1_status_code", "value": {"response": {"$jmespath": "status_code"}}},
          {"target_type": "schema_id", "target_key": "coupa_invoice_id", "value": {"response": {"$jmespath": "body.id"}}},
          {"target_type": "schema_id", "target_key": "api1_response_body", "value": {"response": {"$jmespath": "body"}}}
        ]
      }]
    },
    {
      "evaluate": [
        {"name": "api1_ok", "condition": {"field.api1_status_code": {"$in": ["200", "201"]}}},
        {"name": "id_exists", "condition": {"field.coupa_invoice_id": {"$exists": true, "$ne": ""}}}
      ],
      "call_api": [{
        "name": "attach_image_scan",
        "auth": { "...same OAuth..." : "..." },
        "request": {
          "url": "{field.coupa_api_base_url}api/invoices/{field.coupa_invoice_id}/image_scan",
          "method": "PUT",
          "content_type": "files",
          "content": {"file": ["{payload.document.original_file_name}", "{payload.document.content.@}"]},
          "headers": {"Accept": "application/json", "Authorization": "Bearer {token}"}
        }
      }]
    },
    {
      "evaluate": [ "...same conditions..." ],
      "call_api": [{
        "name": "attach_rossum_url",
        "auth": { "...same OAuth..." : "..." },
        "request": {
          "url": "{field.coupa_api_base_url}api/invoices/{field.coupa_invoice_id}/attachments",
          "method": "POST",
          "content": {"attachment[url]": "{field.rossum_annotation_link}"},
          "content_type": "form",
          "headers": {"Accept": "application/json", "Authorization": "Bearer {token}"}
        }
      }]
    },
    {
      "evaluate": [ "...same conditions..." ],
      "get_content": [
        {"name": "additional_attachments", "source": "document_relation_content", "query": {"key": {"$regex": "^attachment_email_attachments_\\d{8,10}(?:_\\d+)?$"}}}
      ],
      "call_api": [{
        "name": "upload_related_documents",
        "auth": { "...same OAuth..." : "..." },
        "request": {
          "url": "{field.coupa_api_base_url}api/invoices/{field.coupa_invoice_id}/attachments",
          "method": "POST",
          "iterate_over": "property.additional_attachments",
          "iteration_item_name": "item",
          "content_type": "multipart",
          "content": {
            "attachment[file]": ["{item.original_file_name}", "{item.content.@}"],
            "attachment[intent]": "Supplier"
          },
          "headers": {"Accept": "application/json", "Authorization": "Bearer {token}"}
        }
      }]
    },
    {
      "evaluate": [
        {"name": "email_exists", "condition": {"payload.document.email": {"$exists": true, "$ne": ""}}}
      ],
      "get_content": [
        {"name": "email_content_url", "source": "explicit", "query": ["{payload.document.email}/content"]}
      ],
      "call_api": [{
        "name": "attach_email_eml",
        "auth": { "...same OAuth..." : "..." },
        "request": {
          "url": "{field.coupa_api_base_url}api/invoices/{field.coupa_invoice_id}/attachments",
          "method": "POST",
          "content_type": "files",
          "content": {
            "attachment[file]": ["annotation_{payload.annotation.id}_email_file.eml", "{property.email_content_url.@}", "message/rfc822"],
            "attachment[intent]": "Email"
          },
          "headers": {"Accept": "application/json", "Authorization": "Bearer {token}"}
        }
      }]
    }
  ]
}
```

### Minimal: Simple GET and Store

```json
{
  "settings": {
    "stages": [
      {
        "call_api": [{
          "name": "get_vendor_status",
          "request": {
            "url": "https://api.example.com/vendors/{field.vendor_id.value}",
            "method": "GET"
          },
          "response_handlers": [
            {"target_type": "schema_id", "target_key": "vendor_status", "value": {"response.body": {"$jmespath": "status"}}}
          ]
        }]
      }
    ]
  }
}
```

---

## Migration from Pipeline v1

### Key Differences

1. **Single hook** — Request Processor runs as one hook, not a chain of sequential hooks
2. **Formula fields don't work mid-process** — formulas evaluate only after the hook completes. Cannot set a field in stage 1 and use a formula depending on it in stage 2
3. **Use property and templating instead** — store intermediate values via `property` target type, reference with `{property.key}`. Template expressions (`{field.base_url}{field.id}/submit`) replace formula field concatenations

### Migration Example

**Pipeline v1 (multiple hooks):**
```
Hook 1: Create draft → Store invoice_id → Formula calculates upload_url
Hook 2: Use upload_url to upload scan
Hook 3: Submit invoice
```

**Request Processor (single hook):**
```json
{
  "stages": [
    {
      "call_api": [{
        "response_handlers": [
          {"target_type": "schema_id", "target_key": "invoice_id", "value": {"response.body": {"$jmespath": "id"}}},
          {"target_type": "property", "target_key": "calculated_url", "value": {"response.body": {"$jmespath": "..."}}}
        ]
      }]
    },
    {
      "call_api": [{
        "request": {
          "url": "{field.api_base_url.value}/invoices/{field.invoice_id.value}/scan",
          "content": {"derived": "{property.calculated_url}"}
        }
      }]
    }
  ]
}
```

---

## Field Reference

### EvaluatePhase Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | Yes | Descriptive name for logging |
| `condition` | Object | Yes | MongoDB-style filter query |

### GetContentPhase Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | Yes | Key to store result in `property` |
| `source` | String | Yes | `relation`, `relation_content`, `document_relation`, `document_relation_content`, `parent_relation`, `parent_relation_content`, or `explicit` |
| `query` | Object/List | Yes | Filter query (relations) or template list (explicit) |

### Auth Object — `bearer` (default)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | String | No | `bearer` when omitted |
| `url` | String | Yes | Auth endpoint URL; also derives the token cache key |
| `method` | String | Yes | HTTP method |
| `content_type` | String | No | `json` → `json=`, anything else → `data=` |
| `content` | Object | No | Auth request body |
| `headers` | Object | No | Custom headers |
| `params` | Object | No | Query parameters |
| `credential_key` | String | Yes | Dot-path to token in response |

For `type: "oauth1"` and `type: "oauth2_private_certificate"` fields, see [Authentication](#authentication).

### Requester Object

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | String | Yes | — | API endpoint URL |
| `method` | String | Yes | — | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `content` | Any | No | `null` | Request body |
| `content_type` | String | No | `null` | `json`, `form`, `files`, `multipart`; omitted → raw `data=` |
| `headers` | Object | No | `null` | Custom headers |
| `params` | Object | No | `null` | Query-string parameters |
| `timeout` | Integer | No | `10` | Per-request timeout, seconds |
| `ignore_timeout` | Boolean | No | `false` | Skip instead of erroring on timeout |
| `iterate_over` | String | No | `null` | Path to list for iteration |
| `iteration_item_name` | String | No | `item` | Item variable name |

### ResponseHandler Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `condition` | Object | No | Filter query for conditional execution |
| `target_type` | String | Yes | `schema_id`, `property`, or `document_relation` |
| `target_key` | String | Yes | Target field/key name |
| `value` | ValueConfig | No* | Value extraction config |

### ValueConfig Object

Format: `{"context_path": {"$operator": "query"}}`

- `context_path`: any path into the response object — `response`, `response.body`, `response.headers`, `response.headers_raw`, `response.text`, `response.status_code`, …
- Operator: exactly one of `$jmespath`, `$xmlpath`, `$regex` (zero or two is a config error)
- The shorthand dict must have exactly one key; extra keys are rejected

---

## Re-testing an Export

Iterating on a pipeline means running the same annotation through the export leg repeatedly. You do **not** need to re-upload the document or create a fresh annotation each time — and you cannot re-confirm one that has already exported, because confirmation is a one-time transition.

Patch the status back to `exporting`:

```bash
curl -X PATCH 'https://<your-org>.rossum.app/api/v1/annotations/<annotation_id>' \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"status": "exporting"}'
```

Entering `exporting` is what triggers the export leg, so this single PATCH is the whole re-test — no review lock, no content edit, no re-upload. The edit-and-retest loop becomes:

1. Change the hook's `settings` (the pipeline config) or the export template.
2. PATCH the annotation to `exporting`.
3. Read the hook log for that annotation to see the rendered request and the target's response.

Notes:

- A successful run returns the annotation to `exported`; a failure leaves it in `failed_export`. Either is a normal loop state — re-patch to run again.
- An annotation that stays in `exporting` means the target never reported back. Read the log rather than re-patching: a second PATCH while the first run is in flight can double-deliver.
- This re-sends to the real target, so **sandbox/UAT only**. Against a production connector it re-delivers a real document downstream.
- Only the export leg re-runs. `annotation_content.confirmed` hooks, approval-workflow routing, and validation rules are **not** re-evaluated — if the bug is upstream of the export, re-firing this way will keep reproducing the same input.

The equivalent MCP call is `rossum_patch_annotation(annotation_id=<id>, status="exporting")`. See the `iterate` skill for the full set of re-fire patterns and when each one applies.

---

## Troubleshooting

### URL Not Fetching
**Problem:** URL returns as string instead of fetched content.
**Fix:** Add `.@` to explicitly fetch: `{payload.document.content.@}`

### Token Not Caching
**Problem:** New token requested every time.
**Fix:** The cache key is a hash of the **resolved** auth config, so any real difference (a scope, an
extra header, a different URL) mints a separate entry — make the auth blocks byte-identical across
calls. If they already are, check the log for `Failed to update secrets on API`: persisting the token
is a `PATCH /hooks/{id}` and it needs `token_owner` set and the hook's `secrets_schema` open to extra
string keys (see [Authentication](#authentication)). Without persistence the token is cached only
within a single run.

### Handler Not Running
**Problem:** Response handler doesn't execute.
**Fix:** Check handler `condition`. Debug by saving full response: `{"response": {"$jmespath": "@"}}`

### JMESPath Returns None
**Problem:** Query returns null.
**Fix:** Start with `@` (full body), then build path incrementally: `data` → `data.items` → `data.items[0].id`

### Formula Fields Not Evaluating Between Stages
**Problem:** Formula depends on field set in previous stage but returns old value.
**Fix:** This is by design — single hook, formulas evaluate only after completion. Use `property` target type and `{property.*}` references instead. Or use template expressions directly in URL/content: `"{field.base_url.value}/invoices/{field.invoice_id.value}"`.

### Status Code Comparison Fails
**Problem:** Evaluate condition on status code doesn't match.
**Fix:** The handler writes the status code as an **int**, but you read it back through the schema
field, so what you compare against depends on the field's type *and* on whether you wrote `.value`:

| Condition path | String-typed field | Number-typed field |
|----------------|--------------------|--------------------|
| `field.sc` | matches `"200"` / `["200","201"]` | matches `200` / `[200,201]` |
| `field.sc.value` | matches `"200"` | matches `"200"` |

`{field.x.value}` is always the raw string, whatever the schema type — so **use `.value` and compare
against strings** and the condition works either way:

```json
{"field.api1_status_code.value": {"$in": ["200", "201"]}}
```

Referencing the field without `.value` (as several examples in this doc do) works only while the field
stays string-typed; switching it to a number field silently breaks every such condition.

### Per-item Value in the URL Raises `ResolveVarError`
**Problem:** `iterate_over` is set and the URL contains `{item...}`, but the call fails to resolve.
**Fix:** On the current engine the item resolves in `url`, `headers`, `params` and `content`, so this
should work — the usual cause is the **wrong item shape**. Iterating a simple multivalue gives
datapoints (`{item.value}`), iterating a table gives rows (`{item.<column>.value}`); using one form
against the other raises. See [What `item` actually is](#what-item-actually-is). If it genuinely cannot
resolve in the URL at all, the hook is running an older engine build — re-check the Store template
version.

### Pre-built JSON Body Arrives Double-Encoded
**Problem:** The target API reports the body is a string, not an object/array.
**Fix:** You set `content_type: "json"` on a body that was already serialized to a string, so it was
serialized twice. Omit `content_type` and set `Content-Type` in `headers` instead — see
[Raw bodies](#raw-bodies-omit-content_type).

### Field Sometimes Holds a List, Sometimes a Scalar
**Problem:** Downstream logic breaks on some documents.
**Fix:** Repeated response-handler writes to one `schema_id` collapse into a list, so the shape follows
the number of requests that ran. Either tolerate both, or write to `target_key: "..._{sequence}"` for a
fixed shape. Note this also triggers across *different stages* targeting the same field, with no
iteration involved.

### Export Reports an Error but Every Field Looks Right
**Problem:** The generic `Some exception occurred during during export pipeline` message appears even
though the data was written.
**Fix:** The flag is set by *any* failure anywhere in the run, and the run continues afterwards — so a
later optional call (an attachment upload, a second handler) failed while the main call succeeded. The
hook log is the only place that says which. Also note the converse: a non-2xx response is **not** an
error to the engine, so a failed export can finish with no message at all — see
[Failure Semantics](#failure-semantics).

### Connect Timeouts to External Hosts
**Problem:** Every call to a non-Rossum host hangs and times out at the TCP layer. No HTTP status. No body. Identical curl from your laptop succeeds.
**Fix:** External HTTPS egress may be disabled at the org level. Contact Rossum support to enable it, then redeploy affected hooks. See [Prerequisites](#prerequisites).

## Best Practices

1. **Start simple** — begin with one stage and one API call, add complexity gradually
2. **Use descriptive names** — `"create_invoice_in_erp"` not `"api1"`
3. **Validate before calling** — use `evaluate` to check required fields before API calls
4. **Store intermediate results** — use `property` to pass data between stages
5. **Debug with full responses** — `{"response": {"$jmespath": "@"}}` shows status, headers, body
6. **Keep auth config identical** — reuse the same auth object for token caching to work
7. **Secrets in hook secrets** — never hardcode credentials, use `{payload.secrets.*}`
8. **Always write `.value`** — `{field.x.value}` is the raw string regardless of schema type; `{field.x}` is a typed proxy whose comparisons change if the field's type changes
9. **Gate on the previous stage's status code** — non-2xx responses do not stop the pipeline, so a later stage will happily run against a record that was never created
10. **Raise `timeout` for slow systems** — the default is 10 seconds
11. **Iterate, don't index** — `get_content` unwraps a single match into a bare object, so `[0]` breaks on one-item documents while `iterate_over` handles both
