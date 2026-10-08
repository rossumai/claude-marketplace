# NetSuite Integration — Configuration Reference

> Sources, by authority: the connector's live OpenAPI spec
> (`https://<region host>/svc/netsuite-v3/api/openapi.json`, read 2026-10-08) for settings; a
> production SOAP deployment (15 datasets on three cadences, plus the export) for real usage; an
> internal SA handbook (2025) for NetSuite-side setup and NetSuite's own rules; and the REST
> import as released for acceptance testing in October 2026. The REST import is changing
> quickly; re-check the "not there yet" table before relying on it.

## Table of Contents

- [Two Connectors at a Glance](#two-connectors-at-a-glance)
- [SOAP Removal Timeline](#soap-removal-timeline)
- [NetSuite-Side Setup](#netsuite-side-setup)
- [SOAP Connector: Hook Wiring](#soap-connector-hook-wiring)
- [SOAP Import Configuration](#soap-import-configuration)
- [SOAP Dataset Shapes](#soap-dataset-shapes)
- [SOAP Export Configuration](#soap-export-configuration)
- [Reading NetSuite Datasets in MDH](#reading-netsuite-datasets-in-mdh)
- [REST Import](#rest-import)
- [REST import: what is not there yet](#rest-import-what-is-not-there-yet)
- [Migrating a SOAP Import to REST](#migrating-a-soap-import-to-rest)
- [Debugging](#debugging)
- [Gotchas](#gotchas)

---

## Two Connectors at a Glance

```
SOAP connector (svc/netsuite-v3)
  Rossum cron ──invocation.scheduled──▶ /api/v1/import   (webhook, run_async: true)
                                          │ one search/getAll per import_configs[] entry
                                          ▼
                                     MDH datasets (whole NetSuite records)

  Rossum annotation ──annotation_content.export──▶ /api/v1/export   (webhook, run_async: false)
                                          │ upsert record → add File → attach File
                                          ▼
                                     NetSuite VendorBill / VendorCredit + PDF

REST import (job hook, actor netsuite_rest_import)
  Rossum cron ──invocation.scheduled──▶ job service
                                          │ one SuiteQL query, paged by offset
                                          ▼
                                     one MDH dataset (selected columns only)
```

| | SOAP import | REST import |
|---|---|---|
| Hook type | `webhook` to `/svc/netsuite-v3/api/v1/import` | `job`, created from Store template 56 |
| Datasets per hook | many (`import_configs` list) | exactly one (`import_config` object — a list is rejected) |
| What to fetch | SOAP payload: `search` (`*SearchBasic` / `*SearchAdvanced`) or `getAll` | one SuiteQL `query` |
| Incremental runs | `{last_modified_date}` inside the search | `${window_start}` / `${window_end}` inside the query, plus `checkpoint_strategy` |
| Run limits | `async_settings` per config | `job_run_settings` per hook |
| Credentials | TBA | TBA (same secret names); OAuth 2.0 M2M exists but is untested |
| Row key | `internalId` | whatever `id_keys` names, normally `id` |
| Row fields | the whole record: camelCase, nested references, ISO UTC dates | only the selected columns: lowercase, flat, dates as the query formats them |
| Export | yes (`/api/v1/export`) | no |

Both imports share **one concurrency limit per NetSuite account**, keyed on the `account`
string, so they can run side by side as long as both use the same `account` spelling and the
same `concurrency_limit`.

---

## SOAP Removal Timeline

From Oracle's [SOAP Removal Plans FAQ](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/article_2104046421.html):

| NetSuite release | What happens |
|---|---|
| 2025.2 | Last planned SOAP endpoint |
| 2026.1 | New integrations "should use REST web services with OAuth 2.0" |
| 2027.1 | No new integrations with Token-Based Authentication (TBA) — and so no new SOAP integrations |
| 2027.2 | Only the last endpoint (2025.2) is supported; older endpoints still run, unsupported |
| 2028.2 | All SOAP endpoints disabled; SOAP integrations stop working |

Consequences for Rossum projects:

- A SOAP hook pinned to an older WSDL (e.g. `v2024_2_0`) loses support at 2027.2. Moving it to
  the 2025.2 endpoint changes at least `wsdl_url`, `service_url` and `service_binding_name`;
  test the searches and the export against the new endpoint before switching.
- The REST import runs on TBA today. From 2027.1 a **new** customer cannot create TBA
  credentials, so new projects will need the REST import's OAuth 2.0 path — currently its
  least-tested part (see [what is not there yet](#rest-import-what-is-not-there-yet)).
- The export has no REST replacement yet.

---

## NetSuite-Side Setup

TBA credentials come from four objects created by a NetSuite administrator:

1. **Integration record** — Setup → Integrations → Manage Integrations → New, with
   *Token-Based Authentication* checked. Saving shows the **Consumer Key / Consumer Secret**
   once (`consumer_key`, `consumer_secret`).
2. **Role** — Setup → Users/Roles → Manage Roles → New, with *SOAP Web Services*, *Log in using
   Access Tokens*, and the record permissions the datasets need (below).
3. **Integration user** — Lists → Employees → Employees → New, *Give Access*, the role above. Use
   a dedicated employee: a reused one breaks the integration when it is deactivated.
4. **Access token** — Setup → Users/Roles → Access Tokens → New, for that integration, user and
   role. Saving shows the **Token ID / Token Secret** once (`token_key`, `token_secret`).

TBA itself must be enabled (Setup → Company → Enable Features → SuiteCloud). The `account` id is
under Setup → Company → Company Information. From NetSuite 2027.1 new TBA integrations cannot be
created ([timeline](#soap-removal-timeline)).

| Dataset | Role permission |
|---|---|
| accounts | Lists → Accounts |
| currencies | Lists → Currency |
| customers | Lists → Customers |
| subsidiaries | Lists → Subsidiaries |
| vendors | Lists → Vendors |
| purchase orders | Transactions → Purchase Order (View) |
| any advanced transaction search | Transactions → Find Transaction |

---

## SOAP Connector: Hook Wiring

The Store tile "NetSuite Integration" (template 6) is `install_action: request_access`: it
does not install a working hook. In practice the hooks are created by hand: Extensions →
Create extension → *Webhook*, the URL below, a **token owner** under Advanced settings, then
settings and secrets. They are plain webhooks whose `config.url` points at the regional service.
The connector's own OpenAPI spec (`…/svc/netsuite-v3/api/openapi.json`, UI at `…/api/docs`) is
the authoritative settings schema.

| | Import | Export |
|---|---|---|
| `config.url` | `https://<region host>/svc/netsuite-v3/api/v1/import` | `https://<region host>/svc/netsuite-v3/api/v1/export` |
| `events` | `invocation.scheduled` (+ `invocation.manual`) | `annotation_content.export` |
| `queues` | none | the queues that export |
| `config.schedule.cron` | the cadence | — |
| `settings.run_async` | `true` | `false` |
| `config.timeout_s` / `retry_count` (observed) | 30 / 4 | 120 / **0** |

- Rossum webhooks default to a **30 s timeout and 5 retries**. Long exports need a higher
  `timeout_s` (the production export uses 120 s; older docs cite a 60 s cap).
- Set **`retry_count: 0` on export hooks**: a retried export repeats the File Cabinet upload,
  which is not idempotent ([Steps 2–3](#steps-23--upload-and-attach-the-pdf)).
- One hook carries either `import_configs` or `export_configs`, never both.

### `netsuite_settings`

Shared by import and export:

```json
"netsuite_settings": {
  "account": "1234567_SB1",
  "wsdl_url": "https://1234567-sb1.suitetalk.api.netsuite.com/wsdl/v2024_2_0/netsuite.wsdl",
  "service_url": "https://1234567-sb1.suitetalk.api.netsuite.com/services/NetSuitePort_2024_2",
  "service_binding_name": "{urn:platform_2024_2.webservices.netsuite.com}NetSuiteBinding",
  "concurrency_limit": 4
}
```

`account` and `wsdl_url` are required; `service_url` and `service_binding_name` are optional.
The `account` uses the underscore form (`1234567_SB1`) and is case-sensitive; the hostnames use
the dash form (`1234567-sb1`). The concurrency limit is keyed on the exact `account` string; size
it from NetSuite's Setup → Integration → Integration Governance, which shows the account's limit
and the peak concurrency of all its integrations.

### Secrets

TBA, the same set on import and export hooks:
`consumer_key`, `consumer_secret`, `token_key`, `token_secret`, `rossum_username`,
`rossum_password`.

---

## SOAP Import Configuration

```json
{
  "run_async": true,
  "netsuite_settings": { "...": "..." },
  "import_configs": [
    {
      "master_data_name": "ns_currencies",
      "payload": {
        "method_name": "getAll",
        "method_args": [{ "_ns_type": "GetAllRecord", "recordType": "currency" }]
      },
      "async_settings": { "retries": 5, "max_run_time_s": 36000 }
    }
  ]
}
```

| Key | Meaning |
|---|---|
| `master_data_name` | target MDH dataset |
| `payload.method_name` | SOAP operation: `search` or `getAll` |
| `payload.method_args` | the operation's arguments; every object carries its NetSuite type in `_ns_type` |
| `payload.method_headers` | optional request headers: `searchPreferences` (`pageSize`, `bodyFieldsOnly`, `returnSearchColumns`) and `preferences` (e.g. `runServerSuiteScriptAndTriggerWorkflows`) |
| `async_settings` | `retries` (default 2, 1–10), `max_run_time_s` (default 7200; production uses 36000 = 10 h), `valid_for_s` (default 43200, 300–172800: a queued job older than this never runs) |
| `advanced_search_internal_id_jmespath` | advanced searches only — where the row's id lives (below) |
| `checkpoint_strategy` | in the spec ("checkpointed import with sub-window processing", `{"strategy": "temporal", "interval_days": N}`) — the same object the REST import uses; not seen on a production SOAP hook |
| `fields_to_save` | in the spec, marked "CURRENTLY DOESN'T WORK" — do not rely on it |
| `development_options` | in the spec, development only — never in production |

The spec also describes an async import shape with an `import_config` list and a hook-level
`job_run_settings`; production hooks use `import_configs` with per-config `async_settings`.
Use the production shape unless the integration team says otherwise.

All `import_configs` of one hook run on that hook's cron, so split datasets across hooks by
cadence. A production pattern: vendors and POs every 10 minutes, reference data and
transactions hourly, one-off datasets (e.g. accounting contexts) on a manual-only hook.

### `getAll`

For record types with no search, e.g. `{"_ns_type": "GetAllRecord", "recordType": "currency"}`
(also used for `accountingcontext`). Always a full read.

### Basic search

```json
{
  "method_name": "search",
  "method_args": [{
    "_ns_type": "TransactionSearchBasic",
    "type": { "operator": "anyOf", "searchValue": "_vendorBill" },
    "lastModifiedDate": { "operator": "onOrAfter", "searchValue": "{last_modified_date}" }
  }]
}
```

- Search types seen in production: `VendorSearchBasic`, `TransactionSearchBasic`
  (`_vendorBill`, `_vendorCredit`, `_purchaseOrder`), `ItemSearchBasic`
  (`_inventoryItem`, `_nonInventoryItem`), `DepartmentSearchBasic`, `ClassificationSearchBasic`,
  `SubsidiarySearchBasic`, `LocationSearchBasic`, `AccountSearchBasic`, `NexusSearchBasic`,
  `SalesTaxItemSearchBasic`.
- `type` takes one value or a list: `"searchValue": ["_inventoryItem", "_nonInventoryItem"]`.
- `isInactive` appears both as `"false"` (string) and `false` (boolean) in working configs.
- `{last_modified_date}` (single braces) is filled in by the connector, with `operator` `after`
  or `onOrAfter`. On an empty dataset it defaults to 1970-01-01, so the first run is a full
  import. Without it, every run re-reads the whole record type.

### Incremental imports and status filters

An incremental search returns records that **changed and still match its criteria**. Combine
`{last_modified_date}` with `isInactive: false` and a vendor deactivated after the first load
no longer matches, so it is never fetched again: the dataset keeps it as active, and matching
keeps offering it. In one production vendor dataset only 1 of ~4,900 records was marked
inactive, while vendors already deactivated in NetSuite were still marked active. Deletions
never arrive either: the import only upserts.

Ask two questions about every criterion on an incremental search:

1. **Can a record leave the filtered set through an update?** (`isInactive`, a status.) If yes,
   do not put it in the incremental search. Import the field and filter in the MDH matching
   query instead.
2. **Does anything downstream need the records outside the set?** (A "this vendor is inactive"
   warning instead of "not found", a validation rule.) If yes, load them, including in the
   first full load.

A criterion is fine in the import when the field never changes for a record (type, subsidiary,
currency, a creation-date floor), when records can only enter the set, or when the import
re-reads everything each run (`getAll`, a search without `{last_modified_date}`) — but such a run
still only upserts, so records that left the set stay in the dataset until it is replaced.

**Detect:** search the record type with `isInactive: true` and `lastModifiedDate` on or after the
dataset's first load, look those `internalId`s up in the dataset, and count the ones still
`isInactive: false`.
**Repair:** first check that every matching query filters `isInactive` itself — a query that
relied on the import filter starts offering inactive records. Then remove the criterion and run
the hook once with a fixed date in place of `{last_modified_date}`, reaching back to the
earliest stale record, before restoring the placeholder.

### Advanced search (joins, selected columns)

```json
{
  "master_data_name": "ns_po_lines",
  "advanced_search_internal_id_jmespath": "basic.internalId[0].searchValue.internalId",
  "payload": {
    "method_name": "search",
    "method_headers": {
      "searchPreferences": { "pageSize": 100, "bodyFieldsOnly": false, "returnSearchColumns": true }
    },
    "method_args": [{
      "_ns_type": "TransactionSearchAdvanced",
      "criteria": {
        "_ns_type": "TransactionSearch",
        "basic": {
          "_ns_type": "TransactionSearchBasic",
          "type": { "operator": "anyOf", "searchValue": "_purchaseOrder" },
          "mainLine": { "searchValue": false },
          "lastModifiedDate": { "operator": "onOrAfter", "searchValue": "{last_modified_date}" }
        }
      },
      "columns": {
        "_ns_type": "TransactionSearchRow",
        "basic": {
          "_ns_type": "TransactionSearchRowBasic",
          "internalId": {}, "tranId": {}, "tranDate": {}, "line": {},
          "quantity": {}, "quantityBilled": {}, "quantityShipRecv": {}, "lastModifiedDate": {}
        },
        "itemJoin": {
          "_ns_type": "ItemSearchRowBasic",
          "internalId": {}, "itemId": {}, "displayName": {}, "purchaseDescription": {}
        }
      }
    }]
  }
}
```

- `criteria` filters (it can join too: `vendorJoin`, `subsidiaryJoin` with their own
  `*SearchBasic`); `columns` selects, each column as an empty object `{}`.
- `returnSearchColumns: true` is what makes the rows come back as columns.
- `advanced_search_internal_id_jmespath` tells the connector where to read the row's id; it is
  written to a top-level `internalId` on each row.
- `mainLine: false` returns one row per transaction line — and also NetSuite's **tax rows**,
  which reuse a line number. In one production dataset 2,937 `(internalId, line)` pairs
  appeared two or three times, all identical tax rows. Add a tax-line filter to the criteria,
  or deduplicate in the matching query.

### Custom records

`CustomRecordSearchAdvanced` with `criteria.basic` `{"_ns_type": "CustomRecordSearchBasic",
"recType": {"internalId": "<N>"}}`. Custom-record columns go in
`columns.basic.customFieldList.customField[]` (`SearchColumnStringCustomField` + `scriptId`).
The change-date field is **`lastModified`**, not `lastModifiedDate`.

### Search errors

`VendorSubsidiaryRelationshipSearch…` can fail with `UNEXPECTED_ERROR: An unexpected error
occurred. Error ID: …`. NetSuite support's fix: remove `method_headers.searchPreferences` from
that config.

### Initial full import

On an empty dataset the first incremental run reads everything, and large record types can
take days — longer than a job may run (`max_run_time_s`), so the run fails and starts over.
Instead:

1. Ask whether the full history is needed; often the last year is enough.
2. Partition by creation date into several `import_configs` entries writing the same dataset,
   so they run in parallel: `"dateCreated": {"operator": "within", "searchValue":
   "2023-01-01T00:00:00Z", "searchValue2": "2024-01-01T00:00:00Z"}`, and `onOrAfter` for the last
   partition.
3. Check each partition landed with an aggregate on the dataset — on **`createdDate`**: NetSuite
   returns `createdDate` although the search argument is `dateCreated`.
4. Re-run failed partitions with a narrower window; then catch up with a fixed
   `lastModifiedDate` and switch to `{last_modified_date}`.

---

## SOAP Dataset Shapes

**Basic search / getAll** — the whole record, as NetSuite returns it:

```json
{
  "internalId": "7",
  "externalId": null,
  "name": "Finance & Administration",
  "isInactive": false,
  "parent": null,
  "subsidiaryList": { "recordRef": [{ "internalId": "49", "name": null, "type": null, "externalId": null }] },
  "classTranslationList": { "classTranslation": [{ "language": "German", "name": null, "locale": null }] },
  "customFieldList": null,
  "nullFieldList": null
}
```

- `internalId` is a **string**. References are nested `RecordRef`s (`{internalId, name, type, externalId}`).
- Lists are wrapped: `itemList.item[]`, `expenseList.expense[]`, `addressbookList`, `subsidiaryList.recordRef[]`.
- A vendor record carries ~85 top-level fields (`companyName`, `vatRegNumber`, `subsidiary`,
  `currency`, `addressbookList`, `isInactive`, …) whether or not you need them.

**Advanced search** — every column the row type has, unselected ones as empty arrays, selected
ones as one-element arrays:

```json
{
  "internalId": "41892",
  "basic": {
    "tranId":   [{ "searchValue": "PO000003" }],
    "line":     [{ "searchValue": 3 }],
    "quantity": [{ "searchValue": -1.0 }],
    "internalId": [{ "searchValue": { "internalId": "41892" } }],
    "tranDate": [{ "searchValue": { "$date": "2021-03-31T22:00:00Z" } }],
    "amount": [], "account": [], "...": []
  },
  "itemJoin": {
    "itemId": [{ "searchValue": "S-STD" }]
  }
}
```

Read values as `basic.<column>[0].searchValue` (and `.internalId` for reference columns).

---

## SOAP Export Configuration

```json
{
  "run_async": false,
  "netsuite_settings": { "...": "..." },
  "export_configs": [{
    "payload": [
      { "method_name": "upsert", "method_args": [ { "$DATAPOINT_MAPPING$": "... the record ..." } ] },
      { "method_name": "add",    "method_args": [ { "_ns_type": "File", "...": "..." } ] },
      { "method_name": "attach", "method_args": [ { "_ns_type": "AttachBasicReference", "...": "..." } ] }
    ]
  }]
}
```

`payload` is an ordered list of SOAP calls (the spec accepts only the list; older docs show a
single object). Each step's result is available to later steps as
`{pipeline_context[N].internal_id}` — the internal id NetSuite returned for step `N`. Each call
can also set `response_payload_reference_key` / `response_status_reference_key`: the connector
then saves NetSuite's response / status into a document relation under that key, so the outcome
stays with the document. An async variant (`run_async: true`, `async_settings` per export
config) exists; production exports run synchronously.

### The mapping language

The export uses the same template language as the Workday connector — `@{schema_id}`,
`$DATAPOINT_VALUE$` (with `value_type`), `$IF_SCHEMA_ID$`, `$FOR_EACH_SCHEMA_ID$`,
`$IF_DATAPOINT_VALUE$`, `$DATAPOINT_MAPPING$`. See `workday-reference` → The Mapping DSL for each
primitive.
NetSuite-specific pieces:

| Piece | Use |
|---|---|
| `"_ns_type": "<Type>"` | on every object: `VendorBill`, `VendorCredit`, `VendorBillItem`, `VendorBillExpense`, `RecordRef`, `File`, … |
| `RecordRef` | `{"_ns_type": "RecordRef", "type": "vendor", "internalId": "@{entity_id}"}` — the ids come from MDH matching |
| `$GET_DOCUMENT_CONTENT$` | the annotation's source file, for the `File` record (`"content": {"$GET_DOCUMENT_CONTENT$": {}}`) |
| `{pipeline_context[N].internal_id}` | the id created by step `N` |

### Step 1 — upsert the record

The top level is a `$DATAPOINT_MAPPING$` on a document-type field, choosing the record:

```json
{ "$DATAPOINT_MAPPING$": { "schema_id": "document_type", "mapping": {
    "tax_invoice": { "_ns_type": "VendorBill", "...": "..." },
    "credit_note": { "_ns_type": "VendorCredit", "...": "..." }
} } }
```

A `VendorBill` body seen in production:

| Field | Value |
|---|---|
| `externalId` | a Rossum-side unique id — **makes the record upsert idempotent**: re-exporting updates the record instead of duplicating it (the File step is not idempotent) |
| `tranId` | the invoice number |
| `entity` | `RecordRef` type `vendor` |
| `subsidiary`, `currency`, `department` | `RecordRef`s (`subsidiary`, `currency`, `department`) |
| `tranDate`, `dueDate` | `$IF_SCHEMA_ID$` around `$DATAPOINT_VALUE$` with `value_type: "iso_datetime"` |
| `memo` | free text |
| `customForm` | `RecordRef` of type `customRecord` to the customer's form (a fixed internal id) |
| `approvalStatus` | `RecordRef` with `internalId` `1` (Pending) or `2` (Approved) |
| `itemList` / `expenseList` | see below |

Lines go either to `itemList.item[]` (inventory items: `item`, `rate`, `quantity`,
`description`, `department`, optional `orderDoc` / `orderLine` to link a PO line) or to
`expenseList.expense[]` (GL lines: `account`, `amount`, `memo`, `taxCode`, `class`,
`department`, optional `orderDoc` / `orderLine`). The production pattern:

- a formula field (`has_inventory_items`, `has_expenses`) switches each list on with
  `$DATAPOINT_MAPPING$` `"True": {…}, "False": {}`;
- `$FOR_EACH_SCHEMA_ID$` iterates a grouped line table, and an inner `$DATAPOINT_MAPPING$` on
  a line-type field emits either an item or an expense object;
- `taxCode` is a `RecordRef` of type `taxType`; `class` is type `classification`.

NetSuite rules the export must respect:

- **No repeated items** on one bill, and a PO + `orderLine` pair may appear **only once** per
  payload — group the lines first (the line-items grouping extension or a formula table).
- **`VendorCredit` amounts and quantities must be positive** — add hidden formula fields that
  flip the sign.
- Keep big `$DATAPOINT_MAPPING$` switches high in the tree and duplicate whole sections, rather
  than nesting many small conditions.

### Custom fields

Header custom fields (`custbody_…`) go in the record's `customFieldList`; line custom fields
(`custcol_…`) go in the line's own `customFieldList` inside `itemList.item`:

```json
"customFieldList": { "_ns_type": "CustomFieldList", "customField": [
  { "_ns_type": "StringCustomFieldRef", "scriptId": "custbody_captured_total", "value": "@{amount_total}" }
] }
```

Wrap an entry in `$IF_SCHEMA_ID$` to send it only when the field has a value. The concrete type
follows the field type in NetSuite: `LongCustomFieldRef` (integer), `DoubleCustomFieldRef`
(decimal), `BooleanCustomFieldRef` (check box), `StringCustomFieldRef` (text, e-mail, phone,
URL), `DateCustomFieldRef` (date / time), `SelectCustomFieldRef` (list / record),
`MultiSelectCustomFieldRef` (multiple select).

### Applying a vendor credit to a bill

A `VendorCredit` can be applied to an **open** vendor bill, always on line `"0"`:

```json
"applyList": { "$IF_DATAPOINT_VALUE$": { "schema_id": "bill_status_match", "value": "Open", "mapping": {
  "_ns_type": "VendorCreditApplyList",
  "apply": { "_ns_type": "VendorCreditApply", "doc": "@{bill_internal_id_match}", "line": "0",
             "apply": true, "amount": "@{amount_total_normalized}" }
} } }
```

### Steps 2–3 — upload and attach the PDF

```json
{ "method_name": "add", "method_args": [{
    "_ns_type": "File",
    "name": "@{original_file_name}",
    "attachFrom": "_web",
    "content": { "$GET_DOCUMENT_CONTENT$": {} },
    "folder": { "_ns_type": "RecordRef", "type": "folder", "internalId": "@{file_cabinet_folder_id}" }
}] }
```

```json
{ "method_name": "attach", "method_args": [{
    "_ns_type": "AttachBasicReference",
    "attachTo": { "_ns_type": "RecordRef",
                  "type": { "$DATAPOINT_MAPPING$": { "schema_id": "document_type",
                            "mapping": { "tax_invoice": "vendorBill", "credit_note": "vendorCredit" } } },
                  "internalId": "{pipeline_context[0].internal_id}" },
    "attachedRecord": { "_ns_type": "RecordRef", "type": "file",
                        "internalId": "{pipeline_context[1].internal_id}" }
}] }
```

Note the casing: the record body uses `_ns_type` `VendorBill`, but the attach target `type` is
`vendorBill`.

**These steps are not idempotent.** NetSuite requires unique file names in a folder, while Rossum
allows duplicates. A second export of the same annotation repeats the upload: it fails on the
duplicate name or, with a unique name, adds a second copy. Make names unique (e.g. a function
hook that appends a timestamp to the original file name), keep `retry_count: 0`, and guard
against exporting the same document twice (duplicate handling).

---

## Reading NetSuite Datasets in MDH

The production matching configurations built on SOAP datasets:

| Matching | Dataset | `dataset_key` | Label |
|---|---|---|---|
| Vendor (exact VAT, then fuzzy name/address) | vendors | `internalId` | `{companyName} [{internalId}]` |
| PO on header | purchase orders | `tranId` | `{tranId} [{internalId}]: {status}` |
| PO lines (items) | purchase orders | `itemList.item.line` | `{itemList.item.line} - {itemList.item.item.name}` |
| PO lines (expenses) | purchase orders | `expenseList.expense.line` | `{expenseList.expense.line} - {expenseList.expense.memo}` |
| Subsidiary | vendor–subsidiary relationships | `subsidiary__internalId` | `{subsidiary__name} [{subsidiary__internalId}]` |
| Tax code | sales tax items | `internalId` | `{taxType.name}:{itemId} ({rate})` |
| Nexus | nexus | `taxAgency.internalId` | |
| GL account, department, class, item | the matching dataset | `internalId` | `{name} [{internalId}]` etc. |

The keys and labels depend on the SOAP record shape (`internalId`, nested paths). A REST
dataset has none of these — see [migration](#migrating-a-soap-import-to-rest).

---

## REST Import

### Create the hook

1. The organization must see **Store template 56** ("NetSuite REST master data import (TBA)").
   Only organization groups with the **`integrations_team`** visibility tag see it; elsewhere it
   is missing from `GET /hook_templates` and `GET /hook_templates/56` returns 404. Customers
   cannot set the tag; ask Rossum (`rossum-reference` → Store templates and visibility).
2. Install from the template (UI or `POST /hooks/create` with `hook_template`), then fill in the
   settings below. The template is `type: job`, `install_action: copy`, events
   `invocation.scheduled` + `invocation.manual`, default cron `0 3 * * *`.
3. Set a `token_owner` (a user in the organization). A job hook without one fails at once with
   `Error in configuration: Set the token owner of the extension.`

```json
{
  "settings": {
    "netsuite_settings": {
      "account": "1234567_SB1",
      "auth_type": "tba",
      "concurrency_limit": 4,
      "request_timeout_s": 120
    },
    "import_config": {
      "master_data_name": "ns_vendor_bills",
      "id_keys": ["id"],
      "request": { "query": "SELECT id, tranid, ... FROM transaction WHERE type = 'VendBill' ORDER BY id", "page_size": 1000 }
    },
    "job_run_settings": { "retries": 2, "max_run_time_s": 3600, "valid_for_s": 86400 }
  },
  "secrets": {
    "consumer_key": "...", "consumer_secret": "...", "token_key": "...", "token_secret": "...",
    "rossum_username": "unused", "rossum_password": "unused"
  }
}
```

| Setting | Notes |
|---|---|
| `account` | spell it exactly as the SOAP hooks do (`1234567_SB1`, not `1234567-sb1`): the shared concurrency limit is keyed on it |
| `auth_type` | `tba` or `oauth2_m2m` |
| `certificate_id` | required for `oauth2_m2m` |
| `concurrency_limit` | default 4; use the same value as the account's SOAP hooks |
| `import_config` | one object; a list is rejected |
| `id_keys` | the row key for updates, normally `["id"]` |
| `request.page_size` | at most 1000, also the default |
| `checkpoint_strategy` | required by validation when the query uses the window placeholders, rejected when it does not (below) |
| `job_run_settings` | `retries` 1–10, `max_run_time_s` 60–36,000, `valid_for_s` 300–172,800 |
| TBA secrets | same names as SOAP; `rossum_username` / `rossum_password` are required but unused — any value |
| OAuth 2.0 secrets | `client_id`, `private_key` (the whole PEM block), `rossum_username`, `rossum_password` |

Settings are validated **when the hook runs**, not when it is saved: a mistake shows up as a
failed run.

### Run status and logs

A run moves `waiting` → `running` → `completed` / `failed`. The hook log row
(`GET /hooks/logs?hook=<id>`) carries only the status, with an empty `message`. The detail —
progress lines and the failure reason — is in the run log (`rossum_list_hook_logs` with
`include_run_log=true` attaches it):

```
GET /api/v1/hooks/runs?hook=<id>             → runs, each with a uuid
GET /api/v1/hooks/runs/<uuid>/logs           → "Status changed to running", "Imported 1000 records", …,
                                                "Status changed to failed (reason: …)"
```

A run can sit in `waiting` for minutes before a worker picks it up; small datasets spend most
of their time queued.

### SuiteQL rules

1. **Always end with `ORDER BY id`.** Pages are read by offset and NetSuite does not order
   results on its own; without a stable order rows are skipped or repeated between pages.
2. **Select only the columns you need.** Column names always come back lowercase, even quoted
   aliases (`AS "internalId"` returns `internalid`).
3. **Format dates in the query.** A plain date column comes back in the account's display
   format (`14/5/2025`). Use `TO_CHAR(trandate, 'YYYY-MM-DD')`.
4. **References come back as ids.** Add `BUILTIN.DF(<field>) AS <field>_name` for the display
   name, e.g. `BUILTIN.DF(subsidiary) AS subsidiary_name`.
5. **Bound incremental queries by the window:**

```sql
AND lastmodifieddate >= TO_TIMESTAMP(SUBSTR('${window_start}', 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS')
AND lastmodifieddate <  TO_TIMESTAMP(SUBSTR('${window_end}', 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS')
```

`${window_start}` is 2 hours before the last successful run started; `${window_end}` is 5
minutes before now; both UTC (`2026-10-07T06:44:51+00:00`). The first run starts from 1969.
A query that uses them must also carry
`"checkpoint_strategy": {"strategy": "temporal", "interval_days": 7}` in `import_config` —
validation requires it, though the import does not use it yet. A query without placeholders
re-reads the whole table every run.

The window has the same trap as the SOAP search: a windowed query with `isinactive = 'F'` never
sees a record become inactive. Ask the same two questions
([Incremental imports and status filters](#incremental-imports-and-status-filters)); usually,
select `isinactive` as a column and filter in the matching query.

### SOAP search → SuiteQL

| SOAP | SuiteQL |
|---|---|
| `TransactionSearchBasic`, `type: _vendorBill` | `FROM transaction WHERE type = 'VendBill'` |
| `TransactionSearchBasic`, `type: _purchaseOrder` | `FROM transaction WHERE type = 'PurchOrd'` |
| `TransactionSearchBasic`, `type: _vendorCredit` | `FROM transaction WHERE type = 'VendCred'` |
| `ItemSearchBasic`, `type: _inventoryItem` | `FROM item WHERE itemtype = 'InvtPart'` |
| `ItemSearchBasic`, `type: _nonInventoryItem` | `FROM item WHERE itemtype = 'NonInvtPart'` |
| `*SearchBasic`, `isInactive: false` | `isinactive = 'F'` |
| `getAll`, `recordType: currency` | `FROM currency` |
| `CustomRecordSearchBasic`, `recType: {internalId: N}` | `FROM customrecord_<script id>` — look the script id up per account |
| `*SearchAdvanced` with joins | SuiteQL `JOIN`s, translated by hand from the search's columns |

Table names vary by account: `classification` and `location` were missing in one sandbox.
Check each table in the customer's account.

Examples:

```sql
SELECT id, tranid, TO_CHAR(trandate, 'YYYY-MM-DD') AS trandate, entity,
       TO_CHAR(lastmodifieddate, 'YYYY-MM-DD"T"HH24:MI:SS') AS lastmodifieddate
FROM transaction
WHERE type = 'VendBill'
  AND lastmodifieddate >= TO_TIMESTAMP(SUBSTR('${window_start}', 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS')
  AND lastmodifieddate <  TO_TIMESTAMP(SUBSTR('${window_end}', 1, 19), 'YYYY-MM-DD"T"HH24:MI:SS')
ORDER BY id
```

```sql
SELECT id, name, parent, subsidiary FROM department WHERE isinactive = 'F' ORDER BY id
```

---

## REST import: what is not there yet

As of 2026-10-08:

| Missing | Effect | Until fixed |
|---|---|---|
| Windowing (`checkpoint_strategy` is validation-only) | Each run reads its whole window in one pass. A first import that needs longer than `max_run_time_s` **always fails**: it keeps nothing, and every retry and later run starts again from 1969 | Set `max_run_time_s` above the first run's time (max 36,000 s). Skip datasets that cannot finish in that |
| OAuth 2.0 verified end to end | Only TBA has run end to end; EC certificates cannot sign yet; template 56 is TBA-only | Use TBA, or an RSA certificate. Matters from NetSuite 2027.1, when new TBA integrations are blocked |
| Window timezone confirmed | SuiteQL returns times in the account's timezone while the window bounds are UTC; an account more than 2 h behind UTC could miss recent changes | Check an incremental run on a US account |
| NetSuite's 100,000-row SuiteQL limit tested | A very large first import may stop there | Report any run that stops at 100,000 rows |
| Export | No REST export; vendor bills still go through the SOAP connector | — |
| Multi-dataset hooks | One hook per dataset; a 13-dataset SOAP hook becomes 13 REST hooks | — |
| Clean rows | Every row has an empty `links` field from NetSuite | Ignore it; removal is planned |
| Deletes | Not documented. Treat the import as upsert-only, like SOAP: a record deleted in NetSuite stays in the dataset | Periodic full reload if deletions matter |

---

## Migrating a SOAP Import to REST

The SOAP hooks and datasets keep working; build the REST hooks next to them into **new**
datasets, compare, then switch the matching.

1. One REST hook per SOAP `import_configs` entry. Keep the cadence of the SOAP hook it came
   from (`config.schedule.cron`).
2. Translate each search with the [table above](#soap-search--suiteql). Advanced searches with
   joins become SuiteQL `JOIN`s; select exactly the columns MDH reads.
3. Same `account` string and `concurrency_limit` as the SOAP hooks.
4. Size `max_run_time_s` for the first full import (until windowing ships).
5. **Rewrite every MDH configuration that reads the dataset.** REST rows are flat and lowercase:
   - `dataset_key: internalId` → `id`
   - nested paths (`itemList.item.line`, `taxAgency.internalId`, `taxType.name`) → the flat
     columns your query selects (`line`, `taxagency`, `taxtype_name` via `BUILTIN.DF`)
   - `$unwind` of `itemList.item` → not needed: a line-level query already returns one row per line
   - dates are strings in the format your `TO_CHAR` chose, not BSON dates
6. Re-check the schema fields MDH fills (and the export reads): the ids are the same NetSuite
   internal ids, so the export mapping does not change.

---

## Debugging

- **Import one record** to test a search: a `TransactionSearchBasic` with `"tranId":
  {"operator": "is", "searchValue": "<number>"}` into the usual dataset.
- **See the SOAP traffic**: NetSuite's SOAP Web Services Usage Logs
  (`https://system.netsuite.com/app/webservices/syncstatus.nl`) show each request and response
  the connector sent.
- **Call NetSuite directly** from Postman: SOAP needs a TBA signature (HMAC-SHA256 over account,
  consumer key, token, nonce and timestamp) in the SOAP header, plus a `SOAPAction` header.
- REST import runs: `GET /hooks/runs/<uuid>/logs` ([Run status and logs](#run-status-and-logs)).

---

## Gotchas

| Symptom | Cause |
|---|---|
| REST job fails instantly, log row has empty `message` | read `GET /hooks/runs/<uuid>/logs`; first suspects: missing `token_owner`, config validation |
| `GET /hook_templates/56` → 404 | the organization group lacks `integrations_team`; only Rossum can add it |
| REST rows skipped or duplicated between pages | query does not end with `ORDER BY id` |
| Column `internalId` arrives as `internalid` | SuiteQL lowercases every column name |
| Dates like `14/5/2025` in a REST dataset | unformatted date column; use `TO_CHAR` |
| First REST run fails after hours, then again from scratch | first import longer than `max_run_time_s`; no checkpointing yet |
| SOAP and REST hooks throttle each other | expected — one concurrency limit per `account`; keep both on the same `account` string and `concurrency_limit` |
| Records deactivated in NetSuite still offered in matching | incremental import filtered on `isInactive`; deactivated records are never re-fetched |
| Duplicate rows per PO line in an advanced-search dataset | `mainLine: false` also returns tax rows |
| Re-export creates a second bill | the upsert has no stable `externalId` |
| Re-export fails at the file step, or the bill gets two PDFs | File Cabinet names must be unique; the upload is not idempotent |
| Bill rejected for repeated items, or a PO line used twice | group the lines before export |
| Vendor credit rejected | amounts / quantities must be positive |
| `UNEXPECTED_ERROR` on the vendor–subsidiary relationship search | remove `searchPreferences` from that config |
| Partition check finds nothing on `dateCreated` | the stored field is `createdDate` |
| Incremental custom-record import re-reads everything or nothing | custom records use `lastModified`, not `lastModifiedDate` |
