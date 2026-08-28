# Master Data Hub (MDH) API Reference

Rossum's **Master Data Hub (MDH)** manages master data datasets (suppliers, GL codes, cost centers, remit-to addresses, etc.) for use in document processing workflows. MDH provides dataset CRUD, CSV/XLSX upload, fuzzy search, and a powerful hook configuration model for matching extracted document data against master data records using MongoDB-style queries.

- **Base URL:** `/svc/master-data-hub`
- **Auth:** Bearer token (`Authorization: Bearer <token>`). The matching hook's **token owner must have an admin role**.
- **All mutating dataset operations are async** -- return `202 Accepted` with a `Location` header pointing to the operation status URL
- **Maximum file upload size:** 50 MB
- **Supported upload formats:** CSV, XLSX (multipart/form-data)
- **Common error codes:** 401 (Unauthorized), 403 (Forbidden), 413 (Request Entity Too Large), 415 (Unsupported Media Type), 422 (Unprocessable Entity), 503 (Service Unavailable) -- all return `MessageResponse {message, type}`

---

## Endpoints: Dataset

All dataset endpoints require auth. Mutating operations (POST, PUT, PATCH, DELETE) are async and return `202 Accepted`.

### `POST /api/v1/dataset/{dataset_name}` -- Create Dataset

Upload a new dataset. Dataset name must be unique in the organization. Max 50 MB.

**Path params:** `dataset_name` (string, required)

**Request body (multipart/form-data):**
| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| file | binary (file upload) | yes | -- | CSV or XLSX file |
| encoding | string | no | `"utf-8"` | File encoding (e.g. `utf-8`, `latin-1`, `cp1252`) |
| dynamic | boolean | no | false | Enable dynamic mode (allows schema changes on update) |
| field_delimiter | string | no | `","` | CSV field delimiter character |
| quoting | integer | no | 0 | CSV quoting style (0=QUOTE_MINIMAL, 1=QUOTE_ALL, 2=QUOTE_NONNUMERIC, 3=QUOTE_NONE) |
| quotechar | string | no | `"\""` | CSV quote character |
| escapechar | string | no | null | CSV escape character |
| text_qualifier | string | no | null | Alternative text qualifier |

**Response 202:** `MessageResponse` with `Location` header for status polling.
```json
{"message": "Dataset operation has been queued.", "type": "info"}
```

---

### `PUT /api/v1/dataset/{dataset_name}` -- Replace Dataset

Fully replace an existing dataset with new data. Same request body as Create.

**Path params:** `dataset_name` (string, required)

**Request body (multipart/form-data):** Same as Create Dataset.

**Response 202:** `MessageResponse` with `Location` header.

---

### `PATCH /api/v1/dataset/{dataset_name}` -- Update Dataset

Partially update an existing dataset (merge new records into existing data). Same request body as Create.

**Path params:** `dataset_name` (string, required)

**Request body (multipart/form-data):** Same as Create Dataset.

**Response 202:** `MessageResponse` with `Location` header.

---

### `DELETE /api/v1/dataset/{dataset_name}` -- Delete Dataset

Delete a dataset entirely.

**Path params:** `dataset_name` (string, required)

**Response 202:** `MessageResponse` with `Location` header.

---

### `GET /api/v1/dataset/` -- List Datasets

List all datasets in the organization.

**Response 200:** Array of dataset metadata objects.

---

## Endpoints: Operation

### `GET /api/v1/operation/{operation_id}` -- Get Operation Status

Check the status of an async dataset operation.

**Path params:** `operation_id` (string, required)

**Response 200:** Operation status object with fields:
| Field | Type | Description |
|---|---|---|
| id | string | Operation ID |
| status | enum | `queued`, `running`, `success`, `failed` |
| detail | string | Human-readable status message |
| created_at | datetime | When the operation was queued |
| updated_at | datetime | Last status update |

---

### `GET /api/v1/operation/` -- List Operations

