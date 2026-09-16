# mdh-odata-filtered-full-refresh

Refresh a **small, server-side-filtered** population from an OData v4 source into an MDH
dataset with one `PUT` per run. The complement of the watermark syncs: no cursor, no state,
no handoff — and the only shape that **propagates deletions**, because a `PUT` removes rows
that have left the population while a `PATCH`-merge never does.

## When this is the right part

Measured on a one-time-vendor feed, where the real vendor exists only as a typed address on
the purchase order and the master carries a placeholder code:

- the filter (`startswith(Supplier,'<placeholder-prefix>')`) cut **2.33 M rows to 5,134 (0.2 %)**, so the
  whole sweep ran in ~20 s over 12 pages — a cursor would have added state and failure modes
  to save nothing;
- **259 of those rows had no change stamp at all**, so a watermark keyed on
  `LastChangeDateTime` would have skipped them forever;
- rows leave the population when a placeholder is replaced by a real vendor; only a replace
  reflects that.

Two more uses of the same fragment:

- **nightly drift companion** for `master-data/mdh-odata-import-skiptoken-sync`, with an empty
  `«odata_filter»` — OData GET never surfaces deletes, so a periodic full replace is how a
  removed row finally disappears;
- any **reference table** (tax codes, company codes, payment terms) small enough to sweep.

## Atomicity is the point

Pages are buffered and written **once**. A `PUT` mid-sweep could truncate the dataset if a
later page failed; with buffering a failed sweep writes nothing and yesterday's data stands.
An **empty result is refused** for the same reason — a source hiccup must not wipe a working
dataset. The runtime budget aborts *before* the write, never after.

## Params

| param | default | meaning |
|---|---|---|
| `«dataset»` | required | MDH dataset to replace |
| `«id_keys»` | required | comma-separated natural key |
| `«odata_filter»` | `""` | population selector; `""` = whole entity set |
| `«expand_nav»` | `""` | one nav property flattened as `<nav>_<field>` columns |
| `«select_fields»` | `""` | `$select`; keep anything you filter or key on |
| `«order_by»` | `PurchaseOrder asc` | entity key, for stable offsets |
| `«page_top»` | `500` | the gateway may serve fewer; only an empty page ends the walk |
| `«runtime_budget_s»` | `45` | aborts without writing |

Settings `TOKEN_URL`, `API_URL`; secrets `client_id`, `client_secret`.

## Gateway rules that apply here too

- A standalone `or` in `$filter` → 403 `FORBIDDEN_PARAMETER` (SQL threat protection);
  `in (...)` → 500. Build the population with `and`, `startswith()`, ranges.
- The gateway caps pages below `$top` (harder on `$expand`); the fragment continues until an
  empty page.
- MDH ingestion is asynchronous — the row count settles a minute or so after the run.

See `sap-reference` → *Shape A* and *One-time vendors*.
