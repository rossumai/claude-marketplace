# mdh-odata-import-skiptoken-sync

The `master-data/mdh-import-watermark-sync` engine with the **OData v4 through an API gateway**
dialect already adapted — the shape you get when SAP S/4HANA is exposed through SAP BTP
Integration Suite, API Management, or a similar proxy: OAuth2 client credentials, `$filter`,
`$orderby`, `$top`, `$skiptoken`, `$expand`, a JSON `value` array.

## Why it is shaped the way it is

Every rule below was learned from a silent data loss or a 403, not from the OData spec.

| Rule | What went wrong without it |
|---|---|
| **Page by a numeric `$skiptoken` offset, not `@odata.nextLink`** | the gateway's relative nextLink silently truncated a windowed pull |
| **Only an empty page ends the walk** | the gateway serves fewer rows than `$top` (undocumented cap, harder on `$expand`); treating the first short page as the end truncated a full load to one page |
| **Order the full load by the entity key, not the date** | creation dates are day-granular; thousands of rows tie at every page boundary and the server reorders ties between requests, so offset paging skipped and duplicated rows |
| **Scope the full load on the *creation* date, the incremental on the *change* date** | a full load filtered on `LastChangeDateTime` becomes "touched since X" — misses old untouched records, pulls in edited ancient ones |
| **Keep the date fields in `$select`** | `$select=PurchaseOrder` alone stripped `LastChangeDateTime` from 25 of 25 headers; every child landed with an empty watermark and the next run restarted from the beginning |
| **No `or`, no `in` in `$filter`** | the gateway's SQL threat protection returns 403 `FORBIDDEN_PARAMETER` on a standalone `or` (2 terms, 5 terms — the keyword, not the count); `in (...)` gives 500. Only `and` survives: a filter can express a range, never a set |
| **Checkpoint the cursor after every page** | a hard kill at 60 s otherwise redoes the whole window; now it redoes one page |
| **Pin the handoff before the first full page** | the incremental after a multi-run full load restarted from the floor date |

## `$expand`: windowing a child that has no date

A line-item entity may carry no date at all. The only way to window it is through its parent:
request the header with `$expand=<children>` and the header's date filter, then flatten each
header's children into rows and **stamp the header's creation/change dates onto every child**.
That is what `«expand_nav»` does. Composite `«id_keys»` (`PurchaseOrder,PurchaseOrderItem`)
are then the natural key, and the header key is copied down when the child omits it.

Nested expands work (`_Item($expand=_AccountAssignment)`) but page size must drop to 100–200 —
measured on one gateway, and a companion feed for the nested level on its own cursor was the
better shape, so the two can fail independently.

## Params

| param | default | meaning |
|---|---|---|
| `«dataset»` / `«state_dataset»` / `«state_key»` | required | as in `mdh-import-watermark-sync` |
| `«id_keys»` | required | comma-separated; composite for flattened children |
| `«created_field»` | `CreationDate` | Edm.Date; literal is a bare ISO date |
| `«changed_field»` | `LastChangeDateTime` | drives incremental + watermark |
| `«order_by»` | `PurchaseOrder asc` | the entity key |
| `«full_load_start»` | `2000-01-01` | `""` skips the full load |
| `«start_date»` | `2000-01-01T00:00:00Z` | incremental floor on an empty dataset |
| `«expand_nav»` | `""` | navigation property to flatten |
| `«page_top»` | `5000` | 100–500 on an `$expand` feed |
| `«page_limit»` | `200` | pages per run |
| `«runtime_budget_s»` | `40` | under the 60 s kill |

Settings: `TOKEN_URL`, `API_URL` (entity set URL including `sap-client=`). Secrets:
`client_id`, `client_secret`. The token call tries Basic auth first and falls back to
credentials in the form body — both gateways were met.

## What it does not do

- **Surface deletes.** OData GET never does; a removed row just stops appearing. If drift
  matters, run `master-data/mdh-odata-filtered-full-refresh` nightly as a PUT-replace
  companion.
- **Run in parallel.** The production version had stateless worker overrides to drain
  disjoint `$skiptoken` slices concurrently for a 1M-row backfill; left out here. The
  gateway serialised beyond 4 workers anyway.
- **Wait for MDH.** Ingestion is asynchronous (HTTP 202) with one worker across datasets. A
  row count read right after a load is a queue snapshot.

See `sap-reference` → *Shape A* for the gateway contract and the account-assignment feed notes.
