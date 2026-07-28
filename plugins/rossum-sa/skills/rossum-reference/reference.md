# Rossum.ai Complete Reference

## Platform Overview

Rossum.ai is a cloud-based enterprise automation platform for processing transactional documents (invoices, purchase orders, bills of lading, receipts, etc.). The platform provides:

- **Aurora AI Engine**: Proprietary Transactional Large Language Model (T-LLM) supporting 276 languages and handwriting (30 languages), with zero hallucinations via discriminative decoder
- **Cloud-based UI** for verification and correction of extracted data
- **Extension environment** for custom logic (webhooks, serverless functions, formula fields, TxScript)
- **Master Data Hub** for matching extracted data against system records
- **Built-in extensions**: Business Rules Validation, Duplicate Detection, Copy & Paste, Find & Replace, Value Mapping, Line Items Grouping
- **Export pipeline** for structured data delivery (REST API, SFTP, S3)
- **Embedded mode** for integrating Rossum's validation UI into third-party apps
- **Sandboxes** for isolated development and deployment workflows
- **Reasoning fields** (inline LLM fields) for AI-generated values based on prompts
- Reporting database and audit logs
- API for programmatic access

### Five-Stage Processing Pipeline

1. **Document Receipt**: Ingestion via email, API upload, SFTP/S3, EDI, shared drives (PDF, XML, JSON, UBL, images)
2. **Document Understanding**: Aurora AI extracts data with confidence scores, filters spam/duplicates, classifies documents
3. **Data Validation & Enrichment**: Business rules, master data matching, computed fields (GL codes, tax codes), cross-validation
4. **Automated Actions**: Approval workflows, notifications, integration with downstream systems
5. **Insights & Compliance**: Audit trails, document archiving, performance reporting

### Aurora AI Engine

Aurora is Rossum's proprietary T-LLM trained on hundreds of millions of transactional documents:

- **Pre-trained fields** for immediate extraction (focused on AP/AR scenarios)
- **Continuous learning** from user-confirmed documents (no manual retraining needed)
- **10x fewer training examples** needed vs. traditional models
- **Discriminative decoder** prevents hallucinations and prompt injection
- **Confidence scores** on every extracted field for threshold-based automation
- Documents must be **confirmed/exported by a human** (not automated) to trigger learning
- **Value Source must be "Captured"** for AI-driven extraction learning
- Does **not** currently support: handwritten data extraction (except 30 languages), watermark recognition

**Queue strategy**: Separate queues when different field sets apply, or for documents in different scripts/regions

### Architecture Hierarchy

```
Organization
└── Workspace
    └── Queue (linked to a Schema)
        ├── Inbox (email import)
        ├── Hooks (extensions: webhooks, serverless functions, connectors)
        └── Documents
            └── Annotations (extracted data + lifecycle)
                └── Pages
```

### Key Concepts

- **Organization**: Top-level account containing users, workspaces, and billing
- **Workspace**: Groups queues for logical project separation
- **Queue**: Document processing pipeline with a linked schema; each queue processes documents according to its configured schema
- **Schema**: Defines the structure and fields to extract from documents (sections, datapoints, multivalues/tables)
- **Document**: An uploaded file (PDF, PNG, JPEG, TIFF, XLSX, XLS, DOCX, DOC, HTML)
- **Annotation**: Extracted data from a document, tracking the full processing lifecycle
- **Page**: Individual page within a document
- **Hook/Extension**: Webhook, serverless function, or connector that extends platform behavior
- **Inbox**: Email endpoint that auto-imports documents into a queue
- **Dedicated Engine**: Custom AI model trained for specific document types or use cases
- **Label**: Tags for organizing and filtering annotations

---

## Authentication

### Token-Based Auth

**Login**: `POST /v1/auth/login`
- Parameters: `username` (string, required), `password` (string, required), `max_token_lifetime_s` (integer, optional, default: 162 hours)
- Response: `{"key": "token_string", "domain": "domain_name"}`
- Usage: `Authorization: Bearer {token}` or `Authorization: Token {token}`

**Logout**: `POST /v1/auth/logout`

**Token Exchange**: `POST /v1/auth/token`
- Parameters: `scope` ("default" or "approval"), `max_token_lifetime_s` (max 583200s)
- Response: `{"key": "token", "domain": "domain", "scope": "default"}`

### JWT Authentication

Short-lived JWT tokens can be exchanged for access tokens. Supports EdDSA (Ed25519, Ed448) and RS512 signatures only, max token validity 60 seconds.

**JWT Header**: `alg` (required: "EdDSA" or "RS512"), `kid` (required, ends with `:{Rossum org ID}`), `typ` (optional)

**JWT Payload**: `ver` ("1.0"), `iss` (issuer name), `aud` (target domain URL), `sub` (user email), `exp` (UNIX timestamp, max 60s from now), `email`, `name`, `rossum_org` (org ID), `roles` (optional, for auto-provisioning)

### Single Sign-On (SSO)

OAuth2 OpenID Connect protocol. Redirect URI: `https://<domain>.rossum.app/api/v1/oauth/code`. Email claims use case-insensitive matching.

### Basic Auth

Supported for upload/export endpoints: `Authorization: Basic {base64(username:password)}`

---

## API Conventions

**Base URL**: `https://<domain>.rossum.app/api/v1`

**Clusters and shared extension URLs**: Rossum runs on multiple regional clusters (e.g. `eu2`, `us2`). Shared (built-in) extensions use cluster-specific URL prefixes: `https://shared-{cluster}.{extension-name}.rossum-ext.app/`. For example, on US2: `https://shared-us2.custom-format-templating.rossum-ext.app/`. The SFTP/file-storage export URL follows a different pattern: `https://shared-{cluster}.rossum.app/svc/file-storage-export/api/v1/export`. Always match the cluster prefix to the organization's deployment cluster — using the wrong prefix will fail silently or route to the wrong environment.

**Pagination**: All list endpoints use `page_size` (default: 20, max: 100) and `page` (default: 1)

**Ordering**: `ordering` parameter, prefix with `-` for descending

**Date Format**: ISO 8601 in UTC (e.g., `2018-06-01T21:36:42.223415Z`)

**Rate Limits**: 10 requests/second (general), 10 requests/minute (translate endpoint)

**Metadata**: Most objects support custom `metadata` JSON (up to 4 KB per object)

**File Size Limit**: 40 MB per document, 50 MB for email imports

**Supported Import Formats**: PDF, PNG, JPEG, TIFF, XLSX, XLS, DOCX, DOC, HTML

**Export Formats**: CSV, XML, JSON, XLSX

### Common Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not found |
| 409 | Conflict |
| 429 | Too many requests (check `Retry-After` header) |
| 500 | Server error |

---

## Organizations

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/organizations` | List organizations |
| POST | `/v1/organizations` | Create organization |
| GET | `/v1/organizations/{id}` | Retrieve organization |
| POST | `/v1/organizations/{id}/token` | Generate access token |
| GET | `/v1/organizations/{id}/limits` | Get usage limits |
| GET | `/v1/organizations/{id}/billing` | Get billing info |

---

## Workspaces

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/workspaces` | List workspaces |
| POST | `/v1/workspaces` | Create workspace |
| GET | `/v1/workspaces/{id}` | Retrieve workspace |
| PUT | `/v1/workspaces/{id}` | Update workspace |
| PATCH | `/v1/workspaces/{id}` | Partial update |
| DELETE | `/v1/workspaces/{id}` | Delete workspace |

**Create/Update fields**: `name` (required), `organization` (URL, required), `metadata` (optional, up to 4 KB)

**Filtering**: `organization` (integer)

---

