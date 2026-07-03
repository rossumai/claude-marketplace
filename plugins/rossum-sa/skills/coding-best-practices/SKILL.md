---
name: coding-best-practices
description: Use when reviewing, auditing, or writing Rossum serverless hook functions (custom Python extensions) for code quality, security, and correctness
---

# Coding Best Practices — Rossum Serverless Functions

## Overview

Audit custom Python hook code against Rossum-specific and general best practices. Rossum serverless functions run as stateless, event-driven Python 3.12 compute (AWS Lambda style). Issues here cause silent failures, data corruption, or security incidents.

---

## Step 1: Find Custom Hook Files

In a prd project, hook `.py` files live alongside their `.json` counterparts in `<org>/<env>/hooks/`. Only hooks that have a `.py` file contain custom code — the rest are no-code Rossum extensions.

```bash
find . -name "*.py" -path "*/hooks/*"
```

---

## Step 2: Evaluate Each Hook

Work through every checklist item. Flag violations in the output format at the end.

### 2.1 — Consider Whether Custom Code Is Needed

Before reviewing code quality, ask: could this hook be replaced by a no-code alternative?
- **Rossum Store** — pre-built extensions (MDH matching, export pipelines, SFI)
- **Formula Fields** — simple field transformations and calculations
- **Export Pipeline** — routing, filtering, payload construction

Flag hooks where the entire logic could be replaced by a no-code extension.

---

### 2.2 — Security (Critical)

**No hardcoded credentials**
Passwords, API keys, client secrets must never appear in code. Use `payload["secrets"]` and define `secrets_schema` in the hook JSON (outside a prd2 tree, set it via the `rossum_create_hook` / `rossum_patch_hook` MCP tools — key names only, a human enters the values):
```json
// hook.json — secrets_schema
{
  "type": "object",
  "properties": {
    "username": { "type": "string", "minLength": 1, "description": "Service account login" },
    "password": { "type": "string", "minLength": 1, "description": "Service account password" }
  },
  "additionalProperties": false
}
```

The API enforces exactly this shape: top-level keys other than `type`/`properties`/`additionalProperties` are rejected (no `$schema`, no `required`), every property must be type `string`, and `additionalProperties` must be present and literally `false` — HTTP 400 otherwise.
```python
# Bad
auth=("api_user", "Hardcoded123")

# Good
auth=(payload["secrets"]["username"], payload["secrets"]["password"])
```

**No hardcoded URLs, queue IDs, or environment-specific values in code**
Move to `payload["settings"]`:
```python
# Bad
API_URL = "https://prod.api.example.com/endpoint"
QUEUE_ID = 2561561

# Good
api_url = payload["settings"]["api_url"]
queue_id = payload["settings"]["queue_id"]
```

---

### 2.3 — Entry Point Structure

**Event/action validation at the top**
Guard against the hook firing on unintended events. Return early immediately:
```python
def rossum_hook_request_handler(payload: dict) -> dict:
    if payload["event"] != "annotation_status" or payload["annotation"]["status"] != "confirmed":
        return {}
    ...
```

**Entry point wraps business logic in try-except**
The entry point should catch unhandled exceptions and return a user-visible error message rather than crashing silently:
```python
def rossum_hook_request_handler(payload: dict) -> dict:
    messages, operations = [], []
    try:
        messages, operations = main(payload)
    except Exception as e:
        print(f"Raised exception: {e}")
        messages = [{"type": "error", "content": f"Hook failed: {e}"}]
    return {"messages": messages, "operations": operations}
```

---

### 2.4 — TxScript Usage

Use `TxScript` whenever reading or writing annotation fields or returning hook responses. Do not traverse the raw payload content tree manually for field access.

```python
# Bad
field_value = next(
    dp["content"]["value"] for dp in payload["annotation"]["content"]
    if dp["schema_id"] == "document_id"
)

# Good
from txscript import TxScript
t = TxScript.from_payload(payload)
field_value = t.field.document_id
```

Always return `t.hook_response()` from hooks that use TxScript (not a bare `{}`).

---

### 2.5 — Error Handling

**Catch specific exceptions — not bare `Exception`**
```python
# Bad
except Exception as e:
    print(f"Error: {e}")

# Good
except ValueError as e:
    print(f"ValueError: {e}")
    raise
except KeyError as e:
    print(f"Missing key: {e}")
    raise
```
Exception: the entry-point catch-all (2.3) is the one place a broad `except Exception` is intentional.

**Always re-raise after logging** unless you have a valid fallback value.

**`next()` must always have a default**
```python
# Bad — raises StopIteration, making the following None-check dead code
result = next(item for item in data if item["key"] == "x")
if not result: ...

# Good
result = next((item for item in data if item["key"] == "x"), None)
if result is None: ...
```

**`raise_for_status()` after every HTTP call**
Every `requests.get/post/patch/...` must be followed immediately by `.raise_for_status()` before calling `.json()` or accessing `.content`.

