# export-create-then-attach-with-state

Export a confirmed, PO-matched invoice into an ERP through a **two-call HTTPS middleware** —
*create the financial document*, then *attach the original file* — as a function hook on
`annotation_content.export`. What this part actually contributes is not the two POSTs; it is
the **state and verdict logic** that makes a retry safe when the thing you are creating is a
financial document.

Use it when the ERP is reached through a customer-owned REST facade over BAPI-style RFCs
(create → document number + fiscal year; attach → archive id). For OpenText VIM take
`export/export-ubl-to-vim`; for a public-cloud OData API a Request Processor chain fits better
(`export/export-create-upload-submit`).

## The three rules, all measured

| Rule | Why |
|---|---|
| **The transport flag is not the verdict.** Success is decided by scanning the BAPIRET2 return table; any row typed `A`, `E` or `X` fails the request | the middleware answered HTTP 200 + its own success flag while SAP rejected the document |
| **A timeout is an unknown outcome, not a failure.** State is recorded as `unknown` and the hook refuses to retry until a human has checked the ERP | no read-back endpoint existed; retrying blind is how you create a duplicate invoice |
| **State is persisted the instant the create succeeds**, in its own Data Storage collection, *before* the attachment is attempted | returning it as hook `operations` loses it if the attachment crashes the run — and the next run creates a second invoice |

Plus one that makes the second call safe: the attachment's **external document id is derived
from the annotation id**, so a repeated attach is recognised as a duplicate. Verified: the
same id returned the same archive id with the already-exists flag set.

## Flow

```
state = load(annotation_id)
if state.status == unknown        -> refuse; a human reconciles first
if no doc_number in state:
    POST create  -> scan BAPIRET2; on M8/535 (closed period) retry once inside a named open period
                 -> on rejection: save failed, write back, raise
                 -> on success:  SAVE doc_number + fiscal_year   <- before anything else
if no archive_id in state:
    POST attach  -> scan BAPIRET2; already-exists = success
                 -> on failure: save created, write back, raise ("retry will not duplicate")
write back doc_number / fiscal_year / archive_id / status / message
```

The **receipt-coverage guard** (`ENFORCE_RECEIPT_COVERAGE`) refuses an invoice that bills more
than has been received on lines whose PO line expects a goods receipt, using the GR-aware open
quantity and the PO line's own tolerance that matching wrote — quantity first, value as the
fallback, refuse when neither can be evaluated. Three-way match enforced at the last gate, not
only by a rule.

## Params

All seams are schema ids except `«state_dataset»`. Header fields are read from the annotation;
line fields from the `«line_items_field»` tuple; the five write-back fields are `data`-type
datapoints you create for the outcome. Make `«doc_number_field»` `edit: disabled` — an operator
hand-editing the ERP number defeats the duplicate guard.

The matched PO number and item come from **matching**, never from the page. That is the join
between the master-data import half and the export half.

## Settings and secrets (per environment)

```json
{ "api_base_url": "https://<middleware-host>", "token_path": "/API/GetToken",
  "create_path": "/API/V1/<create-endpoint>", "attach_path": "/API/V1/<attach-endpoint>",
  "invoice_status": "A", "posting_date_today": true, "request_timeout_s": 30 }
```

`invoice_status` is RBKP `RBSTAT`: **`"A"` parks, `"5"` posts.** Never default to posting.
`posting_date_today` is on by default: the customer's own worked example posted the run date
for a document dated months earlier, and a document-date default would have failed the first
time an invoice arrived from a closed period. Secrets: `api_key`, `user_id`, `user_pw`.

## Adapt

- **Payload keys** in `build_create_body` / `build_attach_body` carry BAPI semantics
  (`INVOICE_IND`, `DOC_DATE`, `PSTNG_DATE`, `REF_DOC_NO`, `COMP_CODE`, `RBSTAT`, `PO_ITEM`…).
  Rename them to your middleware's contract, **copied verbatim from a working call** — one
  deployment's guide documented the internal RFC signature (`IV_*`) which the middleware
  rejected, and its casing differed between endpoints (`Fiscal Year` vs `Fiscal year`).
- **Response keys** are module constants: `RESPONSE_ENVELOPE`, `RETURN_TABLE_KEY`,
  `DOC_NUMBER_KEY`, `FISCAL_YEAR_KEY`, `ARCHIVE_ID_KEY` (note the trailing period one API
  shipped), `ALREADY_EXISTS_KEY`. `NUMBER` in BAPIRET2 arrived as a JSON integer, not the
  documented zero-padded string — the scan coerces with `str()`.
- Conventions that bit on the wire: header is an **array of exactly one object**; dates
  `YYYYMMDD` (while the same integration's import APIs used 14-digit stamps); flags are the
  strings `"X"` / `""`; ids keep leading zeros; amounts are JSON numbers; item amounts are in the
  header currency; credit memos are positive amounts with the indicator initial.

## Questions to settle with the customer before go-live

1. Is there a **read-back endpoint** (by reference number + company code)? Without it a
   timed-out create stays a manual reconciliation.
2. Is SAP's **duplicate check on `REF_DOC_NO`** active for these company codes?
3. **Park or post** at go-live?
4. Is a goods-receipt reference required on GR-based lines? (Measured: not required where
   `GR_BASED_INVOICE` was blank even though a GR flag was set.)

See `sap-reference` → *Writing invoices into SAP*.