## Queues

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/queues` | List queues |
| POST | `/v1/queues` | Create queue |
| POST | `/v1/queues/from_template` | Create self-contained queue (+ fresh schema/inbox; fresh engine in next-gen mode, shared generic engine with `?legacy=true`) from a queue template; wrapped by the `rossum_create_queue_from_template` MCP tool |
| GET | `/v1/queues/{id}` | Retrieve queue |
| PUT | `/v1/queues/{id}` | Update queue |
| PATCH | `/v1/queues/{id}` | Partial update; wrapped by the `rossum_patch_queue` MCP tool |
| DELETE | `/v1/queues/{id}` | Delete queue — async with a 24h grace window (`?delete_after=0` skips it); wrapped by the `rossum_delete_queue` MCP tool, which also cascade-deletes the queue's sole-referenced schema/inbox/engine |
| POST | `/v1/queues/{id}/duplicate` | Duplicate queue (deep-copies the schema, fresh inbox, shares the engine); wrapped by the `rossum_duplicate_queue` MCP tool |
| POST | `/v1/queues/{id}/import` | Import document |
| GET | `/v1/queues/{id}/export` | Export annotations |
| GET | `/v1/queues/{id}/counts` | Get counts |

### Queue Fields

**Core attributes**: `id`, `url`, `name` (string, required), `workspace` (URL, required), `schema` (URL, required)

**Processing settings**:
- `default_score_threshold` (float 0-1): AI confidence cutoff for automatic field validation; overridable per datapoint
- `engine` (URL, optional): modern custom extraction engine (`/v1/engines/{id}`) — see [Extraction Engines](#extraction-engines)
- `dedicated_engine` (URL, optional): legacy dedicated ML engine
- `generic_engine` (URL, optional): pretrained generic extraction engine (new queues auto-bind one)
- `locale` (string): Language/region code (e.g., `"en_US"`) affecting UI and extraction
- `automation` (object): Auto-validation behavior settings
- `accepted_mime_types` (array): File types permitted for upload
- `rir_params` (object): Parameters for initializing field values
- `metadata` (object, optional): Custom JSON (max 4 KB)

The three engine properties are mutually exclusive in practice — exactly one is non-null on a healthy queue. A non-null `engine` changes the schema editing rules (see [Extraction Engines](#extraction-engines)).

**Workflow settings**:
- `confirmation` (object): Criteria for requiring manual confirmation
- `rejection` (object): Rejection workflow settings (enable/disable rejection status)

**Filtering**: `workspace` (integer), `locale` (string)

### Queue Examples

```bash
# List queues in a workspace
curl -H 'Authorization: Bearer TOKEN' \
  'https://<domain>.rossum.app/api/v1/queues?workspace=7540&locale=en_US&ordering=name'

# Create queue
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Invoice Processing",
    "workspace": "https://<domain>.rossum.app/api/v1/workspaces/123",
    "schema": "https://<domain>.rossum.app/api/v1/schemas/456"
  }' \
  'https://<domain>.rossum.app/api/v1/queues'
```

### Export

`GET /v1/queues/{id}/export`

**Parameters**: `status` (filter by annotation status), `format` (`csv`/`xml`/`json`/`xlsx`), `id` (specific annotation IDs, comma-separated), `page_size` (up to 1000 for CSV)

Only fields with `can_export: true` are included.

```bash
curl -H 'Authorization: Bearer TOKEN' \
  'https://<domain>.rossum.app/api/v1/queues/8199/export?status=exported&format=csv&id=319668'
```

---

## Schemas

Schemas define what data gets extracted from documents.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/schemas` | List schemas |
| POST | `/v1/schemas` | Create schema |
| GET | `/v1/schemas/{id}` | Retrieve schema |
| PUT | `/v1/schemas/{id}` | Update schema |
| PATCH | `/v1/schemas/{id}` | Partial update |
| DELETE | `/v1/schemas/{id}` | Delete schema |
| POST | `/v1/schemas/validate` | Validate schema content without saving (dry-run); wrapped by the `rossum_validate_schema` MCP tool — pass the schema `id` in the body to enable engine-binding checks |

### Schema Content Structure

Schemas consist of **sections** containing **datapoints** (header fields) and **multivalues** (tables/line items).

**Common attributes** (all schema objects):
- `category`: "section", "datapoint", "multivalue", or "tuple"
- `id`: Unique identifier (max 50 chars)
- `label`: Display name
- `hidden`: Hide from UI (default: false)
- `disable_prediction`: Disable AI extraction (default: false)

### Datapoint (Field) Types with Examples

**String field**:
```json
{
  "category": "datapoint",
  "id": "document_id",
  "label": "Invoice ID",
  "type": "string",
  "rir_field_names": ["document_id"],
  "constraints": {
    "length": {"max": 16, "min": null},
    "regexp": {"pattern": "^INV[0-9]+$"},
    "required": false
  },
  "default_value": null
}
```

**Number field**:
```json
{
  "category": "datapoint",
  "id": "item_quantity",
  "type": "number",
  "label": "Quantity",
  "format": "#,##0.#"
}
```

**Date field**:
```json
{
  "category": "datapoint",
  "id": "item_delivered",
  "type": "date",
  "label": "Delivered",
  "format": "MM/DD/YYYY"
}
```

**Enum field**:
```json
{
  "category": "datapoint",
  "id": "document_type",
  "type": "enum",
  "label": "Document Type",
  "options": [
    {"label": "Invoice Received", "value": "21"},
    {"label": "Receipt", "value": "23"}
  ],
  "enum_value_type": "number",
  "default_value": "21"
}
```

**Button** (`popup_url`, `can_obtain_token`), **Formula** (calculated from other fields), **Reasoning** (AI-generated from prompt and context) are also supported.

### Datapoint Configuration

- `rir_field_names` (array): Sources for field values (AI extraction, upload, email). Supports prefixes:
  - `"document_id"` — AI-extracted field
  - `"upload:my_field_id"` — User-provided value during upload
  - `"edit:my_field_id"` — User-provided value via edit endpoint
  - `"email_header:subject"` — Email header (from, to, reply-to, subject, message-id, date)
  - `"email_body:text_html"` — HTML email body
- `default_value`: Fallback if extraction unavailable
- `constraints`: `length` (min/max), `regexp` (pattern), `required`
- `score_threshold` (float 0-1): AI confidence threshold for auto-validation
- `can_export` (boolean): Whether included in export
- `can_collapse` (boolean): For tabular fields in UI
- `ui_configuration.type`: `captured`, `data`, `manual`, `formula`, `reasoning`
- `ui_configuration.edit`: `enabled`, `enabled_without_warning`, `disabled`

### Common `rir_field_names` (AI Extraction Sources)

**Identifiers**: `document_id`, `customer_id`, `order_id`, `account_num`, `iban`, `bic`, `bank_num`

**Dates**: `date_issue`, `date_due`, `date_delivery`, `date_performance`

**Parties**: `sender_name`, `sender_address`, `sender_ic`, `sender_dic`, `recipient_name`, `recipient_address`, `recipient_ic`, `recipient_dic`

**Amounts**: `amount_total`, `amount_due`, `amount_paid`, `amount_total_tax`, `amount_total_base`, `amount_rounding`

**Document attributes**: `currency`, `document_type`, `language`, `payment_method_type`

**Line item columns**: `item_description`, `item_quantity`, `item_amount_total`, `item_amount_base`, `item_amount_tax`, `item_tax_rate`, `item_uom`, `item_code`, `item_other`

**Tax details**: `tax_detail_rate`, `tax_detail_base`, `tax_detail_tax`, `tax_detail_total`, `tax_detail_code`

### Multivalue (Table Container)

- `children`: Nested datapoint or tuple
- `min_occurrences` / `max_occurrences`: Row count limits
- `grid.row_types`: Classify rows (header, data, footer)
- `grid.default_row_type`: Default classification
- `grid.row_types_to_extract`: Which rows to include in export

### Tuple (Table Row)

- `children`: Array of datapoints in the row
- `rir_field_names`: AI field sources for the row

### Complete Schema Example

```json
[
  {
    "category": "section",
    "id": "invoice_info_section",
    "label": "Basic Information",
    "children": [
      {
        "category": "datapoint",
        "id": "document_id",
        "label": "Invoice Number",
        "type": "string",
        "rir_field_names": ["document_id"]
      },
      {
        "category": "datapoint",
        "id": "date_issue",
        "label": "Issue Date",
        "type": "date",
        "format": "YYYY-MM-DD",
        "rir_field_names": ["date_issue"]
      }
    ]
  },
  {
    "category": "section",
    "id": "amounts_section",
    "label": "Amounts",
    "children": [
      {
        "category": "datapoint",
        "id": "amount_total",
        "label": "Total Amount",
        "type": "number",
        "format": "#,##0.00",
        "rir_field_names": ["amount_total"]
      },
      {
        "category": "multivalue",
        "id": "line_items",
        "label": "Line Items",
        "rir_field_names": ["line_items"],
        "min_occurrences": 0,
        "max_occurrences": 1000,
        "children": {
          "category": "tuple",
          "id": "line_item",
          "rir_field_names": ["line_items"],
          "children": [
            {
              "category": "datapoint",
              "id": "item_description",
              "label": "Description",
              "type": "string",
              "rir_field_names": ["item_description"]
            },
            {
              "category": "datapoint",
              "id": "item_quantity",
              "label": "Quantity",
              "type": "number",
              "rir_field_names": ["item_quantity"]
            },
            {
              "category": "datapoint",
              "id": "item_amount_total",
              "label": "Amount",
              "type": "number",
              "format": "#,##0.00",
              "rir_field_names": ["item_amount_total"]
            }
          ]
        }
      },
      {
        "category": "multivalue",
        "id": "vat_details",
        "label": "VAT Details",
        "rir_field_names": ["tax_details"],
        "children": {
          "category": "tuple",
          "id": "vat_detail",
          "children": [
            {
              "category": "datapoint",
              "id": "vat_detail_rate",
              "label": "VAT Rate",
              "type": "number",
              "rir_field_names": ["tax_detail_rate"],
              "format": "# ##0.#"
            }
          ]
        }
      }
    ]
  }
]
```

