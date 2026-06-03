# Request Processor — Complete Configuration Reference

A flexible, multi-stage engine for integrating Rossum with external APIs. Configure complex export workflows using JSON settings — no code required. Runs as a single serverless function hook, replacing the legacy multi-hook export pipeline.

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
11. [Common Patterns](#common-patterns)
12. [SFTP Export Pattern](#sftp-export-pattern)
13. [Complete Examples](#complete-examples)
14. [Migration from Pipeline v1](#migration-from-pipeline-v1)
15. [Field Reference](#field-reference)
16. [Troubleshooting](#troubleshooting)

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

Function hooks that use `{payload.rossum_authorization_token}` (e.g. SFTP Export, or OAuth flows that cache tokens via `update_hook_secrets`) require `token_owner` to be set on the hook. Without it, the token field is absent from the payload.

**Symptom:** `KeyError: 'rossum_authorization_token'` on first invocation; OAuth flow cannot persist `update_hook_secrets`.

**Fix:** Set `token_owner` to a user URL when creating the hook, or PATCH it onto an existing hook.

### Hook-level: `sideload: ["schemas"]`

The Request Processor requires schema sideloading on the hook. (Response handlers also depend on the schema being present — see the [`schema_id` target callout](#response-handlers) below.)

**Symptom:** `PayloadError: Schema sideloading must be enabled!`

**Fix:** Add `"sideload": ["schemas"]` to the hook config.

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
- **Restricted to the hook's parent domain.** The fetch is allowed only when the URL's hostname shares the same final two dot-separated components as the hook's `base_url` (e.g. a hook on `elis.rossum.ai` can fetch anything ending in `rossum.ai`, including `api.elis.rossum.ai`; it cannot fetch `httpbin.org` or `*.rossum.app`). URLs that don't match return `None` silently — use a `call_api` stage with an explicit request to fetch from third-party hosts.

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
| `$size` | Array length | `{"field.line_items": {"$size": {"$gt": 0}}}` |
| `$and` / `$or` | Logical operators | `{"$and": [{...}, {...}]}` |

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

**Important:** Only the first document in each relation's `documents[]` is fetched.

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

Fetches annotation relations.

```json
{
  "name": "parent_annotation",
  "source": "relation",
  "query": {"type": {"$eq": "parent"}}
}
```

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
| `content_type` | String | No | `json`, `form`, `files`, or `multipart`. Default: `form`. |
| `headers` | Object | No | Custom headers. |
| `iterate_over` | String | No | Path to list variable to iterate over. |
| `iteration_item_name` | String | No | Variable name for current item (default: `item`). |

### Content Types

| Type | Sends As | Use For |
|------|----------|---------|
| `json` | `application/json` | Structured data |
| `form` | `application/x-www-form-urlencoded` | Simple key-value pairs |
| `files` | `multipart/form-data` | File uploads |
| `multipart` | `multipart/form-data` | Mixed files and data fields |

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

### Auth Object Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | String | Yes | Auth endpoint URL. Supports templating. |
| `method` | String | Yes | HTTP method for auth request. |
| `content_type` | String | No | `json` or `form`. |
| `content` | Object | No | Auth request body (credentials). |
| `headers` | Object | No | Custom headers for auth request. |
| `params` | Object | No | Query parameters for auth request. |
| `credential_key` | String | Yes | Dot-path to token in response (e.g., `access_token` or `data.token`). |

### Token Behavior

- Tokens are **cached** and reused for identical auth configs
- Cache **persisted in hook secrets** — survives across function executions
- Auto-**invalidated and refreshed** if API returns 401
- Available as `{token}` in request templates
- You **must** explicitly set the Authorization header: `"Authorization": "Bearer {token}"`

---

## Response Handlers

Process API responses and extract/store data.

### Response Handler Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `condition` | Object | No | Filter query — handler only runs if condition passes. |
| `target_type` | String | Yes | `schema_id`, `property`, or `document_relation`. |
| `target_key` | String | Yes | Field name, property key, or relation key. Supports `{sequence}`. |
| `value` | ValueConfig | No* | How to extract value. *Required for `schema_id` and `property`. |

### Target Types

| Type | Description | Notes |
|------|-------------|-------|
| `schema_id` | Write to a document field | Dicts stored as JSON string, numbers as string |
| `property` | Store in property context | Available in later stages via `{property.key}` |
| `document_relation` | Save response as new Rossum document | Creates/updates relation with given key |

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

**Context paths:**

| Path | Type | Description |
|------|------|-------------|
| `response.status_code` | Integer | HTTP status code |
| `response.headers` | Object | Normalized headers (underscores, lowercase) |
| `response.raw` | List | Raw headers as tuples |
| `response.body` | Any | Parsed body (JSON object/array, XML string, text, or bytes) |
| `response.text` | String | Response as text |
| `response.content` | Bytes | Raw response bytes |
| `response.url` | String | Final URL (after redirects) |
| `response.ok` | Boolean | True if status < 400 |
| `response.elapsed` | Float | Request duration in seconds |
| `response` | Object | All response fields as one object |

**Body parsing by content type:**

| Content-Type | Parsed As |
|--------------|-----------|
| `application/json` | JSON object/array |
| Contains `xml` | String (use `$xmlpath`) |
| Contains `text` | Plain text string |
| Other/unknown | Raw bytes |

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

// Custom response structure
{
  "target_type": "schema_id",
  "target_key": "api_metadata",
  "value": {"response": {"$jmespath": "{status_code: status_code, headers: headers, raw: raw}"}}
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

**Multiple values to single field:** If a response handler targets the same field during iteration, the processor auto-collects values into a list (e.g., `["ID1", "ID2", "ID3"]`).

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

---

## Complete Examples

### Real-World: Coupa Integration (5-stage)

A production configuration that creates a Coupa invoice, uploads scans, attaches URL, uploads related documents, and attaches email:

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
| `source` | String | Yes | `relation`, `document_relation`, `document_relation_content`, or `explicit` |
| `query` | Object/List | Yes | Filter query (relations) or template list (explicit) |

### Auth Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | String | Yes | Auth endpoint URL |
| `method` | String | Yes | HTTP method |
| `content_type` | String | No | `json` or `form` |
| `content` | Object | No | Auth request body |
| `headers` | Object | No | Custom headers |
| `params` | Object | No | Query parameters |
| `credential_key` | String | Yes | Dot-path to token in response |

### Requester Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | String | Yes | API endpoint URL |
| `method` | String | Yes | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `content` | Any | No | Request body |
| `content_type` | String | No | `json`, `form`, `files`, `multipart` |
| `headers` | Object | No | Custom headers |
| `iterate_over` | String | No | Path to list for iteration |
| `iteration_item_name` | String | No | Item variable name (default: `item`) |

### ResponseHandler Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `condition` | Object | No | Filter query for conditional execution |
| `target_type` | String | Yes | `schema_id`, `property`, or `document_relation` |
| `target_key` | String | Yes | Target field/key name |
| `value` | ValueConfig | No* | Value extraction config |

### ValueConfig Object

Format: `{"context_path": {"$operator": "query"}}`

- `context_path`: `response`, `response.body`, `response.headers`, `response.text`
- Operator: exactly one of `$jmespath`, `$xmlpath`, `$regex`

---

## Troubleshooting

### URL Not Fetching
**Problem:** URL returns as string instead of fetched content.
**Fix:** Add `.@` to explicitly fetch: `{payload.document.content.@}`

### Token Not Caching
**Problem:** New token requested every time.
**Fix:** Ensure auth config object is identical across all calls (cache key is the entire auth object).

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
**Fix:** Status codes stored as strings in fields. Use `{"$in": ["200", "201"]}` not `{"$in": [200, 201]}`.

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