List all recent operations for the organization.

**Response 200:** Array of operation status objects.

---

## Endpoints: Fuzzy Search

### `POST /api/v1/fuzzy_search/{dataset_name}` -- Enable Fuzzy Search

Enable fuzzy text search on a dataset. This builds a search index for use with `$search` in aggregation queries.

> Fuzzy matching is an **advanced** feature — it is **not exposed in the Rossum UI**. Enable it via this endpoint (or the hook config), then reference it with `$search` in an aggregation query.

**Path params:** `dataset_name` (string, required)

**Response 202:** `MessageResponse` with `Location` header.

---

### `DELETE /api/v1/fuzzy_search/{dataset_name}` -- Disable Fuzzy Search

Disable fuzzy search on a dataset (removes the search index).

**Path params:** `dataset_name` (string, required)

**Response 202:** `MessageResponse`

---

## Endpoints: Search Indexes V2

Atlas Search indexes are a **subresource of the dataset**, held in the MDH datasets
registry. This is the current, official API — use it for all new work.

Base path: `/svc/master-data-hub/api/v2/datasets/{dataset_name}/search_indexes`

> The old `POST /svc/data-storage/api/v1/search_indexes/{list,create,drop}` endpoints are
> URL-rewritten at ingress to MDH and still work, but that compat layer is flagged
> `deprecated: true` in MDH's own spec and exists only for existing code that is hard to
> change. The `rossum-api` MCP tools already call V2.

### `GET .../search_indexes` -- List Declared Indexes

**Response 200:** array of `SearchIndexWithStatus`:

| Field | Type | Description |
|---|---|---|
| name | string | Index name |
| definition | object | The declared definition |
| queryable | boolean | **Whether `$search` can actually use it yet** |
| status | enum | See below |
| latest_definition_version | object? | `{version, created_at}` |

`status` values — `PENDING_CREATE`, `PENDING_UPDATE`, `PENDING_DELETE` are MDH-derived sync
states meaning the registry is ahead of the engine; `PENDING`, `BUILDING`, `READY`, `STALE`,
`FAILED` are engine values passed through verbatim.

**Response shape differs from the legacy endpoint.** V2 returns a **bare JSON array**; the
old Data Storage call returned `{"code": "ok", "message": "", "result": [...]}`. The btree
`data_storage_list_indexes` still returns the wrapped shape, so the two list tools no longer
agree — read V2's array directly, with no `.result` unwrapping.

**Caveat — `definition` key casing is not stable across states.** Measured on one index: while
`PENDING_CREATE` all three read paths return **snake_case** (`search_analyzer`,
`num_partitions`); once it flips to `PENDING_DELETE` the same endpoints return **camelCase**
(`searchAnalyzer`, `numPartitions`). Writes accept camelCase either way. Do not key logic off
either spelling — read `mappings` (stable in both) and treat the rest as display.

### `GET .../search_indexes/{index_name}` -- Get One

**Response 200:** a single `SearchIndexWithStatus`. **404** when the index exists in neither
the registry nor the engine.

### `PUT .../search_indexes/{index_name}` -- Upsert One Index

The index name is in the **URL**; the body is the bare **definition** (not a
`{collectionName, indexName, mappings}` envelope like the legacy endpoint).

**Request body (`SearchIndexDefinition`):**
| Field | Type | Required | Description |
|---|---|---|---|
| mappings | object | yes | `{"dynamic": true}` or `{"dynamic": false, "fields": {...}}` |
| analyzer | string? | no | Default indexing analyzer |
| searchAnalyzer | string? | no | Query-time analyzer |
| analyzers | object[]? | no | Custom analyzer definitions |
| synonyms | object[]? | no | Synonym mappings |
| storedSource | bool\|object? | no | Store all, or select fields |
| numPartitions | int? | no | Index partitions |

Validated as a whole before it reaches the registry (**422**, nothing persisted).