### Schema Update Behavior

Data values are preserved when: adding/removing fields, reordering fields, moving fields between sections, converting single fields to multivalues, changing tuple membership, updating labels/formats/constraints/enum options. The `category` and `schema_id` must remain unchanged for data preservation.

---

## Documents

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/documents` | List documents |
| POST | `/v1/documents` | Create document |
| GET | `/v1/documents/{id}` | Retrieve document |
| PATCH | `/v1/documents/{id}` | Partial update |
| GET | `/v1/documents/{id}/content` | Get file content |
| DELETE | `/v1/documents/{id}` | Delete document |

**Attributes**: `id`, `url`, `s3_name`, `mime_type`, `arrived_at`, `original_file_name`, `content` (file URL), `metadata`, `annotations` (array of URLs)

**Supported formats**: PDF, PNG, JPEG, TIFF, XLSX, XLS, DOCX, DOC, HTML (max 40 MB)

---

## Annotations

Annotations represent extracted data from documents and track the full processing lifecycle.

### Annotation Lifecycle

```
                                ┌──────────┐
                         ┌─────│ importing │
                         │     └──────────┘
                         │           │
                         │     ┌─────▼──────┐
              ┌──────────┤     │ to_review   │◄─────────────────────┐
              │          │     └─────┬───────┘                      │
              │          │           │                               │
         ┌────▼─────┐   │     ┌─────▼──────┐    ┌────────────┐     │
         │ failed_   │   │     │ reviewing  │───►│ confirmed  │     │
         │ import    │   │     └────────────┘    └─────┬──────┘     │
         └──────────┘   │                              │            │
                         │     ┌────────────┐    ┌─────▼──────┐     │
                         │     │ rejected   │    │in_workflow  │     │
                         │     └────────────┘    └─────┬──────┘     │
                         │                              │            │
                         │                        ┌─────▼──────┐     │
                         │                        │ exporting  │─────┘
                         │                        └─────┬──────┘  (on failure)
                         │                              │
                         │                        ┌─────▼──────┐
                         │                        │ exported   │
                         │                        └────────────┘
                         │
                    ┌────▼─────┐    ┌──────────┐
                    │postponed │    │ deleted   │──► purged
                    └──────────┘    └──────────┘
