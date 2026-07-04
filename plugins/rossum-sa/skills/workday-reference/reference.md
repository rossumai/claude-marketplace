# Workday Connector — Configuration Reference

The Rossum-hosted Workday connector is a webhook service with two endpoints:

- **Export** — renders an annotation into a Workday SOAP call (any `Submit_*`-style
  operation; in practice `Resource_Management` / `Submit_Supplier_Invoice`) using a
  declarative JSON `mapping`.
- **Import** — pulls Workday reference data (any `Get_*` SOAP operation) and writes it
  into Master Data Hub datasets, on a cron schedule, with optional differential sync.

Both are configured entirely through the hook's `settings` JSON. Public baseline
documentation: [Workday](https://knowledge-base.rossum.ai/docs/workday) (landing page,
mostly a stub), [Export Configuration](https://knowledge-base.rossum.ai/docs/export-configuration-1)
and [Import Configuration](https://knowledge-base.rossum.ai/docs/import-configuration-1)
(example-level; the import page shows an outdated config dialect — see the
[dialect warning](#differential-sync-placeholders)). This pack documents the connector's
verified contract; import settings are strictly validated, so the import shapes below
are normative — the export side is looser (see the validation note under
[Hook Wiring](#hook-wiring)).

## Table of Contents

1. [Architecture at a Glance](#architecture-at-a-glance)
2. [Hook Wiring](#hook-wiring)
3. [Credentials and Secrets](#credentials-and-secrets)
4. [The `wsdl` Block](#the-wsdl-block)
5. [Export Configuration](#export-configuration)
6. [The Mapping DSL](#the-mapping-dsl)
7. [Reference Wrappers, Worktags, WIDs](#reference-wrappers-worktags-wids)
8. [Attachments](#attachments)
9. [Goods vs Service Lines](#goods-vs-service-lines)
10. [Header-Level Patterns](#header-level-patterns)
11. [Export Responses and Errors](#export-responses-and-errors)
12. [Import Configuration](#import-configuration)
13. [Regions and Addressing](#regions-and-addressing)
14. [What the Connector Does NOT Do](#what-the-connector-does-not-do)
15. [Gotchas and Troubleshooting](#gotchas-and-troubleshooting)

## Architecture at a Glance

```
Rossum annotation ──annotation_content.export──▶  svc/workday /api/v1/export
                                                        │  first matching configuration
                                                        │  renders mapping → SOAP
                                                        ▼
                                               Workday web services
                                               (Submit_Supplier_Invoice)

Rossum cron ──invocation.scheduled──▶  svc/workday /api/v1/import   (HTTP 202)
                                             │  one background job per configuration
                                             │  Get_* SOAP → extraction_jmespath
                                             ▼
                                      MDH datasets (workday_*)
```

The connector speaks **SOAP** toward Workday (WS-Security username/password), pinned to a
WSDL version you choose per configuration. Rossum-side it is a plain webhook: the export
fires when an annotation is exported/confirmed and responds synchronously with messages;
the import fires on schedule or manual invocation, immediately acknowledges (HTTP 202),
and runs each configuration as a background job. Matching between the two halves happens
in your schema: the import fills MDH datasets, MDH matching stamps Workday reference IDs
(`supplier_wd`, `entity_wd`, `item_*_wd`, …) onto the annotation, and the export mapping
reads those fields via `@{schema_id}`.

## Hook Wiring

Both endpoints are **webhooks** (`type: webhook`), not serverless functions. Create the
hook via API or prd2 and point `config.url` at the org's own base URL:

| | Export | Import |
|---|---|---|
| `config.url` | `https://<org base>/svc/workday/api/v1/export` | `https://<org base>/svc/workday/api/v1/import` |
| `events` | `["annotation_content.export"]` | `["invocation.scheduled", "invocation.manual"]` |
| `config.schedule.cron` | — | e.g. `*/10 * * * *` (see [scheduling](#scheduling-patterns)) |
| `token_owner` | **required** | **required** |
| `settings` | `{configurations: [...]}` | `{configurations: [...], job_run_settings?}` |
| `secrets` | `workday_username`, `workday_password` | `workday_username`, `workday_password` |

- `<org base>` is the organization's own Rossum domain (e.g. `https://acme.rossum.app` or
  `https://us.app.rossum.ai`) — the same base the org's API lives under. See
  [Regions](#regions-and-addressing).
- `token_owner` must point at a (service) user whose token the connector uses to call the
  Rossum API back (fetching document content) and whose identity authorizes the call — a
  missing `token_owner` is a setup error. No `config.secret` (shared secret) is needed.
- Standard webhook knobs (`timeout_s`, `retry_count`, `payload_logging_enabled`, …) apply
  as for any Rossum webhook. Note the export responds only after the Workday round-trip;
  set `timeout_s` generously (large attachments make `Submit_Supplier_Invoice` slow).
- Export hooks are typically chained with `run_after` behind the hooks that compute
  export-ready fields (supplemental formula/matching hooks), and bound to the queues they
  serve via the hook's `queues` list.

**Validation strictness differs per side.** Import settings are strict end to end —
misspelled or unknown keys are rejected rather than silently ignored. On the export
side, only the `request` and `wsdl` blocks are strict; unknown keys at the settings and
configuration-entry level are **silently ignored**. That includes the `debug: true`
shown in the KB export example (no effect) — and it bites harder than it sounds:
mistype `queues` as `queue` and the typo is dropped, `queues` stays unset, and the entry
applies to **every queue** (see configuration selection below). Double-check export
top-level key names by hand.

## Credentials and Secrets

Workday credentials go in the hook's **`secrets`** object — never in `settings`. The
exact keys (both endpoints):

```json
{
  "workday_username": "ISU_Rossum",
  "workday_password": "…"
}
```

- The connector authenticates to Workday with WS-Security as `<username>@<tenant>` —
  supply the bare integration username; the tenant from the `wsdl` block is appended
  automatically. Do not include `@tenant` in the secret.
- Workday side, use a dedicated **Integration System User (ISU)** with web-service
  permissions granted for exactly the operations you call. Workday authorizes
  operation-by-operation: a user that can `Get_Suppliers` may still get a
  "task not authorized"-style SOAP fault on `Submit_Supplier_Invoice`.
- Workday tenants commonly restrict API access by IP allowlist; authentication failures
  with a correct username/password can mean the connector's egress IPs are not
  allowlisted on the tenant.
- Updating hook secrets merges keys (a PATCH with one key does not drop the other).

When creating the hooks, also set `secrets_schema` (via the `rossum_create_hook` /
`rossum_patch_hook` MCP tools, raw API, or prd2 — the UI cannot edit the schema itself)
so the Secrets editor presents the two expected keys as `__change_me__` placeholders
instead of an empty `{}`. The closed `additionalProperties: false` shape is the right
one here: the connector only ever reads these two credentials and never writes secrets
of its own at runtime (hooks that cache tokens into their own secrets need the open
`"additionalProperties": {"type": "string"}` shape instead — see `rossum-reference` →
Hook Object Fields):

```json
"secrets_schema": {
  "type": "object",
  "properties": {
    "workday_username": { "type": "string", "minLength": 1, "description": "Workday ISU username (without @tenant)" },
    "workday_password": { "type": "string", "minLength": 1, "description": "Workday ISU password" }
  },
  "additionalProperties": false
}
```

## The `wsdl` Block

Every configuration entry (export and import) starts with:

```json
"wsdl": {
  "domain": "wd3-impl-services1.workday.com",
  "tenant": "acme_dpt1",
  "api_version": "v42.2"
}
```

- `domain` — the Workday **web-services host**, not the browser URL.
- `tenant` — the Workday tenant ID (the path segment in the tenant's login URL).
- `api_version` — the SOAP API version the WSDL is pinned to. Versions from `v39.1` to
  `v44.0` are in production use; pick the newest version the tenant supports and keep it
  consistent across export and import so records and references line up.
- An optional `protocol` key exists (default `https`) — leave it alone.

The connector derives the WSDL URL as
`https://<domain>/ccx/service/<tenant>/<service>/<api_version>?wsdl` — if you have a
Workday connection string in that shape, you can read all four values straight out of it.

### Implementation vs production domains (the classic cut-over gotcha)

Workday **implementation** (test) tenants and **production** tenants live on different
hosts *and* have different tenant IDs:

| | Host pattern | Example tenant |
|---|---|---|
| Implementation / sandbox | `wd<N>-impl-services1.workday.com` | `acme_dpt1` |
| Production | `wd<N>-services1.myworkday.com` (also seen bare: `services1.myworkday.com`) | `acme` |

The KB examples use `wd3-impl-services1.workday.com` — an implementation cell. At go-live
you must change **both** `domain` and `tenant` in every configuration entry (export and
all imports), and the ISU + IP allowlist must exist on the production tenant. Forgetting
one of these is the most common cause of "worked in UAT, fails in prod".

## Export Configuration

Shape of the export hook's `settings`
([KB: Export Configuration](https://knowledge-base.rossum.ai/docs/export-configuration-1)):

```json
{
  "configurations": [
    {
      "queues": [123456],
      "excluded_queues": [],
      "wsdl": { "domain": "…", "tenant": "…", "api_version": "v42.2" },
      "request": {
        "mapping": { "Add_Only": true, "Supplier_Invoice_Data": { … } },
        "service": "Resource_Management",
        "operation": "Submit_Supplier_Invoice"
      }
    }
  ]
}
```

- **Configuration selection**: for each exported annotation, the connector picks the
  **first** configuration in the list applicable to the annotation's queue — `queues`
  absent or `null` means "any queue"; `excluded_queues` subtracts. Only that one
  configuration executes. If none matches, the export is a **silent no-op** (no message,
  no error) — a misrouted queue fails quietly, so keep queue lists tight.
- `request.service` + `request.operation` name the SOAP service and operation. Any
  submit-style operation works; AP invoice export uses `Resource_Management` /
  `Submit_Supplier_Invoice` in every known configuration.
- `request.mapping` is a JSON template of the SOAP request body: element names are the
  Workday API's element names (`Supplier_Invoice_Data`, `Invoice_Line_Replacement_Data`,
  …), values are literals or DSL expressions (next section). Consult the Workday SOAP API
  documentation for the element vocabulary; the connector renders what you write.
- The mapping's template syntax is validated when the hook payload is parsed — a syntax
  error (unknown operation name, malformed arguments) fails the export immediately with
  an explanatory message.

## The Mapping DSL

The mapping language has three layers: **literals**, **`{…}` resource placeholders**
(Jinja-style), and **`$OPERATION$` blocks** with the `@{…}` shorthand. Everything not
recognized as one of these passes through unchanged.

### Literals

Any JSON literal is emitted as-is: strings, numbers, and real JSON booleans
(`"Add_Only": true`, `"Submit": true`, `"Line_Order": "10000001"`).

### `{…}` resource placeholders

Single-brace strings are rendered as Jinja-style template expressions (sandboxed) over
connector-provided resources. On export two resources are available:

- `{current_datetime}` — the render-time timestamp (e.g. for `Invoice_Received_Date`).
- `{document_content}` — the annotated document's file content, fetched from Rossum with
  the hook's token. Used as `File_Content` inside `Attachment_Data`; the SOAP layer
  transmits it base64-encoded.

Jinja expression syntax works inside the braces (filters, attribute access), and native
types are preserved. Referencing an undefined resource fails the render. Inside
`$FOR_EACH_SCHEMA_ID$` one extra variable is available: `{schema_loop.index}` /
`{schema_loop.index0}` — the current row number (1- and 0-based), handy for `Line_Order`.

### `@{schema_id}` — datapoint value shorthand

`"Invoice_Date": "@{date_issue}"` is shorthand for the `$DATAPOINT_VALUE$` operation:

```json
"Invoice_Date": { "$DATAPOINT_VALUE$": { "schema_id": "date_issue" } }
```

Semantics (shared by every schema-id-based operation below):

- **Scoping**: the value is looked up in the *current context* first — inside
  `$FOR_EACH_SCHEMA_ID$` that is the current row — and falls back to the whole
  annotation content. A row-level lookup can therefore reference header fields, but not
  vice versa.
- **Missing datapoint**: if the schema id exists nowhere in the annotation content, the
  render fails and the export returns an error message naming the schema id. A
  *present-but-empty* field renders as an empty value — wrap optional elements in
  `$IF_SCHEMA_ID$` instead of sending empties.
- The long form takes an optional `value_type`: `string` (default), `integer`, `float`,
  `boolean`, `iso_datetime`. Unconvertible values fail the export with a message.
  Caution: `boolean` reflects truthiness of the raw string — a field containing the text
  `"false"` still converts to `true`; put real booleans in the template or prepare a
  formula field instead.
- All value shaping beyond these conversions (date reformatting, sign flips,
  concatenation, line grouping) belongs in formula fields upstream — the DSL substitutes
  and dispatches, it does not compute.

### `$IF_SCHEMA_ID$` — conditional block

```json
"Default_Tax_Option_Reference": {
  "$IF_SCHEMA_ID$": {
    "mapping":          { "ID": [ { "type": "Tax_Option_ID", "_value_1": "@{tax_option_id}" } ] },
    "schema_id":        "tax_option_id",
    "fallback_mapping": {}
  }
}
```

Exact truth table:

| Field with `schema_id` | Result |
|---|---|
| Found once, non-empty value | `mapping` is rendered — with the context narrowed to that element |
| Found once, empty value | `fallback_mapping` |
| Not found at all | `fallback_mapping` |
| No `fallback_mapping` given | the surrounding key is dropped entirely |
| Found **more than once** | render error (fails the export) — don't gate on a line-item column from header level |

Verified usage patterns:

| Pattern | Effect |
|---|---|
| `"fallback_mapping": {}` | Emit an empty object when the field is empty — **the** way to make a Workday reference optional (sending a reference wrapper with an empty value is rejected by the tenant). |
| No `fallback_mapping` key | The surrounding key vanishes (used for optional `Additional_Fields_Data_Reference` entries and optional list members). |
| Scalar mappings | `"mapping": "@{tax_amount_corrected}", "fallback_mapping": "@{amount_total_tax}"` — value-level fallback chain between two fields. |
| Literal mappings | `"mapping": true, "fallback_mapping": false` — turn a field's non-emptiness into a boolean element (e.g. an on-hold flag). |

### `$IF_DATAPOINT_VALUE$` — conditional on a specific value

```json
"On_Hold": {
  "$IF_DATAPOINT_VALUE$": {
    "schema_id": "document_type",
    "value": "credit_note",
    "mapping": true,
    "fallback_mapping": false
  }
}
```

Renders `mapping` when the field equals `value`, else `fallback_mapping`; with no
`fallback_mapping` the surrounding key is dropped. Unlike `$IF_SCHEMA_ID$`, the field
**must** exist exactly once — missing or duplicated schema ids fail the render.

### `$FOR_EACH_SCHEMA_ID$` — multivalue iteration

```json
"Invoice_Line_Replacement_Data": {
  "$FOR_EACH_SCHEMA_ID$": {
    "mapping": {
      "Line_Order":       "{schema_loop.index}",
      "Item_Description": "@{item_description}",
      "Extended_Amount":  "@{item_total_base}"
    },
    "schema_id": "line_item",
    "fallback_mapping": []
  }
}
```

Repeats `mapping` once per element with the given schema id (each row becomes the
current context; renders to a JSON array). `fallback_mapping` (default `[]`) renders when
**no** element exists — use it to emit an artificial single line for documents captured
without a line table. Two production uses:

- **Invoice lines** — iterate `line_item` (or a grouped/aggregated shadow table produced
  by a formula hook) into `Invoice_Line_Replacement_Data`.
- **Attachments** — iterate an attachment multivalue into `Attachment_Data`, pairing
  per-row filename/content-type fields with `$FETCH_DOCUMENT_CONTENT$`.

### `$DATAPOINT_MAPPING$` — value switch

```json
"Quantity": {
  "$DATAPOINT_MAPPING$": {
    "mapping": { "<ORDER_TYPE_GOODS_ID>": "@{item_quantity}" },
    "schema_id": "item_order_type_wd",
    "fallback_mapping": null
  }
}
```

Dispatches on the **value** of the field `schema_id` (current-context first, then whole
annotation — so a row-level switch can key off a header field):

- A key matching the value → that key's projection (scalar or object) is rendered.
- Composite keys route several values to one branch: `"goods|stock"` matches either
  (first definition wins on conflicts).
- No matching key → `fallback_mapping` (default `null`, which the SOAP layer omits — so
  omit-on-miss is the default behavior).

Verified uses: the [goods-vs-service line switch](#goods-vs-service-lines), and
header-level document-type dispatch (e.g. a dedicated `Payment_Terms_Reference` only for
`credit_note`). Not documented in the KB.

### `$FETCH_DOCUMENT_CONTENT$` — fetch a file by URL field

```json
"File_Content": {
  "$FETCH_DOCUMENT_CONTENT$": { "datapoint": "attachment_content_url" }
}
```

Reads a URL from the field named by `datapoint`, downloads it **authenticated with the
hook's Rossum token**, and emits the raw content (base64-encoded on the wire by the SOAP
layer). The URL must therefore be a Rossum API document-content URL the token owner can
read. Used inside a `$FOR_EACH_SCHEMA_ID$` over an attachments multivalue to ship
*multiple* documents — where `{document_content}` can only ship the one annotated
document. Not documented in the KB.

### `$CHILD_COUNT$` — count rows

```json
"Line_Count": { "$CHILD_COUNT$": { "schema_id": "line_items" } }
```

Renders the number of child elements under a non-datapoint element (e.g. rows of a
multivalue). Fails the render if the element is missing or is a plain datapoint.

## Reference Wrappers, Worktags, WIDs

Workday SOAP references follow one wrapper convention everywhere:

```json
"Supplier_Reference": {
  "ID": [ { "type": "Supplier_ID", "_value_1": "@{supplier_wd}" } ]
}
```

- `type` is a **reference-ID type** and is tenant configuration: `Supplier_ID`,
  `Organization_Reference_ID`, `Currency_ID`, `Tax_Applicability_ID`,
  `Spend_Category_ID`, `Cost_Center_Reference_ID`, `Project_ID`, … Verify the exact type
  names against the target tenant; two tenants can key the same object differently.
- `_value_1` is the reference value — the ID your matching stamped onto the annotation
  (values come from the imported datasets, which is why import and export must agree on
  reference-ID types).
- Every Workday object also has a tenant-independent **WID**; `{"type": "WID",
  "_value_1": …}` works wherever a reference is accepted, at the cost of readability.
- **PO line references** add parent coordinates to the same wrapper:

  ```json
  "Purchase_Order_Line_Reference": {
    "ID": [ {
      "type": "Line_Number",       "_value_1": "@{item_order_line_nr_wd}",
      "parent_type": "Document_Number", "parent_id": "@{item_document_number_po_wd}"
    } ]
  }
  ```

- **Worktags** are a JSON array of such wrappers, one per worktag type, each usually
  guarded so empty coding is omitted rather than sent:

  ```json
  "Worktags_Reference": [
    { "$IF_SCHEMA_ID$": { "mapping": { "ID": [ { "type": "Cost_Center_Reference_ID", "_value_1": "@{item_cost_center_wd}" } ] },
                          "schema_id": "item_cost_center_wd", "fallback_mapping": {} } },
    { "$IF_SCHEMA_ID$": { "mapping": { "ID": [ { "type": "Project_ID", "_value_1": "@{item_project_wd}" } ] },
                          "schema_id": "item_project_wd", "fallback_mapping": {} } }
  ]
  ```

  Extend with further guarded entries (organization, custom organization/product,
  withholding tax, affiliate company, …) as the tenant's coding model requires.

## Attachments

```json
"Attachment_Data": [
  {
    "Encoding": "base64",
    "Filename": "@{file_name}",
    "Content_Type": "application/pdf",
    "File_Content": "{document_content}"
  }
]
```

- The static-array form above ships the annotated document itself. `Filename` is
  typically fed from a formula field carrying `document.original_file_name` (populate it
  in a supplemental hook); a literal filename also works.
- For **multiple attachments**, replace the static array with a `$FOR_EACH_SCHEMA_ID$`
  over an attachments multivalue whose rows carry name, content type, and a Rossum
  document-content URL, and use `$FETCH_DOCUMENT_CONTENT$` for `File_Content`.
- Base64 encoding inflates content by ~33% — a 30 MB PDF travels as ~40 MB of payload.
- The connector imposes no explicit size cap of its own; practical ceilings are the
  Workday tenant's attachment limits and total processing time (the connector allows a
  Workday call up to 15 minutes, but the Rossum webhook `timeout_s` is far shorter — a
  very slow submit can time out platform-side *after* the invoice was created in
  Workday). Test the real document-size profile before promising large-attachment
  support; `Add_Only` plus Workday's duplicate checks are the guard against
  timeout-then-retry double submission.

## Goods vs Service Lines

Workday supplier-invoice lines come in two shapes, mirroring the PO line model:

| Line type | Amount carried by | Elements to send |
|---|---|---|
| **Goods** (quantity-based) | `Quantity` × `Unit_Cost` | `Quantity`, `Unit_Cost` |
| **Service** (amount-based) | `Extended_Amount` | `Extended_Amount` |

A PO-backed invoice must project each line the way its PO line is typed — sending
`Extended_Amount` against a goods line (or quantity math against a service line) causes
tenant-side validation or mis-posting. Since one invoice can mix both, the projection
must switch **per line**, keyed by an order-type field your PO-line matching stamps onto
the row:

```json
"Quantity":        { "$DATAPOINT_MAPPING$": { "mapping": { "<GOODS_TYPE_ID>": "@{item_quantity}" },
                                              "schema_id": "item_order_type_wd" } },
"Unit_Cost":       { "$DATAPOINT_MAPPING$": { "mapping": { "<GOODS_TYPE_ID>": "@{item_unit_price}" },
                                              "schema_id": "item_order_type_wd" } },
"Extended_Amount": { "$DATAPOINT_MAPPING$": { "mapping": { "<SERVICE_TYPE_ID>": "@{item_total_base}" },
                                              "schema_id": "item_order_type_wd" } }
```

`<GOODS_TYPE_ID>` / `<SERVICE_TYPE_ID>` are the tenant's order-type reference IDs — read
them from the imported PO data. The unmatched element renders `null` and is omitted from
the SOAP request, which is exactly the switch behavior needed. Non-PO (amount-coded)
invoices side-step the switch and send `Extended_Amount` only. The same duality shows up
in Workday's REST procurement API (`goodsLines` vs `serviceLines`) if you build the
live-consumption workaround described [below](#what-the-connector-does-not-do).

## Header-Level Patterns

Verified conventions worth copying:

- `"Add_Only": true` — sibling of `Supplier_Invoice_Data`; standard for create-only
  submission.
- `"Submit": true` submits the invoice into the tenant's business process;
  `"Submit": "@{submit_flag}"` lets a formula field hold selected invoices in draft
  (mind the `boolean` conversion caveat above — emit a real boolean from the formula).
- `Business_Process_Parameters` (e.g. `{"Auto_Complete": false}`) controls business
  process behavior on the Workday side.
- `Control_Amount_Total` (`@{amount_total}`) makes Workday validate the line sum against
  the header total — cheap insurance against projection bugs.
- Header extras map to `Additional_Fields_Data_Reference` — an array of
  `$IF_SCHEMA_ID$`-guarded `{Attribute_Value, Configurable_Attribute_Reference}` entries;
  attribute names (`Configurable Text Attribute 01`, …) are tenant configuration.
- Credit notes: dispatch header elements on `document_type` via `$DATAPOINT_MAPPING$`
  (e.g. a dedicated `Payment_Terms_Reference`) and flip line signs field-side.
- `External_Supplier_Invoice_Source_Reference` tags the submission's source system where
  the tenant tracks one.

## Export Responses and Errors

The export returns HTTP 200 with `messages` that Rossum shows on the annotation — for
errors too, so the platform does not blind-retry a failed mapping. Message vocabulary:

| Message | Meaning |
|---|---|
| `Export to Workday successful. Created new supplier invoice with ID: <id>.` | Success; the ID is parsed from the tenant's response (for `Submit_Supplier_Invoice`). |
| `Export to Workday successful. Unable to retrieve new object info` | The submit succeeded but the operation is one the connector cannot extract an ID from — harmless. |
| `Export to Workday failed. Mapping error: …` | Template rendering failed — missing datapoint (named in the message), failed type conversion, missing `document.content`, or a mis-used operation. |
| `Export to Workday failed. Error: …` | The Workday call itself failed — SOAP fault (permissions, validation), element rendering, or connectivity. |
| `Could not connect to Workday. Check domain configuration.` | Wrong `wsdl.domain` / network. |
| `Service not found. Check wsdl configuration.` | Wrong service name or API version (tenant returned 410 for the WSDL). |
| `Internal Workday server error. Check the connection string (tenant, service and operation) or the request payload.` | Tenant returned 500 while fetching the WSDL. |

The connector does **not** retry Workday calls on export; retries are governed by the
hook's standard webhook retry settings (and since errors come back as 200-with-message,
they do not trigger platform retries).

## Import Configuration

The import endpoint fills MDH datasets from Workday `Get_*` operations. Shape of the
import hook's `settings`:

```json
{
  "configurations": [
    {
      "wsdl": { "domain": "…", "tenant": "…", "api_version": "v42.2" },
      "method": "update",
      "id_keys": ["Supplier_ID"],
      "request": {
        "payload": {
          "Response_Group": { "Include_Attachment_Data": false },
          "Response_Filter": { "Count": 999 },
          "Request_Criteria": {
            "Updated_To_Date":   "${current_datetime}",
            "Updated_From_Date": "${last_modified_date}"
          }
        },
        "service": "Resource_Management",
        "operation": "Get_Suppliers"
      },
      "response": { "extraction_jmespath": "Supplier[].Supplier_Data" },
      "dataset_name": "workday_suppliers"
    }
  ],
  "job_run_settings": { "max_run_time_s": 36000 }
}
```

Per-entry keys (strictly validated — unknown keys are rejected):

| Key | Meaning |
|---|---|
| `wsdl` | Tenant coordinates, as for export. |
| `request.payload` | Body of the `Get_*` call. Exactly four top-level keys are accepted: `Request_References`, `Request_Criteria`, `Response_Group`, and `Response_Filter` (whose only fields are `Count`, `Page`, `As_Of_Effective_Date`, `As_Of_Entry_DateTime`). Keep supplier/PO payloads lean with `Response_Group.Include_Attachment_Data: false`. |
| `request.service` / `operation` | The SOAP service and `Get_*` operation. |
| `response.extraction_jmespath` | JMESPath applied to each response page; must yield a **list of objects** (each becomes one dataset record). Default `*[]`. |
| `dataset_name` | Target **MDH dataset**. Convention: `workday_<object>`, with an environment suffix for test-tenant data (e.g. `workday_suppliers_sandbox`) when one org hosts both tenants' data. |
| `method` | `replace` — rebuild the dataset from scratch each run; `update` — upsert records by `id_keys`. |
| `id_keys` | Required with `method: update` (a **list**, even for one key): path(s) to the record's identity **relative to each extracted record** (e.g. `["Supplier_ID"]`, or `["Purchase_Order_Data.0.Document_Number"]` when the jmespath extracts a wrapper element). |

Execution model:

- The endpoint acknowledges immediately (HTTP 202); **each entry** in `configurations[]`
  runs as an independent background job (one hook can refresh entities, cost centers,
  projects, tax objects, … in one scheduled run). Failures surface in the hook's logs,
  not on any annotation.
- Job deduplication **short-circuits the whole invocation**: if an entry's job is
  already queued (e.g. the previous cron tick is still waiting), the handler stops
  there — that entry *and every entry after it* in `configurations[]` is silently not
  enqueued for this run. Put the most critical datasets first, and don't schedule a
  multi-entry hook faster than its slowest job drains.
- Independently, two runs writing the **same dataset** cannot overlap — the later one
  fails with an error telling you to space out the cron schedule.
- A `replace` rebuilds the dataset **in place** (not an atomic swap): during a long
  replace run, matching sees partial data. Schedule big replaces off-hours — the
  weekend-full-rebuild pattern below.
- **Paging is automatic**: all pages are fetched (page size `Response_Filter.Count`,
  default 999, the maximum). Setting `Response_Filter.Page` pins the import to that
  single page — a debugging aid, not something to leave in production.
- `job_run_settings` is accepted and shape-validated (`retries` 0–10, `max_run_time_s`
  60–36000 s, `valid_for_s` 300–172800 s) but is currently **inert** — the import runs
  with fixed job parameters (one retry, a fixed multi-hour run cap, ~1-day expiry)
  regardless of what you set. Don't tune it expecting effect; initial full loads of
  large tenants simply take hours within the fixed cap.

### Differential sync placeholders

Inside `request.payload` string values, two `${…}` placeholders (dollar + braces —
distinct from the export-side `{…}` resources) enable incremental sync:

- `${current_datetime}` — replaced with the run's timestamp (ISO-8601, UTC); use as the
  window end (`Updated_To_Date` / `Item_Updated_To`).
- `${last_modified_date}` — replaced with the start time of the **last successful import
  into that dataset, minus a 24-hour safety buffer** (ISO-8601, UTC); use as the window
  start (`Updated_From_Date` / `Item_Updated_From`). The built-in overlap means records
  are re-fetched for a day — harmless, because `update` upserts by `id_keys`.

Differential entries use `method: update` + `id_keys`; pair them with an occasional
`method: replace` full-rebuild job (e.g. weekends) so deletions in Workday — which a
differential window can never observe — do not accumulate in the dataset forever.

> **KB dialect warning:** the public
> [Import Configuration](https://knowledge-base.rossum.ai/docs/import-configuration-1)
> page shows an older key set — `ds_collection_name`, `request.replication.id_key_name` +
> `differential_replication`, and single-brace `{current_datetime}` /
> `{last_successful_import}` placeholders. The connector's strict settings validation
> **rejects** that dialect today; single-brace placeholders are likewise not substituted
> on import. Use the form documented here; if you inherit a config in the KB dialect, it
> needs migrating, not copying.

### Operations observed in production

| Operation | Service | Typical `extraction_jmespath` | Dataset |
|---|---|---|---|
| `Get_Suppliers` | Resource_Management | `Supplier[].Supplier_Data` | `workday_suppliers` |
| `Get_Purchase_Orders` | Resource_Management | `Purchase_Order[].Purchase_Order_Data[]` (or `Purchase_Order[]` with wrapper-relative `id_keys`) | `workday_purchase_order` |
| `Get_Purchase_Items` | Resource_Management | `Purchase_Item[].Purchase_Item_Data` | `workday_purchase_item` |
| `Get_Projects` | Resource_Management | `Project[].Project_Data` | `workday_project` |
| `Get_Invoice_Types` | Resource_Management | `Invoice_Type[].Invoice_Type_Data[]` | `workday_invoice_type` |
| `Get_Resource_Categories` | Resource_Management | `Resource_Category[].Resource_Category_Data` | `workday_spend_category` |
| `Get_Company_Organizations` | Financial_Management | `Company_Organization[].Company_Organization_Data[]` | `workday_entity` |
| `Get_Organizations` | Financial_Management | `Organization[].Organization_Data[]` | `workday_organizations` |
| `Get_Cost_Centers` | Financial_Management | `Cost_Center[].Cost_Center_Data` | `workday_cost_center` |
| `Get_Tax_Applicabilities` | Financial_Management | `Tax_Applicability[].Tax_Applicability_Data` | `workday_tax_applicability` |
| `Get_Transaction_Tax_Rates` | Financial_Management | `Tax_Rate[].Tax_Rate_Data` | `workday_tax_rates` |
| `Get_Transaction_Tax_Codes` | Financial_Management | `Tax_Code[].Tax_Code_Data` | `workday_tax_codes` |
| `Get_Currency_Conversion_Rates` | Financial_Management | `Currency_Conversion_Rate[].Currency_Conversion_Rate_Data` | `workday_currency_conversion_rate` |

Any `Get_*` operation the ISU is authorized for follows the same pattern; the Workday
SOAP API docs define the request/response vocabulary.

### Scheduling patterns

Verified cadences, matched to how fast each object changes and how it is synced:

| Data | Cadence | Method |
|---|---|---|
| Purchase orders | every 10 min | differential `update` |
| Suppliers, purchase items | hourly differential | `update` |
| Entities, cost centers, tax objects, invoice types | daily or every few hours | `replace` |
| Purchase items full rebuild | weekly (weekend) | `replace` |
| FX conversion rates | monthly | `replace` |

Plus an event-less `invocation.manual`-only hook as an ad-hoc re-sync button.

## Regions and Addressing

Address the connector **relative to the org's own base URL**
(`https://<org base>/svc/workday/api/v1/…`). As of mid-2026 the connector is available in
the EU1 (`elis.rossum.ai` / `*.app.rossum.ai`), EU2 (`*.rossum.app`), and US
(`us.app.rossum.ai`) environments; it is **not** available in the Japan environment. The
KB landing page's endpoint table (EU1 only) is stale — but for a region not listed here,
confirm availability with the connector team before contracting it.

## What the Connector Does NOT Do

- **No live PO consumption.** The imported PO data is a snapshot: SOAP
  `Get_Purchase_Orders` does not expose per-line *received / already-invoiced* quantities
  and amounts, so GRN-style and over-invoicing checks against connector-imported data can
  only be as fresh as the last sync — and never see consumption at all. The established
  workaround is a custom validation hook that calls Workday's **REST procurement API**
  per PO at annotation-open time (OAuth client + refresh token; look up the PO by its
  Workday internal ID, read the goods/service line consumption fields, write them onto
  matched rows for business rules to check). That hook is a separate build — see
  `export-pipeline-reference` / `txscript-reference`; it is not part of this connector.
- **No supplier creation or master-data write-back.** The connector's export submits
  invoices (submit-style operations); it has no supplier-onboarding or master-data-update
  flows. If a project needs supplier creation from Rossum, treat it as custom scope and
  confirm feasibility with the connector team.
- **No document import from Workday.** The import side fills MDH datasets with reference
  data; it does not pull documents/attachments from Workday into Rossum queues.
- **No transformation language.** The mapping DSL substitutes, guards, iterates, and
  dispatches — it does not compute. All value shaping (dates, signs, concatenations,
  grouping lines) belongs in formula fields or supplemental hooks upstream of the export.
- **At most one export configuration fires per annotation** (first match by queue) — one
  annotation cannot fan out to several Workday tenants/operations from a single hook.

## Gotchas and Troubleshooting

| Symptom | Likely cause |
|---|---|
| Worked in UAT, fails after go-live | `wsdl.domain`/`tenant` still point at the implementation tenant (see [domains](#implementation-vs-production-domains-the-classic-cut-over-gotcha)); or ISU/IP allowlist missing on production |
| Export does nothing — no message at all | No configuration matched the annotation's queue (`queues`/`excluded_queues`); selection is first-match and no-match is silent |
| An export entry fires on queues it shouldn't | Mistyped export top-level key (e.g. `queue` instead of `queues`) — export settings silently ignore unknown keys, so `queues` stays unset and the entry matches every queue |
| `Mapping error: Datapoint with schema_id '…' not found` | The mapping references a field absent from that queue's schema — fix the schema id or guard with `$IF_SCHEMA_ID$` |
| `Mapping error: Cannot convert value … ` | `value_type` conversion failed (bad date/number in the field) |
| SOAP fault "task not authorized" | ISU lacks the domain/web-service permission for that specific operation |
| Authentication failure with correct credentials | Password rotated, connector egress IPs not on the tenant allowlist — or the secret contains `user@tenant` (the tenant is appended automatically) |
| Tenant rejects an empty reference | An optional reference was emitted empty — wrap it in `$IF_SCHEMA_ID$` with `"fallback_mapping": {}` |
| `IF_SCHEMA_ID … expects to find only 1 element` | The guard targets a schema id that occurs multiple times (line column gated from header level) — move the guard inside `$FOR_EACH_SCHEMA_ID$` |
| Lines mis-posted / rejected on PO-backed invoices | Goods/service projection not switched per line (see [Goods vs Service Lines](#goods-vs-service-lines)) |
| Hook times out but the invoice appears in Workday | Slow submit (large attachment): the platform gave up waiting while the connector finished — rely on `Add_Only`/duplicate checks, raise `timeout_s` |
| Import config rejected on save/run | Unknown key (strict validation), KB-dialect keys, `id_keys` given as a string instead of a list, or an unsupported `request.payload` top-level key |
| Import runs but a dataset stays stale | Differential window: records' `Updated_*` dates fall outside `${last_modified_date}` − 24 h, or the entry pins `Response_Filter.Page`; force a `replace` run |
| Later entries of a multi-entry import stay stale | An earlier entry's job was still queued at invocation time — the duplicate short-circuits the rest of the run; slow the cron down or split the hook |
| Import fails complaining about overlap | Two runs hit the same dataset concurrently — space out the cron schedules |
| Deleted Workday records linger in a dataset | Differential sync cannot see deletions — add a periodic `replace` rebuild |
| Import hook does nothing on schedule | `events` missing `invocation.scheduled`, or empty `config.schedule.cron` |
| Connector cannot read the annotation/document | `token_owner` unset or its user lacks queue access |

## Related Skills

- `mdh-reference` — the datasets the import fills; matching config that stamps `*_wd`
  reference IDs onto annotations
- `export-pipeline-reference` — REST/JSON export targets and the Request Processor
  (alternative path; also the shape of custom REST lookups toward Workday)
- `txscript-reference` — formula fields and hooks that prepare export-ready values
- `rossum-reference` — hooks, events, `token_owner`, webhook mechanics