**Response 202.** The declaration lands in the registry synchronously; a reconcile then
builds it on the engine. **The index is not queryable yet** — poll `GET` until
`status: READY` and `queryable: true` (~1-2 min). There is no job id to poll: the old
`operation_status/{job_id}` flow is superseded by reading the subresource.

### `DELETE .../search_indexes/{index_name}` -- Remove One Declaration

**Response 202.** Deleting the last declaration leaves `[]` — explicitly no search indexes,
not an absent field. **404** when the dataset or the declared index does not exist.

Measured: an explicit `DELETE` drops the declaration from the listing **immediately**,
whereas omitting an index from the bulk `PUT` leaves it listed as `PENDING_DELETE` until the
reconcile runs. Both end up removed; only the intermediate listing differs.

### `PUT .../search_indexes` -- Bulk Replace (destructive by omission)

Body is the **full desired list** of `{name, definition}` entries; `[]` declares no indexes
at all. Validated as a whole (**422**, nothing persisted), then the reconcile **drops
whatever the list leaves out**.

> **Not exposed as an MCP tool, deliberately.** Omission deletes. Use the per-index `PUT`
> unless you genuinely intend to replace the entire set, and read the current list first.

Verified: with `search_probe_v1` and `keep_me_v1` both declared, a bulk `PUT` naming only
`keep_me_v1` flipped `search_probe_v1` to `PENDING_DELETE` — no warning, no error, 202.

### Declaring indexes at dataset creation

`POST /api/v2/datasets/{name}` accepts `search_indexes` in the body. **Omitting the field
declares a default index named `default`** — the one Atlas queries when a `$search` names
none; `[]` declares none at all.

Measured on a sandbox org (2026-08-26): omitting the field provisions not just
`{"dynamic": true}` but the full established baseline — a `default_whitespace_lowercase`
analyzer set as both `analyzer` and `search_analyzer`, with a whitespace tokenizer, a
lowercase token filter, and a char-mapping filter folding `.` `/` `\` `-` `,`:

```json
{"name": "default", "definition": {
  "mappings": {"dynamic": true},
  "analyzer": "default_whitespace_lowercase",
  "search_analyzer": "default_whitespace_lowercase",
  "analyzers": [{"name": "default_whitespace_lowercase",
                 "tokenizer": {"type": "whitespace"},
                 "token_filters": [{"type": "lowercase"}],
                 "char_filters": [{"type": "mapping",
                   "mappings": {".": " ", "/": "", "\\": "", "-": " ", ",": " "}}]}]}}