```

**Status descriptions**:

| Status | Description |
|--------|-------------|
| `created` | Manually created, awaiting import |
| `importing` | AI engine actively extracting data |
| `failed_import` | Processing error (malformed file, etc.) |
| `split` | Divided into multiple documents |
| `to_review` | Extraction complete, awaiting validation |
| `reviewing` | User actively validating |
| `confirmed` | User validated and confirmed |
| `rejected` | User declined annotation |
| `in_workflow` | Processing through automated workflows (content locked) |
| `exporting` | Awaiting connector completion |
| `exported` | Successfully exported (terminal state) |
| `failed_export` | Connector returned error |
| `postponed` | User deferred processing |
| `deleted` | Marked for deletion |
| `purged` | Metadata-only retention (irreversible) |

### Annotation Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/annotations` | List annotations |
| POST | `/v1/annotations` | Create annotation |
| GET | `/v1/annotations/{id}` | Retrieve annotation |
| PUT | `/v1/annotations/{id}` | Update annotation |
| PATCH | `/v1/annotations/{id}` | Partial update |
| DELETE | `/v1/annotations/{id}` | Delete annotation |
| POST | `/v1/annotations/{id}/copy` | Copy annotation |
| POST | `/v1/annotations/{id}/start` | Start annotation |
| POST | `/v1/annotations/{id}/confirm` | Confirm annotation |
| POST | `/v1/annotations/{id}/cancel` | Cancel annotation |
| POST | `/v1/annotations/{id}/approve` | Approve annotation |
| POST | `/v1/annotations/{id}/reject` | Reject annotation |
| POST | `/v1/annotations/{id}/assign` | Assign to user |
| POST | `/v1/annotations/{id}/postpone` | Switch to postponed |
| POST | `/v1/annotations/{id}/switch_to_deleted` | Switch to deleted |
| POST | `/v1/annotations/{id}/rotate` | Rotate pages |
| POST | `/v1/annotations/{id}/edit` | Edit annotation |
| POST | `/v1/annotations/{id}/split` | Split annotation |
| POST | `/v1/annotations/{id}/validate` | Validate content |
| POST | `/v1/annotations/{id}/purge` | Purge deleted |
| GET | `/v1/annotations/{id}/time_spent` | Get time spent |
| GET | `/v1/annotations/{id}/page_data` | OCR text / spatial data — `granularity` required; see [Page data](#page-data-ocr-text-and-spatial-data) |
| POST | `/v1/annotations/{id}/page_data/translate` | Translate spatial data |
| POST | `/v1/annotations/search` | Search annotations |

### Annotation Content (Extracted Data)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/annotations/{id}/content` | Get extracted data |
| PATCH | `/v1/annotations/{id}/content` | Update data |
| POST | `/v1/annotations/{id}/content/bulk_update` | Bulk update |
| POST | `/v1/annotations/{id}/content/replace_by_ocr` | Re-OCR |
| POST | `/v1/annotations/{id}/content/validate` | Validate against schema |

### Annotation Object Fields

- `id` (integer): Unique identifier
- `url` (string): API endpoint URL
- `status` (string): Current lifecycle state
- `document` (string): Associated document URL
- `queue` (string): Parent queue URL
- `schema` (string): Extraction schema URL
- `modifier` (string): User URL who last modified
- `created_at`, `updated_at`, `confirmed_at`, `started_at` (string): ISO 8601 timestamps
- `content` (object): Extracted data structure
- `messages` (array): Validation messages and errors
- `metadata` (object): Custom JSON (up to 4 KB)

### Annotation Response Example

```json
{
  "id": 319668,
  "url": "https://<domain>.rossum.app/api/v1/annotations/319668",
  "queue": "https://<domain>.rossum.app/api/v1/queues/8199",
  "document": "https://<domain>.rossum.app/api/v1/documents/319768",
  "status": "to_review",
  "created_at": "2019-02-11T19:22:33.993427Z",
  "updated_at": "2019-02-11T19:25:15.123456Z",
  "modifier": "https://<domain>.rossum.app/api/v1/users/42",
  "metadata": {"batch_id": "12345"}
}
```

### Filtering & Sideloading

**Query parameters**: `status`, `queue` (integer), `workspace` (integer), `modifier` (integer), `created_at`, `updated_at` (ISO 8601 date ranges), `ordering`

**Sideloading**: `sideload=content` (include extracted data), `sideload=document` (include document metadata). When `sideload=content` is not used, search max page size is 500.

### Annotation Operations Detail

**Copy**: `POST /v1/annotations/{id}/copy` — Body: `{"target_queue": "URL", "target_status": "to_review"}`

**Search**: `POST /v1/annotations/search` — Max page size 500 (1000 for CSV export)

**Validate**: `POST /v1/annotations/{id}/content/validate` — Returns validation messages, constraint violations, table aggregations, and AI confidence scores

---

## Pages

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/pages` | List pages |
| GET | `/v1/pages/{id}` | Retrieve page |

**Attributes**: `id`, `url`, `annotation`, `page_number` (1-indexed), `image` (URL), `width`, `height`

> `GET /v1/pages?annotation=<id>` returns pages in **arbitrary order** (observed `1,4,6,13,12,…`).
> Sort by `number` yourself; never trust list position.

### Page data (OCR text and spatial data)

```
GET /v1/annotations/{id}/page_data?granularity=<g>[&page_numbers=1,25]
```

The way to get **OCR text** out of a document — including a clean whole-page text blob for
feeding an LLM or a reasoning field's parked context field.

**`granularity` is required.** Omitting it is `HTTP 400 {"granularity":["This field is required."]}`;
an unknown value gives `"\"x\" is not a valid choice."` without listing the valid ones.

| `granularity` | Items per page | Item shape |
|---|---|---|
| `texts` | exactly **one** | `{"text": "<the whole page>"}` — **no `position` key** |
| `lines` | one per line | `{"position": [x1,y1,x2,y2], "text": …}` |
| `words` | one per word | `{"position": […], "text": …}` |
| `chars` | one per character | `{"position": […], "text": …}` |
| `barcodes` | one per barcode | `[]` when the page has none |

**Use `texts` for a page-text blob.** It returns the full page as a single string, so there is no
line joining and no reading-order reconstruction to get wrong. The other granularities exist for
spatial work and hand back dozens-to-thousands of fragments per page.

**Response**

```json
{"results": [{"page_number": 1, "granularity": "texts", "items": [{"text": "…"}]}]}
```

#### `page_numbers` defaults to the first 20 pages — and every limit fails silently

Verified live against a 25-page document:

| Request | Returns |
|---|---|
| *(no `page_numbers`)* | 20 results, pages **1–20** — so `results[-1]` is page 20, **not** the last page |
| `page_numbers=6,7,…,26` (21 entries) | pages **6–25** — truncated to the first 20 **of your list**, no error |
| `page_numbers=25,24,23` | `[23,24,25]` — **always ascending**, request order ignored |
| `page_numbers=1,1,1,25` | `[1,25]` — duplicates collapsed |
| `page_numbers=99` / `0` / `-1` | `results: []`, HTTP 200 — out-of-range silently dropped |
| `page_numbers=` (empty) | treated as absent → the 20-page default |
| `page_numbers=abc` | HTTP 400 `Invalid value, expected to be a comma separated list of integers.` |

Only a non-numeric value errors. Everything else — the 20-page cap, an over-long list, a page that
does not exist — returns HTTP 200 with quietly wrong or missing data.

**Therefore:**
1. **Always pass `page_numbers` explicitly** when you care about specific pages. For first+last of
   an `n`-page document that is `page_numbers=1,{n}` — get `n` from `GET /v1/pages?annotation=<id>`.
2. **Match results by `page_number`, never by array index.** `results[-1]` is the highest page
   number returned, which on a 21+ page document defaults to page 20.

> The community-documented caveat that the **Full Page Search** extension "only reads the first 20
> pages" is this API default surfacing, not a limitation of that extension.

---

## Uploads

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/uploads` | Upload document |
| GET | `/v1/uploads/{id}` | Check upload status |

**Upload states**: `created` → `processing` → `succeeded` / `failed`

**Format**: `multipart/form-data`

**Parameters**: `queue` (required, as URL parameter), `content` (file, required), `metadata` (optional JSON, max 4 KB)

**Pre-filling fields on import**: Use `rir_field_names: ["upload:my_id"]` in the schema, then pass values during upload.

**Recommended**: A4 format, minimum 150 DPI for scans/photos

```bash
# Upload a document
curl -H 'Authorization: Bearer TOKEN' \
  -F content=@document.pdf \
  'https://<domain>.rossum.app/api/v1/uploads?queue=8199'
```

Response returns a task URL for monitoring processing status.

---

## Hooks (Extensions)

Hooks extend Rossum with custom logic. Three types: **webhooks**, **serverless functions**, and **connectors**.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/hooks` | List hooks |
| POST | `/v1/hooks` | Create hook |
| GET | `/v1/hooks/{id}` | Retrieve hook |
| PUT | `/v1/hooks/{id}` | Update hook |
| PATCH | `/v1/hooks/{id}` | Partial update |
| DELETE | `/v1/hooks/{id}` | Delete hook |
| POST | `/v1/hooks/{id}/test` | Test hook |
| POST | `/v1/hooks/{id}/manual_trigger` | Manual trigger |
| GET | `/v1/hooks/{id}/logs` | List call logs |

### Hook Object Fields

- `id` (integer): Unique identifier
- `url` (string): API endpoint
- `type` (string): `"webhook"`, `"function"`, or connector type
- `name` (string): Display name
- `events` (array): Trigger event types
- `config` (object): Extension-specific configuration
- `queues` (array): Queue URLs this hook applies to
- `active` (boolean): Enable/disable — check this (together with `queues`) before treating anything in the hook's settings as live behavior; inactive hooks are common leftovers in real implementations
- `sideload` (array): Additional data to include in payloads
- `token_owner` (string): User identity for API access
- `run_after` (array): Hook URLs that must run before this one
- `description` (string): Human-readable description of what the hook does — always fill this in and keep it up to date when creating or modifying hooks
- `metadata` (object): Custom JSON (up to 4 KB)
- `settings` (object): Behavior settings (retry, timeout, queue filters)
- `secrets` (object): Sensitive credential storage — write-only (never returned by GET; declared key names are listed by `GET /hooks/{id}/secrets_keys` once values are saved). Values are entered by a human (UI Secrets editor or a direct API PATCH) or written by the hook's own code at runtime (e.g. Request Processor OAuth token caching) — always outside model context, never through MCP tools
- `secrets_schema` (object): JSON Schema declaring the hook's expected secret key names — **standard on every hook that carries secrets: never leave the platform default when creating one**. Set it via the `rossum_create_hook` / `rossum_patch_hook` MCP tools (or raw API/prd2; the UI cannot edit the schema itself). The API validates the shape (HTTP 400 otherwise): only `type`/`properties`/`additionalProperties` are accepted at the top level (no `$schema`, no `required`), every declared property must be `{"type": "string", …}` (add `minLength: 1` + a `description` by convention), and `additionalProperties` is required — either `false` (fixed credential keys; the default) or `{"type": "string"}` (open string map — required for hooks whose code writes its own secrets at runtime, e.g. Request Processor OAuth token caching). With `false`, secret writes are validated against the declared keys (undeclared keys and, with `minLength`, empty values are rejected). Under both shapes, the UI Secrets editor prefills `{"<key>": "__change_me__"}` for each declared property instead of an empty `{}` — the open map just also accepts extra keys written at runtime. Hooks created before this validation may carry other shapes — normalize before re-submitting one

### Webhook Extension

Webhooks send HTTP POST payloads to a configured URL when events occur.

**Payload validation**: HMAC-SHA256 signature via `X-Rossum-Signature` header. Verify by computing `HMAC-SHA256(secret_key, request_body)` and comparing.

**Payload includes a temporary API token** for making callbacks to the Rossum API.

```bash
# Create a webhook
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "webhook",
    "events": ["annotation.confirmed"],
    "config": {
      "url": "https://example.com/webhook",
      "timeout_s": 30
    },
    "active": true
  }' \
  'https://<domain>.rossum.app/api/v1/hooks'
```

**Example webhook payload**:
```json
{
  "event": "annotation.confirmed",
  "timestamp": "2024-01-15T10:30:00Z",
  "annotation": {
    "id": 12345,
    "url": "https://<domain>.rossum.app/api/v1/annotations/12345",
    "content": {"fields": {}}
  },
  "token": "temporary_api_token_for_webhook"
}
```

### Serverless Function Extension

Custom code executed in response to events without maintaining infrastructure. Functions receive event payloads identical to webhooks and can modify annotation data.

> **Editing rule:** When working with serverless function code locally, always edit the `.py` file next to the hook JSON. Never edit the `code` field inside the hook's `.json` file. `prd2` extracts code into `.py` files on pull and merges it back on push.

```bash
# Create a serverless function
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "function",
    "events": ["annotation.to_review"],
    "config": {
      "runtime": "python3.9",
      "code": "def handler(event, context): return {}"
    },
    "active": true
  }' \
  'https://<domain>.rossum.app/api/v1/hooks'
```

### Connector Extension

Connectors push validated data to external systems via two endpoints:
- **Validate endpoint** (`POST /validate`): Called before export; can reject invalid data
- **Save endpoint** (`POST /save`): Called after validation; HTTP 200 marks annotation as exported

Both endpoints receive POST requests with JSON annotation data matching the queue schema. The validate endpoint returns status and optional error messages.

### Hook Settings

```json
{
  "settings": {
    "retry": {"max_attempts": 3, "backoff_seconds": 60},
    "timeout_seconds": 30,
    "queue_filter": [8236, 8199]
  }
}
```

### Webhook Events

| Event | Trigger |
|-------|---------|
| `upload.created` | Document uploaded |
| `annotation.started` | Annotation begins |
| `annotation.confirmed` | User confirms data |
| `annotation.in_workflow` | Workflow processing started |
| `annotation.exported` | Export succeeds |
| `annotation.rejected` | Annotation rejected |
| `annotation.failed_export` | Export failed |
| `email.received` | Email arrives at inbox |

### Hook event triggers — decision table

Pick the right event when you create a hook, otherwise it silently won't fire.

| Event | Fires when | Use for |
|-------|------------|---------|
| `annotation_status.changed` | Annotation enters a new status (`to_review`, `reviewing`, `confirmed`, `rejected`, `exporting`, `exported`, `failed_export`, `purged`, `deleted`) | Anything keyed on workflow / status transitions: rejection notifications, per-level approval emails, single-line generator on `to_review`, post-export side effects |
| `annotation_content.initialize` | Right after OCR, before user sees the doc | Pre-fill / seed fields, default values, suggest line items |
| `annotation_content.user_update` | User edits a field in the UI | Live recalculation, formula-style cross-field updates, real-time validation messages |
| `annotation_content.updated` | Any content change incl. API/import | Same as above when you also want to react to programmatic edits |
| `annotation_content.started` | User opens an annotation for review | Show one-time info messages, lazy lookups |
| `annotation_content.export` | Just before export payload is built | Last-mile transformations on the export representation |
| `email.received` | Inbound email arrives in the inbox | Email parsing / routing hooks |
| `invocation.manual` | Operator clicks "Run" on a function hook | One-off batch / cron-style tasks (combined with a queue list) |

Common confusion: a **rejection notification belongs on `annotation_status.changed`** filtered to `to.status == "rejected"`, *not* on `annotation.rejected` (which is a Rule/Trigger event, not a hook event).

### Hook Operations Examples

```bash
# Test a hook
curl -X POST -H 'Authorization: Bearer TOKEN' \
  'https://<domain>.rossum.app/api/v1/hooks/123/test'