**Use `None` as sentinel, not `""`**
```python
# Bad
result = next((...), "")
if not result: return None   # works by accident, misleading

# Good
result = next((...), None)
if result is None: return None
```

---

### 2.6 — Code Quality

**No unused imports** — remove any `import` not referenced in the file.

**Type annotations on function signatures**
```python
# Bad
def get_document(payload):

# Good
def get_document(payload: dict) -> dict | None:
```
Annotate at function boundaries; avoid annotating every local variable.

**Extract repeated patterns to helpers**
Auth headers built more than once → extract to a function:
```python
def _auth_headers(payload: dict) -> dict:
    return {"Authorization": f"Bearer {payload["rossum_authorization_token"]}"}
```

**Meaningful names, English only** — no one-letter variables outside list comprehensions.

**Modular functions with single responsibilities** — if a function does more than one thing, split it.

---

### 2.7 — Logging

**Log at key entry points with context** — include annotation ID, status, or the value being processed.

**Never log credentials or PII.**

**Accumulate loop outputs, log once** — avoid thousands of individual `print()` calls inside loops.

**Label all print statements** — `print(url)` is noise; `print(f"Fetching document: {url}")` is useful.

---

### 2.8 — API Rate Limiting (FUP)

**The Rossum API Fair Use Policy recommends staying under 2 requests/second.** Any hook that calls the Rossum API inside a loop — or fires concurrently across many annotations — is a FUP risk and **must be flagged**.

**How to identify the risk:**
- Look for `requests.*`, `client.*`, or `ElisAPIClientSync` calls inside `for`/`while` loops
- Consider the realistic collection size: line items (can be 100+), annotation lists, email attachments, MDH results
- Calculate worst-case req/s: `calls_per_iteration × estimated_items ÷ expected_duration_s`
- Also flag `asyncio.gather()` or `concurrent.futures` without a semaphore — these fire all requests simultaneously

**Required fix — add `time.sleep()` between iterations:**
```python
import time

RATE_LIMIT_DELAY = 0.5  # max 2 req/s

for annotation_id in annotation_ids:
    start_annotation(annotation_id, headers)
    time.sleep(RATE_LIMIT_DELAY)
    validate_annotation(annotation_id, headers)
    time.sleep(RATE_LIMIT_DELAY)
```

**Better fix — use batch endpoints where available:**
```python
# Bad — N individual PATCH calls
for annotation_id in annotation_ids:
    requests.patch(f"{base_url}/annotations/{annotation_id}", ...)

# Good — single bulk operation (where API supports it)
requests.post(f"{base_url}/annotations/bulk_update", json={"ids": annotation_ids, ...})
```

**For async code — limit concurrency with a semaphore:**
```python
import asyncio

sem = asyncio.Semaphore(2)  # max 2 concurrent requests

async def fetch_with_limit(client, url, headers):
    async with sem:
        response = await client.get(url, headers=headers)
        await asyncio.sleep(0.5)
        return response
```

**When to flag:** Any loop making ≥1 Rossum API call per iteration where the collection could contain more than a handful of items (>5). Always report the worst-case request count and suggest the appropriate fix.

---

### 2.9 — HTTP and Concurrency

**Add `timeout=` to every HTTP call** — serverless functions have hard execution limits; a hanging request will exhaust the timeout with no useful error.

**Prefer single-threaded execution** — only use `asyncio`/`httpx` for genuinely I/O-bound performance problems (e.g. fetching 50+ pages in parallel). Never use `threading` or `multiprocessing`.

---

## Output Format

Group findings by hook file, severity first:

```
[Hook] (C) OUT2 External API Push

  [CRITICAL] Hardcoded credentials in source code
    Line: auth=("cmk_rossum_prod", "Udmscskekv8o")
    Fix: Move to payload["secrets"]; define secrets_schema in hook JSON

  [CRITICAL] next() without default — null-check below it is dead code
    Line: document_validation = next(r["documents"][0] for r in data ...)
    Fix: next(..., None) then check result is not None

  [MAJOR] No event/action validation at entry point
    Fix: return {} early if event/action/status don't match expected values

  [MAJOR] Missing raise_for_status() on 5 of 7 HTTP calls
    Lines: get_email_attachment (3×), get_email_body (2×)

  [CRITICAL] FUP risk — API calls inside loop, no rate limiting
    Loop: for annotation_id in annotation_ids (up to 10 items)
    Calls per iteration: 4 (start, validate, confirm, cancel)
    Worst case: 40 requests fired in <1s — exceeds the 2 req/s recommendation
    Fix: add time.sleep(0.5) after each API call, or use bulk endpoint if available

  [MINOR] Unused imports: BytesIO, default_to, substitute
```

End with:
```
Summary: X critical, Y major, Z minor issues across N hooks
```

After presenting all findings, list suggested fixes for each issue and ask the user whether they want them applied to the actual hook files.