```

This is the analyzer long observed on established collections, so a **v2-created** dataset
now lands with the correct search-index baseline rather than bare. It says nothing about
btree indexes (see the scope limit below). Immediately after create the index is
`status: PENDING_CREATE`, `queryable: false`.

### What durability does and does not guarantee

Declared indexes are **durable**: MDH continuously reconciles the registry onto the
collection. Every index that existed on the engine before the cutover was adopted into the
registry in a one-time migration, and creates through the legacy compat endpoints now write
registry-first-then-engine — so unmanaged "engine-only" indexes are not a population you
meet in practice.

- **Retire index-repair tooling.** Code that audits for a missing search index and
  re-creates it is redundant; the platform guarantees existence.
- **Keep query-vs-index conformance checks.** Durability never creates an index nobody
  declared. A query naming `search_suppliers_v1` against a collection that declares
  `search_suppliers_dev_v1` still returns zero results silently — as does an index whose
  `mappings` omit a field the query references.
- **Never fix a bad index on the engine.** Reconcile restores the declaration; an
  out-of-band Atlas change is reverted. Change the declaration instead.

### Scope limit: btree indexes are not covered

This registry covers **Atlas Search indexes only**. Regular btree indexes — including the
wildcard `$**` index (`dmv2_default_normal_index` / `__dynamic_index`) that keeps `$match`
off a full collection scan — have **no MDH v2 endpoint at all** and remain Data Storage
concerns (`/svc/data-storage/api/v1/indexes/{list,create,drop}`, MCP:
`data_storage_list_indexes` / `_create_index` / `_drop_index`). They are not self-healing,
so the "a recreated dataset lands with only `_id_`" trap still applies to precisely the
index that matters most for query performance.

---

## Key Schemas: Hook Configuration Model

MDH hooks are configured as JSON objects attached to Rossum extensions. The hook config defines how extracted document fields are matched against master data datasets using MongoDB-style queries. Below are the key schemas.

### MatchConfig (top-level hook configuration entry)

Each entry in the hook's `configurations` array is a `MatchConfig`. A single hook can have multiple configurations, each matching a different dataset against different document fields.

| Field | Type | Required | Description |
|---|---|---|---|
| source | DMDatasetSource or RestAPISource | yes | Data source definition -- typically a DMDatasetSource pointing to an MDH dataset |
| mapping | Mapping | yes | How to map the matched result back to the annotation schema |
| additional_mappings | Mapping[] | no | Extra field mappings beyond the primary one |
| result_actions | ResultActions | yes | What to do when 0, 1, or N matches are found |
| default | object | no | Default values to set if no match is found |
| preferred_result | object | no | Criteria for selecting the preferred result from multiple matches |
| action_condition | string | no | JSONLogic expression -- if it evaluates to false, skip this configuration |
| queue_ids | integer[] | no | Restrict this configuration to specific queue IDs (empty = all queues) |

---

### DMDatasetSource

Defines which MDH dataset to query and how to query it.

| Field | Type | Required | Description |
|---|---|---|---|
| dataset | string | yes | Dataset name in MDH (e.g. `"suppliers_us"`, `"gl_codes"`) |
| queries | (Find or Aggregate)[] | yes | Array of queries to run in cascade -- first query that returns results wins |

---

### Find (query type)

A MongoDB `find`-style query within a DMDatasetSource.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| find | object | yes | -- | MongoDB filter document (e.g. `{"vendor_id": "{sender_ic}"}`) -- supports `{field_id}` placeholders that are replaced with annotation values at runtime |
| projection | object | no | null | Fields to include/exclude (e.g. `{"name": 1, "code": 1}`) |
| skip | integer | no | 0 | Number of documents to skip |
| limit | integer | no | 0 | Max documents to return (0 = no limit) |
| sort | object | no | null | Sort order (e.g. `{"name": 1}`) |

**Example:**
```json
{
  "find": {"vendor_number": "{sender_ic}", "status": "active"},
  "projection": {"_id": 0, "vendor_number": 1, "name": 1, "address": 1},
  "limit": 10,
  "sort": {"name": 1}
}
```

---

### Aggregate (query type)

A MongoDB aggregation pipeline within a DMDatasetSource.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| pipeline | object[] | yes | -- | Array of aggregation stages (`$match`, `$search`, `$unwind`, `$addFields`, `$project`, `$sort`, `$limit`, etc.) -- supports `{field_id}` placeholders |
| collation | object | no | null | Collation rules (e.g. `{"locale": "en", "strength": 2}` for case-insensitive) |
| let | object | no | null | Variables for use in pipeline expressions |
| options | object | no | null | Additional aggregation options |

**Example (fuzzy text search):**
```json
{
  "pipeline": [
    {
      "$search": {
        "index": "default",
        "text": {
          "query": "{sender_name}",
          "path": ["name", "name_normalized"],
          "fuzzy": {"maxEdits": 2}
        }
      }
    },
    {"$match": {"status": "active"}},
    {"$addFields": {"score": {"$meta": "searchScore"}}},
    {"$sort": {"score": -1}},
    {"$limit": 5},
    {"$project": {"_id": 0, "vendor_number": 1, "name": 1, "score": 1}}
  ]
}
```

**Example (exact match with collation):**
```json
{
  "pipeline": [
    {"$match": {"iban": "{iban}"}}
  ],
  "collation": {"locale": "en", "strength": 2}
}
```

---

### Mapping

Defines how a matched master data record maps back to the annotation.

| Field | Type | Required | Description |
|---|---|---|---|
| target_schema_id | string | yes | The annotation schema field ID to populate with the match result (e.g. `"sender_match"`, `"item_gl_code"`) |
| dataset_key | string | yes | The dataset field to use as the unique key / stored value (e.g. `"vendor_number"`) |
| label_keys | string[] | no | Dataset fields to display as a human-readable label in the UI |
| label_template | string | no | Template string for the label (e.g. `"{vendor_number} - {name} ({city})"`) |

**Example:**
```json
{
  "target_schema_id": "sender_match",
  "dataset_key": "vendor_number",
  "label_keys": ["vendor_number", "name", "city"],
  "label_template": "{vendor_number} - {name} ({city})"
}
```

---

### ResultActions

Defines behavior for each possible match outcome.

| Field | Type | Required | Description |
|---|---|---|---|
| no_match_found | ActionConfig | yes | What to do when zero results are returned |
| one_match_found | ActionConfig | yes | What to do when exactly one result is returned |
| multiple_matches_found | ActionConfig | yes | What to do when more than one result is returned |

Each `ActionConfig` has:
| Field | Type | Required | Description |
|---|---|---|---|
| select | string | yes | `"best_match"` (auto-select top result) or `"default"` (use the default value) |
| message | string | no | Optional message to show to the user (e.g. `"No matching supplier found"`) |

**Example:**
```json
{
  "no_match_found": {"select": "default", "message": "No matching supplier found -- please select manually."},
  "one_match_found": {"select": "best_match"},
  "multiple_matches_found": {"select": "best_match", "message": "Multiple matches found -- top result auto-selected."}
}
```

---

### AnnotationContentHookResponse

The response object returned by an MDH hook after processing.

| Field | Type | Description |
|---|---|---|
| messages | ResponseMessage[] | Array of messages to display on the annotation |
| operations | object[] | Array of field-level operations (set value, set options, etc.) |
| automation_blockers | object[] | Array of conditions that block automatic export |

---

### ResponseMessage

| Field | Type | Description |
|---|---|---|
| type | enum | `"info"`, `"warning"`, or `"error"` |
| content | string | Message text |
| id | string | Schema field ID to attach the message to (optional) |

---

### Schema Datapoint Types

Schema fields in Rossum annotations use these types:

| Type | Description | Typical Use |
|---|---|---|
| `string` | Free-text string | Invoice number, vendor name, address |
| `date` | Date value (YYYY-MM-DD) | Invoice date, due date |
| `number` | Numeric value | Amounts, quantities, unit prices |
| `enum` | Dropdown / selection from options | Currency, document type, matched supplier |
| `button` | Action button | Manual triggers |

> **Numeric enum results:** when an MDH-matched value is numeric (e.g. a GL code or supplier number), set `"enum_value_type": "number"` on the enum result field so the value type-converts correctly — the default treats option values as strings.

**Common datapoint fields:**
| Field | Type | Description |
|---|---|---|
| id | string | Unique schema ID (e.g. `"document_id"`, `"sender_name"`) |
| label | string | Human-readable label shown in the UI |
| type | string | One of the types above |
| formula | string | Python formula expression (for calculated fields) |
| rir_field_names | string[] | OCR engine field mappings |
| constraints | object | Validation constraints (e.g. `{"required": true}`) |
| default_value | string | Default value for the field |
| width | number | UI column width |
| hidden | boolean | Whether the field is hidden in the UI |
| can_export | boolean | Whether the field is included in export |
| score_threshold | number | Minimum AI confidence score to auto-accept |

---

## MDH Hook Configuration Pattern

MDH hooks use a **cascade query pattern** to match extracted document data against master data. The hook configuration is a JSON object stored on the Rossum extension (hook). Here is how the cascade works:

### How Query Cascade Works

1. The hook receives an annotation event (e.g. `annotation_content` on `initialize` or `update`).
2. For each `MatchConfig` in the `configurations` array:
   - The `action_condition` is evaluated. If false, skip this config.
   - The `queue_ids` filter is checked. If the current queue is not in the list, skip.
   - The `source.queries` array is iterated **in order** (cascade):
     - **Query 1** runs. If it returns results, those results are used. Stop.
     - **Query 2** runs only if Query 1 returned nothing. If it returns results, use them. Stop.
     - **Query 3** runs only if Query 2 returned nothing. And so on.
   - The `result_actions` determine what happens based on the match count (0, 1, or N).
   - The `mapping` and `additional_mappings` populate annotation fields with matched data.
3. The hook returns an `AnnotationContentHookResponse` with field operations, messages, and automation blockers.

### Typical Query Cascade Example

```json
{
  "source": {
    "dataset": "suppliers_us",
    "queries": [
      {
        "find": {"vendor_number": "{sender_ic}", "status": "active"},
        "limit": 1
      },
      {
        "pipeline": [
          {"$match": {"iban": "{iban}", "status": "active"}}
        ]
      },
      {
        "pipeline": [
          {
            "$search": {
              "index": "default",
              "text": {
                "query": "{sender_name}",
                "path": ["name", "name_normalized"],
                "fuzzy": {"maxEdits": 2}
              }
            }
          },
          {"$match": {"status": "active"}},
          {"$addFields": {"score": {"$meta": "searchScore"}}},
          {"$sort": {"score": -1}},
          {"$limit": 5}
        ]
      },
      {
        "pipeline": [
          {"$match": {"status": "active"}},
          {"$sort": {"name": 1}},
          {"$limit": 50}
        ]
      }
    ]
  },
  "mapping": {
    "target_schema_id": "sender_match",
    "dataset_key": "vendor_number",
    "label_keys": ["vendor_number", "name", "city"],
    "label_template": "{vendor_number} - {name} ({city})"
  },
  "result_actions": {
    "no_match_found": {"select": "default", "message": "No supplier match found."},
    "one_match_found": {"select": "best_match"},
    "multiple_matches_found": {"select": "best_match"}
  }
}
```

**Cascade logic in the example above:**
1. **Query 1 (exact match by vendor number):** If the vendor number from the invoice matches a record, use it immediately.
2. **Query 2 (IBAN match):** If no vendor number match, try matching by IBAN.
3. **Query 3 (fuzzy name search):** If no IBAN match, fuzzy-search by vendor name. Returns up to 5 candidates ranked by score.
4. **Query 4 (fallback):** If nothing else matched, return all active suppliers (up to 50) for manual selection.

### Common Matching Patterns

| Pattern | Query Type | Use Case |
|---|---|---|
| Exact match by ID | `find` with `{sender_ic}` or `{vendor_number}` | Primary lookup by vendor/tax ID |
| IBAN / account match | `find` or `aggregate` with `{iban}` | Bank account matching |
| Last-N-chars match | `aggregate` with `$expr` + `$substrCP` | Partial account number matching |
| Fuzzy text search | `aggregate` with `$search` + `fuzzy` | Vendor name fuzzy matching |
| Normalized match | `aggregate` with `$toLower` / `$trim` | Case-insensitive address matching |
| SWIFT/BIC regex | `aggregate` with `$regexMatch` | BIC code pattern matching |
| Fallback (all records) | `find` or `aggregate` with broad filter | Manual selection from full list |

### Placeholder Syntax

In query filters and pipelines, use `{schema_field_id}` to reference annotation field values at runtime:
- `{sender_ic}` -- replaced with the value of the `sender_ic` field from the annotation
- `{sender_name}` -- replaced with the vendor name
- `{iban}` -- replaced with the IBAN value
- Any schema field ID can be used as a placeholder

Placeholders are string-replaced before the query is sent to the database. If the referenced field is empty, the placeholder is replaced with an empty string.