# Manual trigger
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -d '{"annotation_id": 12345}' \
  'https://<domain>.rossum.app/api/v1/hooks/123/manual_trigger'

# View hook logs
curl -H 'Authorization: Bearer TOKEN' \
  'https://<domain>.rossum.app/api/v1/hooks/123/logs?page_size=50'
```

---

## Connectors

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/connectors` | List connectors |
| POST | `/v1/connectors` | Create connector |
| GET | `/v1/connectors/{id}` | Retrieve connector |
| PUT | `/v1/connectors/{id}` | Update connector |
| PATCH | `/v1/connectors/{id}` | Partial update |
| DELETE | `/v1/connectors/{id}` | Delete connector |

---

## Inboxes

Email endpoints that auto-import documents into queues.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/inboxes` | List inboxes |
| POST | `/v1/inboxes` | Create inbox |
| GET | `/v1/inboxes/{id}` | Retrieve inbox |
| PUT | `/v1/inboxes/{id}` | Update inbox |
| PATCH | `/v1/inboxes/{id}` | Partial update |
| DELETE | `/v1/inboxes/{id}` | Delete inbox |

### Inbox Fields

- `name` (string): Display name
- `queue` (string): Associated queue URL
- `email` (string): Inbox email address for receiving documents
- `accepted_mime_types` (array): File format filters
- `bounce_settings` (object): Email bounce handling configuration

**Email field initialization**: Use `rir_field_names` with `"email_header:<id>"` (supported: from, to, reply-to, subject, message-id, date) to populate fields from email metadata.

**Processing**: Incoming emails are scanned for PDF, images, and ZIP archives. Small images (≤100x100 pixels) are auto-ignored.

**Email limits**: 50 MB (raw message with base64 encoding). ZIP archives: 40 MB uncompressed, max 1000 files. Only root-level or first-level directory contents extracted.

```bash
# Create inbox
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Invoice Inbox",
    "queue": "https://<domain>.rossum.app/api/v1/queues/8199",
    "accepted_mime_types": ["application/pdf", "image/*"]
  }' \
  'https://<domain>.rossum.app/api/v1/inboxes'
```

---

## Emails

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/emails` | List emails |
| GET | `/v1/emails/{id}` | Retrieve email |
| PUT | `/v1/emails/{id}` | Update email |
| PATCH | `/v1/emails/{id}` | Partial update |
| POST | `/v1/emails/import` | Import (simulate an inbound) email — async, fires the `email.received` pipeline; wrapped by the `rossum_import_email` MCP tool |
| POST | `/v1/emails/{id}/send` | Send email |
| GET | `/v1/emails/counts` | Get counts |

---

## Email Templates

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/email_templates` | List templates |
| POST | `/v1/email_templates` | Create template |
| GET | `/v1/email_templates/{id}` | Retrieve template |
| PUT | `/v1/email_templates/{id}` | Update template |
| PATCH | `/v1/email_templates/{id}` | Partial update |
| DELETE | `/v1/email_templates/{id}` | Delete template |
| POST | `/v1/email_templates/{id}/render` | Render with annotation data |

---

## Users

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/users` | List users |
| POST | `/v1/users` | Create user |
| GET | `/v1/users/{id}` | Retrieve user |
| GET | `/v1/users/me` | Current user |
| PUT | `/v1/users/{id}` | Update user |
| PATCH | `/v1/users/{id}` | Partial update (assign queues, change role via `groups`, deactivate); wrapped by the `rossum_patch_user` MCP tool — `queues`/`groups` replace the full list, not additive |
| DELETE | `/v1/users/{id}` | Delete user (not exposed as a tool — deactivate with `is_active=false` instead) |
| POST | `/v1/users/{id}/set_password` | Set password |

### User Fields

- `id` (integer): Unique identifier
- `username` (string): Login email
- `email` (string): User email address
- `first_name`, `last_name` (string): Display name
- `role` (string): User role assignment
- `groups` (array): Group memberships (organization groups)
- `is_active` (boolean): Account enabled/disabled
- `metadata` (object): Custom JSON (max 4 KB)
- `max_token_lifetime_s` (integer): Token expiration duration

Users can be auto-provisioned through SSO with roles specified in the JWT `roles` array.

### Memberships

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/memberships` | List memberships |
| POST | `/v1/memberships` | Create membership |
| GET | `/v1/memberships/{id}` | Retrieve membership |
| PUT | `/v1/memberships/{id}` | Update membership |
| PATCH | `/v1/memberships/{id}` | Partial update |
| DELETE | `/v1/memberships/{id}` | Delete membership |

Memberships control user access to workspaces and organizations.

```bash
# Create user
curl -X POST -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "user@example.com",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe"
  }' \
  'https://<domain>.rossum.app/api/v1/users'
```

---

## Rules and Triggers

### Rules

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/rules` | List rules |
| POST | `/v1/rules` | Create rule |
| GET | `/v1/rules/{id}` | Retrieve rule |
| PUT | `/v1/rules/{id}` | Update rule |
| PATCH | `/v1/rules/{id}` | Partial update |
| DELETE | `/v1/rules/{id}` | Delete rule |

**Rule actions**: Send email, update fields, change status, assign to user, add labels, trigger webhooks.

**Rule conditions**: Field value matches/contains, numerical comparisons, date ranges, AND/OR logic.

For the Rule *feature* in depth — `trigger_condition` + `actions[]`, FIRE-vs-PASS polarity, lifecycle, and the legacy Business Rules Validation extension — see the `business-rules-reference` skill. The `trigger_condition` expression language itself lives in `txscript-reference`.

### Triggers

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/triggers` | List triggers |
| POST | `/v1/triggers` | Create trigger |
| GET | `/v1/triggers/{id}` | Retrieve trigger |
| PUT | `/v1/triggers/{id}` | Update trigger |
| PATCH | `/v1/triggers/{id}` | Partial update |
| DELETE | `/v1/triggers/{id}` | Delete trigger |

**Trigger events**: `annotation.started`, `annotation.confirmed`, `annotation.rejected`, `annotation.exported`, `field.changed`, `status.changed`

---

## Approval Workflows

Approval workflows route documents through an ordered chain of decision steps before export (model: workflow → ordered `workflow_steps` → `workflow_runs`). Each step has its own approvers, condition, type, and mode.

For the verified read-only API vs. the unverified write endpoints, the real `workflow_step` fields, and the safe `prd2`-based procedure for changing a workflow, see the `approval-workflows-reference` skill.

---

## Extraction Engines

How a queue gets its AI extraction. Exactly one of three queue properties is non-null:

| Queue property | Model | Field binding mechanism |
|---|---|---|
| `generic_engine` | Pretrained generic extraction — the default; new queues auto-bind one (e.g. `/v1/generic_engines/5`) | `rir_field_names` on schema datapoints |
| `dedicated_engine` | Legacy dedicated models (see [Dedicated Engines](#dedicated-engines)) | `rir_field_names` |
| `engine` | Custom engine (`/v1/engines`) — the newer engine model; pretrained-seeded, learning-enabled | **Name match**: engine field `name` == schema datapoint `id`; `rir_field_names` must be empty |

All facts in this section were verified against a live org (2026-06-12; the `reasoning` ui-type exemption re-confirmed 2026-06-15 — an engine-bound queue carrying a `reasoning` datapoint flips successfully with no engine field for it).

### Engine entity

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/engines` | List engines |
| POST | `/v1/engines` | Create engine |
| GET/PUT/PATCH/DELETE | `/v1/engines/{id}` | Retrieve / update / delete |
| POST | `/v1/engines/{id}/duplicate` | Duplicate (requires `name` in body) |
| POST | `/v1/engines/{id}/check_template_compatibility` | Requires `name` in body (semantics unverified) |
| GET | `/v1/engines/{id}/queue_stats` | Per-queue `number_of_used_engine_fields`, `training_queue`, `prediction_queue` |

```json
{
  "id": 51877,
  "url": ".../api/v1/engines/51877",
  "organization": ".../api/v1/organizations/313278",
  "name": "Sales Orders",
  "type": "extractor",
  "learning_enabled": true,
  "training_queues": [".../api/v1/queues/2812478"],
  "description": "",
  "agenda_id": "egar_0e8c55db",
  "settings": {"use_case": "generic_ap"}
}
```

- `agenda_id` (ML-side identity) is auto-provisioned on POST — org admins can create engines via API with no extra setup.
- `settings.use_case` defaults to `"generic_ap"`.
- `learning_enabled` engines learn from confirmed annotations in `training_queues` (a list — one engine can serve and learn from multiple queues).
- Only `type: "extractor"` has been verified; other types exist but are undocumented here.

### Engine fields

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/engine_fields?engine={id}` | List (cursor-paginated) |
| POST | `/v1/engine_fields` | Create; wrapped by the `rossum_create_engine_field` MCP tool |
| GET/PUT/PATCH/DELETE | `/v1/engine_fields/{id}` | Retrieve / update / delete; PATCH/DELETE wrapped by the `rossum_patch_engine_field` / `rossum_delete_engine_field` MCP tools. `name` is immutable after creation (PATCH rejects it); DELETE 409s (`conflict_referenced`) while any schema datapoint still uses the name |
| GET | `/v1/engine_fields/pre_trained_fields` | Catalog of 75 pretrained fields: `name`, `label`, `section`, `type`, `subtype`, `tabular`, `multiline`, `description`. Header fields use plain names (`document_id`, `sender_name`, …); line-item columns use `table_column_*` names. |

```json
{
  "id": 2662411,
  "url": ".../api/v1/engine_fields/2662411",
  "engine": ".../api/v1/engines/51877",
  "name": "document_id",
  "label": "Document ID",
  "type": "string",
  "subtype": "alphanumeric",
  "pre_trained_field_id": "document_id",
  "tabular": false,
  "multiline": "false"
}
```

- `pre_trained_field_id` non-null → the field is seeded from the pretrained catalog and extracts at catalog quality from day zero. `null` → custom field; starts cold and learns from confirmed annotations.
- `tabular: true` → line-item column (datapoint inside a multivalue/tuple).
- `name` is the binding key: it must equal the schema datapoint `id` it extracts into.

### Binding rules on engine-bound queues

The API enforces these on every schema write and on the queue flip. Exact error texts (HTTP 400):

1. `Engine (id: N) restriction: extracted field 'X' must have empty rir_field_names` — applies to every engine-extracted datapoint; even `upload:`-prefixed sources are rejected.
2. `Engine (id: N) restriction: extracted field 'X' must not have disable_prediction=true`.
3. `Engine (id: N) restriction: extracted field 'X' is not present among names of engine fields` — every captured-looking datapoint must have a matching engine field. `disable_prediction: true` does NOT exempt a datapoint; only a `ui_configuration.type` of `formula`, `data`, `manual`, or `reasoning` does.
4. The multivalue container's own `rir_field_names` (e.g. `["line_items"]`) is exempt — restrictions apply to datapoints only.

Consequences:

- **Adding a captured field to an engine-bound queue: create the engine field FIRST** (`rossum_create_engine_field`), then add the schema datapoint (`rossum_patch_schema`; dry-run the edit with `rossum_validate_schema`, passing the schema id) — adding the datapoint first fails the schema write with error 3. Removing one is the mirror image: remove the datapoint first, then `rossum_delete_engine_field` — deleting the engine field first fails with HTTP 409 `conflict_referenced`.
- Validation timing: `PATCH /queues/{id}` with `engine` enumerates ALL violations at once in `non_field_errors` (wording "should"); schema writes while bound return per-field errors under `content` (wording "must").

### Converting a queue from the generic engine

Verified order of operations:

1. If the schema is shared by other queues, copy it and point this queue at the copy first.
2. `POST /v1/engines` — name it (queue name is the convention), `type: "extractor"`, `learning_enabled: true`, `training_queues: [<queue url>]`.
3. `POST /v1/engine_fields` — one per captured datapoint (header and table):
   - `name` = schema datapoint `id`, `label` = schema `label`,
   - `pre_trained_field_id` = the first `rir_field_names` entry that matches a pretrained catalog name (else `null`); additional `rir_field_names` sources have no equivalent and are dropped (tabular datapoints' `rir_field_names` already use `table_column_*` catalog names, so the lookup works directly),
   - `type`/`subtype`/`multiline` copied from the catalog entry when seeded (e.g. `date_issue`→`period_begin`, `date_due`→`period_end`, amounts→`amount`, `sender_vat_id`→`vat_number`); for custom fields use the schema datapoint type, `subtype: null`, `multiline: "false"`,
   - `tabular` = whether the datapoint sits inside a multivalue.
4. Clean the schema: every engine-extracted datapoint gets `rir_field_names: []` and explicit `ui_configuration: {"type": "captured", "edit": "enabled"}` (note: this normalizes a captured-but-read-only field from `edit: "disabled"` to `"enabled"`); remove `disable_prediction` from ALL datapoints (captured or not); keep the multivalue's own `rir_field_names`; leave `formula`/`data`/`manual`/`reasoning` fields otherwise untouched.
5. `PATCH /v1/queues/{id}` with `{"engine": "<engine url>"}` — on success the platform auto-nulls `generic_engine`.

For a brand-new queue, `POST /v1/queues` accepts `engine` directly in the creation body (live-verified 2026-06-12) — the queue is born engine-bound with `generic_engine: null`, no create-then-PATCH step needed.

### Reverting to the generic engine

Live-verified 2026-06-12: `PATCH /v1/queues/{id}` with `{"engine": null, "generic_engine": "<generic url>"}` rebinds the queue to the generic engine in one call; then restore `rir_field_names` from a pre-conversion snapshot, or map back from each engine field's `pre_trained_field_id` (tabular fields restore to their `table_column_*` catalog names). The reverted queue can be converted again afterwards without errors (round-trip verified).

### Deletion semantics

- `DELETE /v1/engines/{id}` with active queues → 400 `engine_attached_to_active_queues`.
- Queue deletion is async (202; can take up to 24 hours). Until it completes, engine deletion 400s with `engine_attached_to_queues_waiting_for_deletion` and dependent schema/engine-field deletes return 409.
- Deletion order that works: detach or delete queues → wait for async deletion → delete engine fields → engine → schema.

### Choosing an engine

Decision guide for new queues and conversion candidates.

- **Structural branch (a fact, not a quality judgment):** if any field you need extracted is missing from the pretrained catalog, the generic engine cannot emit it at all. The real choice is "custom engine vs. no extraction (formula/hook/MDH)", not "generic vs. custom". Standard doctype, full catalog coverage → generic engine, done.
- **Standard doctype + a few custom fields:** a custom engine seeded with `pre_trained_field_id` mappings keeps catalog-grade extraction on the standard fields from day zero; only the custom fields start cold and learn from confirmed annotations. The main cost is the binding discipline above, not extraction quality.
- **Existing queue:** convert when you need fields outside the catalog, or when captured-field quality has stalled despite threshold calibration (a custom engine learns from operator corrections; the generic engine does not learn org-specifically). Otherwise leave it on generic.
- **Volume heuristic (heuristic, not verified):** custom fields need a steady stream of confirmed annotations to learn; on very low-volume queues prefer generic + formulas/hooks.

---

## Dedicated Engines

Legacy custom AI models trained for specific document types or use cases. For the current custom-engine model (`/v1/engines`, name-match binding), see [Extraction Engines](#extraction-engines).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/dedicated_engines` | Create engine |
| GET | `/v1/dedicated_engines` | List engines |
| GET | `/v1/dedicated_engines/{id}` | Retrieve engine |
| PUT | `/v1/dedicated_engines/{id}` | Update engine |
| PATCH | `/v1/dedicated_engines/{id}` | Partial update |
| DELETE | `/v1/dedicated_engines/{id}` | Delete engine |

### Dedicated Engine Schemas

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/dedicated_engine_schemas/validate` | Validate schema |
| POST | `/v1/dedicated_engine_schemas/predict` | Test extraction |
| GET | `/v1/dedicated_engine_schemas` | List schemas |
| POST | `/v1/dedicated_engine_schemas` | Create schema |
| GET | `/v1/dedicated_engine_schemas/{id}` | Retrieve schema |
| PUT | `/v1/dedicated_engine_schemas/{id}` | Update schema |
| DELETE | `/v1/dedicated_engine_schemas/{id}` | Delete schema |

### Generic Engines

Pre-built extraction engines for common document types.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/generic_engines` | List engines |
| GET | `/v1/generic_engines/{id}` | Retrieve engine |
| GET | `/v1/generic_engine_schemas` | List schemas |
| GET | `/v1/generic_engine_schemas/{id}` | Retrieve schema |

---

## Labels

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/labels` | List labels |
| POST | `/v1/labels` | Create label |
| POST | `/v1/labels/apply` | Bulk add/remove labels on annotations; wrapped by the `rossum_apply_labels` MCP tool |
| GET | `/v1/labels/{id}` | Retrieve label |
| PUT | `/v1/labels/{id}` | Update label |
| PATCH | `/v1/labels/{id}` | Partial update |
| DELETE | `/v1/labels/{id}` | Delete label |

Labels can be added/removed on annotations for tagging and filtering. Bulk-tagging from a Claude session goes through `rossum_apply_labels` (add + remove in one call, many annotations at once); applying labels from inside a hook uses a raw POST to `/v1/labels/apply` (see the txscript-reference pattern). Either way the label definition must already exist — `apply` attaches, it never creates.

---

## Automation

### AI Confidence & Auto-validation

`score_threshold` on datapoints controls automatic validation. If AI confidence exceeds the threshold, the field is auto-validated. Falls back to queue's `default_score_threshold` if not set on the datapoint.

### Automation Blockers

Track reasons preventing full automation:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/automation_blockers` | List blockers |
| GET | `/v1/automation_blockers/{id}` | Retrieve blocker |

---

## Audit Logs

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/audit_logs` | List audit logs |

Records include: user, action type (create/update/delete/export), timestamp, affected object, previous/updated values, IP address, session info.

**Filtering**: date range, user, action type, object type, queue, workspace.

---

## Hook Logs

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/v1/hook_logs` | List hook execution logs |

Records include: request sent, response received, timestamp, duration, success/failure, error messages.

---

## TxScript & Formula Fields

TxScript is Rossum's Python-flavored expression language, used in schema-field formulas (datapoints of type `formula`), serverless function hooks, and native Rule `trigger_condition`s. Formula fields compute derived values automatically; serverless functions run custom logic on hook events.

> **Editing rule:** edit `formulas/<field_id>.py` (formulas) or the `.py` next to a hook's JSON (serverless) — never the `formula`/`code` field in JSON; `prd2` does the round-trip.

For the full reference — field/annotation access, helpers (`is_set`/`is_empty`/`default_to`), messaging, line-item `all_values`, the serverless `rossum_hook_request_handler` payload and `TxScript` API, formula constraints, and best practices — see the `txscript-reference` skill.

---

## Reasoning Fields

Reasoning fields are "inline LLM fields" that generate predictions based on configured prompts. Schema type: `reasoning`.

**Key characteristics**:
- Best for single-value, single-task extraction (e.g., extract country code from address)
- Aggressive caching: identical inputs produce identical outputs even when prompt changes
- Not suitable for tasks requiring high accuracy or reproducibility (use formula fields for math)
- Can be overridden from UI unless edit option is disabled
- Always validate outputs with business rules when possible

### Availability — feature-gated and separately billed

Reasoning fields require **Rossum-side organization enablement** and are **billed separately**.
They are not a schema feature you can simply start using. On an organization without the feature,
a schema PATCH adding one is rejected:

```
HTTP 400
{"content":[…,{"children":{"2":{"type":"reasoning not among date,number,enum,string,button"}}}]}
```

That error reads like a typo in the `type` value; it is not. It is the feature gate. Confirm
entitlement before designing around reasoning fields. (The rejected PATCH is atomic — the schema
is left untouched.)

### The two properties that matter

`prompt` and `context` are properties of the schema **datapoint** itself:

```jsonc
{
  "category": "datapoint",
  "id": "service_period_start",
  "label": "Service period start",
  "type": "reasoning",
  "prompt": "Return the service period start date as YYYY-MM-DD. If the document states no service period, return an empty string.",
  "context": ["field.page_text_first", "self.attr.label"]
}
```

| Property | Type | Meaning |
|---|---|---|
| `prompt` | string | The instruction. Structure it: guidelines, field logic, fallback, examples. |
| `context` | array | What the model is allowed to see. **Field references only.** |

### `context` takes field references only — there is no document context

`context` accepts TxScript-style references: **`field.<schema_id>`**, plus **`self.attr.label`**
and **`self.attr.description`**. That is the whole vocabulary.

There is **no whole-document, page-text, or attachment context.** A reasoning field cannot read
the document. This is the single most important thing to know when designing one, because it
inverts the natural approach.

**Consequence — the "park it in a field" pattern.** Any document content a reasoning field needs
must first be written into a schema field, by a hook or a formula, and that field then named in
`context`:

```
hook / formula  ──writes──>  schema field  ──named in──>  reasoning field's context
```

To park raw page text, fetch it with `GET /annotations/{id}/page_data?granularity=texts`
(see [Page data](#page-data-ocr-text-and-spatial-data)) and write it to a plain `string` field
carrying `"ui_configuration": {"type": "data"}`.

### Prefer several narrow context fields over one blob

Context entries are presented to the model **labelled by the source field's `label`**. So field
labels are prompt surface, not decoration — and splitting content across a few well-labelled
fields beats concatenating everything into one field with hand-rolled `### SECTION` markers. The
platform already provides the delimiting; hand-rolled markers just add tokens the model has to
re-parse. Name the parked fields for what they contain (`Page text — first page`), not for their
mechanism (`hook_output_1`).

### Caching changes how you iterate

Caching is aggressive: **identical inputs produce identical outputs even after you change the
prompt.** Re-running a document you have already run is therefore not a test of your new prompt —
it will hand back the previous answer and read as "my prompt edit did nothing".

To actually see a prompt change, vary the input: use a different document, or re-fire a document
whose context field content has changed. Budget for this — it is the main reason reasoning-field
prompt iteration feels slower than formula iteration.

### `edit` and `no_recalculation`

- `ui_configuration.edit` — `"enabled"` (default) lets a reviewer overwrite the generated value;
  set it to `"disabled"` to make the field read-only in the UI.
- `no_recalculation` — when set, the field is generated once and not recomputed on subsequent
  events, which pins a value a reviewer has accepted.

### Open question — hook-written context and recalculation ordering

**Unverified.** Whether a reasoning field recalculates when a **hook** writes its context field
*within the same event chain* is not established.

The known-good in-field precedent has a **formula** field as the context source. Formula
evaluation order is resolved by Rossum's dependency graph, so a formula-sourced context field is
a different — and easier — ordering question than a hook write, which lands via a separate
content operation. Do not assume the hook case behaves the same way.

If you depend on this, verify it on the target organization before committing to the design: park
the value with the hook, then confirm the reasoning field's output reflects the newly parked
content on that same run rather than on a subsequent one.

---

## Master Data Hub

The Master Data Hub (Rossum Store: "Data matching v2") matches extracted data against uploaded reference datasets (vendors, GL codes, POs, customers) using MongoDB-style queries run in sequence, writing results into **enum** schema fields. Supports `.json`/`.xml`/`.csv`/`.xlsx`, exact and fuzzy matching, result actions for zero/one/multiple matches, and cascaded configs that reference earlier matches.

For dataset CRUD and the API, the hook configuration model (MatchConfig, mapping, result actions, query cascades), query-design rules, score normalization, and worked examples, see the `mdh-reference` skill.

## Business Rules Validation

Rossum validates extracted data and blocks automation two ways: **native Rossum Rules** (the `/v1/rules` entity — `trigger_condition` + `actions[]`) and the **legacy Business Rules Validation Store extension** (a `checks[]` config with its own curly-brace expression engine). Both run at validation time to surface messages and block confirmation/automation of invalid documents.

For both implementations — the native Rule entity, actions, and FIRE-vs-PASS polarity; the legacy extension's `checks[]` config and its expression engine; and how to choose — see the `business-rules-reference` skill. (The native Rule `trigger_condition` is written in TxScript — see `txscript-reference`.)

## Duplicate Detection

Detects duplicate documents based on configurable rules. Available in Rossum Store.

**Three rule types**:
1. **Field**: Compares specific datapoint schema IDs (e.g., `document_id`)
2. **Filename**: Matches based on document filenames
3. **Relation**: Identifies duplicates through file hash

**Scope levels**: Queue, Workspace, or Organization

**Status filtering**: Rules can target documents in specific states (`to_review`, `confirmed`, `exported`, etc.)

**Matching logic**: Rules can be combined with AND/OR: `["1and2", "3"]` means "(rule 1 AND rule 2) OR rule 3"

**Actions**: When duplicates detected, can fill fields (e.g., set `is_rossum_duplicate` to true)

**Trigger events**: `annotation_content` with actions `initialize`, `started`, `user_update`, `updated`

---

## Built-in Extensions (Rossum Store)

### Copy & Paste Values *(deprecated)*
Copies values from one field to another based on conditions. Configuration uses source-to-target field mapping with conditional expressions.

### Find & Replace Values *(deprecated)*
Finds and replaces extracted values using Python `re.sub()`. Used for cleaning/normalizing data (e.g., removing non-alphanumeric characters from IBAN fields).

### Value Mapping
Maps values from one field to specific predefined values in another field.

### Line Items Grouping
Groups line items based on SQL criteria. Useful when downstream systems require one unique line item per invoice. Available as webhook extension with region-specific endpoints.

### Automation Unblocker
Unblocks specified datapoints when conditions are met. Evaluates fields and updates `validation_sources` to enable automation. Conditions: `value_existence` (non-empty value) or `single_option` (exactly one enum option + non-empty).

---

## Export Pipeline

Rossum delivers structured output two ways. The **legacy export pipeline** chains separate extensions via run-after (Custom Format Templating Purge → Custom Format Templating → REST API Export → Data Value Extractor → Export Evaluator → SFTP/S3 Export). The modern **Request Processor** consolidates these into a single JSON-configured hook and is preferred for new builds.

For the Request Processor (stages, templating, auth, response handlers, SFTP export, migration from Pipeline v1), see the `export-pipeline-reference` skill. To author the legacy Custom Format Templating (Jinja2) templates, see the `render-export-template` skill.

## SFTP & S3 Import/Export

Rossum integrates with file storage via Store extensions: **imports** ("Import Master Data From SFTP/S3", "Import Documents From SFTP/S3"; scheduled trigger) and **export** ("Export To SFTP/S3"). Configuration is JSON — credentials (host, port, auth type), import rules (dataset names, formats, regex patterns), and result actions (archive/failed directories). Endpoints are region-specific (EU1/EU2/US/JP).

For the **export** side via the Request Processor's `file-storage-export` service (credentials, export rules, filename templates), see the `export-pipeline-reference` skill. The **import** extensions are not yet covered by a dedicated pack — configure them per the overview above.

## Structured Formats Import

Structured Formats Import (SFI) processes non-visual documents (XML, JSON, e-invoices) by extracting data with XPath/JMESPath selectors and rendering a PDF for review. It runs as a webhook extension on `upload.created` and requires the relevant structured MIME types to be enabled.

For end-to-end setup, field mapping, value transformations, document splitting, PDF rendering, and production e-invoicing examples (ZUGFeRD, X-Rechnung), see the `sfi-reference` skill.

## Embedded Mode

Rossum's validation interface can be embedded in third-party applications via:
- `POST /v1/annotations/{id}/start_embedded` — Launch embedded annotation
- `POST /v1/annotations/{id}/create_embedded_url` — Generate temporary URL

Useful when out-of-the-box Rossum dashboards don't fit the use case.

---

## Sandboxes

Sandboxes enable isolated development and deployment workflows. Paid feature requiring Rossum Sales involvement.

**Tooling**: `deployment-manager` CLI (`prd2` command) from GitHub.

**Key commands**: `deploy` (source → target), `pull` (download objects locally), `push` (update local → Rossum), `init` (create project), `purge` (delete objects)

**Configuration**: `credentials.yaml` with API token, region-specific API URLs.

**Workflow**: Develop in sandbox organization → test → deploy to production via `prd2 deploy`.

---

## Integrations

Pre-built integrations available for: **SAP**, **Coupa**, **NetSuite**, **Workday**, **Microsoft Dynamics**, **Oracle**, **Xero**, **QuickBooks**.

Integration architecture supports low-code extensions, editable code, and turnkey integrations via microservices.

---

## Schema Field Templates

Common JSON templates for adding fields to Rossum schemas.

### Captured String Field

```json
{
  "rir_field_names": [],
  "constraints": {"required": false},
  "default_value": null,
  "category": "datapoint",
  "id": "FIELD_ID",
  "label": "Label",
  "hidden": false,
  "disable_prediction": false,
  "type": "string",
  "can_export": true,
  "ui_configuration": {"type": "captured", "edit": "enabled"}
}
```

### Enum Field (MDH-Matched)

```json
{
  "rir_field_names": [],
  "constraints": {"required": false},
  "score_threshold": 0.0,
  "default_value": null,
  "category": "datapoint",
  "id": "FIELD_ID",
  "label": "Label",
  "hidden": false,
  "disable_prediction": true,
  "type": "enum",
  "can_export": true,
  "ui_configuration": {"type": "data", "edit": "enabled"},
  "options": [],
  "enum_value_type": "string"
}
```

### CRITICAL: All MDH-Populated Fields Must Be Enum

**Every field populated by MDH — both `mapping.target_schema_id` and all `additional_mappings[].target_schema_id` targets — MUST use `"type": "enum"`, never `"type": "string"`.** MDH writes option lists and selected values into enum fields; a string field silently drops the value.

This includes read-only derived fields (e.g., supplier number, site code, commodity name from additional mappings). Use `"edit": "enabled"` for the primary matched field and `"edit": "disabled"` for derived fields:

```json
{
  "type": "enum",
  "options": [],
  "enum_value_type": "string",
  "score_threshold": 0,
  "disable_prediction": true,
  "ui_configuration": {"type": "data", "edit": "disabled"}
}
```

See the `mdh-reference` skill for why MDH writes enum option lists, how `mapping`/`additional_mappings` targets bind, and the `enum_value_type` choice for numeric matches.

### Formula Field

```json
{
  "rir_field_names": [],
  "constraints": {"required": false},
  "score_threshold": 0.0,
  "default_value": null,
  "category": "datapoint",
  "id": "FIELD_ID",
  "label": "Label",
  "hidden": false,
  "disable_prediction": true,
  "type": "string",
  "can_export": true,
  "ui_configuration": {"type": "formula", "edit": "disabled"},
  "formula": "field.source_field"
}
```

---

## Memorization Extension Settings

The memorization extension saves user corrections to a Data Storage collection for future automatic matching. Configuration stored in `hook.settings`:

```json
{
  "collection_name": "_collection_memorization_test",
  "datapoints_to_save": [
    {"schema_id": "natural_key_field", "is_natural_key": true},
    {"schema_id": "primary_key_field", "is_primary_key": true},
    {"schema_id": "line_item.nested_field", "alias": "flat_alias"}
  ],
  "unwind": "line_item",
  "skip_record_insert": [
    [{"schema_id": "field_id", "operator": "$eq", "value": ""}]
  ],
  "skip_automated_annotations": true
}
```

**Key fields:**
- `is_natural_key`: dedup key — the combination of all natural keys determines uniqueness
- `is_primary_key`: if this value changes for the same natural key, the record is replaced
- `unwind`: splits line items into individual records (one memorization record per line)
- `skip_record_insert`: OR of AND condition groups — skip when any group fully matches
- `skip_automated_annotations`: do not memorize corrections from fully automated annotations
- Operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`

---

## Export Mapping (Jinja2)

The legacy Custom Format Templating export step renders a Jinja2 template into the file a downstream system ingests (CSV, XML, EDI, custom JSON). Header fields are `{{ field.schema_id }}`; line items iterate with `{% for item in field.line_items %}`; standard Jinja2 conditionals and filters (`| default(0, true)`, `| tojson`, `| upper`, `| lower`) apply.

To author, render, and debug these templates against a real annotation, see the `render-export-template` skill. For the modern JSON-stage alternative, see `export-pipeline-reference`.

## Document Sorting

The document sorting extension routes documents to different queues based on field values. It watches a formula field (`document_sorting_target_queue`) and applies rules that map values to target queues:

```json
{
  "value": "17",
  "target_queue": 2582637,
  "target_status": "importing",
  "trigger_status": "to_review"
}
```

- `value`: the formula field value that triggers this rule
- `target_queue`: queue ID to move the document to
- `target_status`: status in the target queue after move (`"importing"` to re-extract, `"to_review"` to keep existing data)
- `trigger_status`: the document must be in this status for the rule to fire

**Reimport gotcha:** When `target_status` is `"importing"`, the document is re-extracted by the AI engine in the destination queue, which **resets all annotation data points** (field values, messages, etc.). To preserve field values across a reimport move, implement a Store/Restore data points hook pair: (1) a hook on the source queue saves critical field values to `annotation.metadata` before the move, and (2) a hook on the destination queue reads them back from metadata after reimport completes. Without this, any values set by formulas, matching, or manual corrections in the source queue will be lost.
