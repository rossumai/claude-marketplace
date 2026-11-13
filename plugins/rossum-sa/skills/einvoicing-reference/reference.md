# E-Invoicing in Rossum — Full Reference

Every fact here carries a tag saying how it was established:

- **[source]** — read in the service's implementation.
- **[live]** — observed on a real Rossum org or on B2Brouter staging.
- **[B2Brouter docs]** — B2Brouter's public API documentation.
- **[not verified]** — plausible but unchecked; confirm before relying on it.

All IDs, hostnames and values in examples are placeholders.

## Contents

0. [Choosing a route](#0-choosing-a-route)
1. [B2Brouter extension — how it works](#1-b2brouter-extension--how-it-works)
2. [Prerequisites](#2-prerequisites)
3. [B2Brouter extension setup](#3-b2brouter-extension-setup)
4. [Settings reference](#4-settings-reference)
5. [B2Brouter API versions, hosts and the canonical JSON](#5-b2brouter-api-versions-hosts-and-the-canonical-json)
6. [Field mapping](#6-field-mapping)
7. [Validation and showing it to users](#7-validation-and-showing-it-to-users)
8. [Visualization](#8-visualization)
9. [Re-extract and recovery](#9-re-extract-and-recovery)
10. [France: the B2Brouter Store jobs](#10-france-the-b2brouter-store-jobs)
11. [Hosted e-invoice upload import](#11-hosted-e-invoice-upload-import)
12. [Sproom](#12-sproom)
13. [Troubleshooting](#13-troubleshooting)
14. [Calling the B2Brouter API](#14-calling-the-b2brouter-api)

---

## 0. Choosing a route

| Route | How e-invoices arrive | Validation (EN 16931 etc.) | Mapping | Use when |
|---|---|---|---|---|
| **B2Brouter E-Invoicing extension** (§1–9) | B2Brouter network polling (e.g. Peppol) **and/or** files uploaded to Rossum by API, UI or email | **Yes** — XSD + schematron by B2Brouter | B2Brouter canonical JSON (JMESPath) + optional XPath on the XML | Rossum is the receiver, or validation is wanted |
| **Hosted e-invoice upload import** (§11) | An upstream system uploads the XML with file-role metadata (Coupa, a network poller such as Sproom §12) | No | XPath (SFI configuration model) | Something else receives the e-invoice and hands it to Rossum |
| **Plain SFI** (`sfi-reference`) | Any upload or email | No | XPath / JSONPath | Structured files, e-invoice or not, without validation |
| **No structured route** | — | — | OCR on the PDF | Fallback when no configuration triggers |

Decision questions:

1. **Does Rossum receive from an e-invoicing network?** Via B2Brouter → the extension. Via Sproom → §12. Via Coupa (CIB 2.0) → `coupa-baseline-reference` §8.
2. **Is EN 16931 / national-CIUS validation required?** Only the B2Brouter extension validates. It also validates e-invoices that arrive by **email or API upload** — no network registration needed, but a B2Brouter account to import them into is (`b2b_router_account_id_upload`) [source].
3. **Does an upstream system supply the PDF and attachments with roles?** → hosted upload import.
4. **Otherwise** → plain SFI.

Production orgs run plain SFI hooks and B2Brouter extension hooks side by side, on different queues [live].

---

## 1. B2Brouter extension — how it works

The extension is a **plain webhook** (`extension_source: custom`, no `hook_template`) pointing at a Rossum-hosted service, the e-invoice dispatcher [live]. The dispatcher answers each event at once with `202` and a poll URL, and runs the work as a background job; Rossum polls that URL until the job finishes (up to the hook's `max_polling_time_s`) [source]. A `200` means "nothing to do" (e.g. the upload contained no e-invoice) [source].

### Events and what they do [source]

| Event | What happens |
|---|---|
| `upload.created` | Each uploaded file that is an e-invoice is imported (see below). Other files in the same upload go through normal extraction. |
| `invocation.scheduled` / `invocation.manual` | **Network poll**: for each account in `b2b_router_account_id`, list received invoices not yet acknowledged, download the original, upload it to the hook's queue (which fires `upload.created`), then acknowledge it in B2Brouter. Up to 30 invoices per run (a service default). |
| `annotation_status.changed` | Only reacts to **Re-extract** (status → `importing` from anything but `created`): runs the recovery in §9. |

Any other event is rejected [source].

### What counts as an e-invoice [source]

- An XML file (`application/xml`, `text/xml`).
- A PDF with an embedded invoice XML (ZUGFeRD/Factur-X/XRechnung PDF). The XML is chosen by the PDF's Factur-X/ZUGFeRD XMP file name, then the well-known names `factur-x.xml`, `xrechnung.xml`, `zugferd-invoice.xml`, then the attachment relationship (`/Alternative`, `/Data`, `/Source`), then any well-formed XML attachment not marked `/Supplement`. A named XML that is not well-formed fails the import instead of falling back.

During polling, a downloaded document that is **not** an e-invoice is **acknowledged and skipped** — it never reaches Rossum [source].

### The import, per e-invoice [source, live]

1. **B2Brouter ID.** Reused from the document's metadata (`einvoice_id`) when present — polled invoices and retries carry it — otherwise the file is imported into `b2b_router_account_id_upload` as a received, acknowledged invoice. It then appears in B2Brouter with `from_net: uploaded` and `state: new` [live].
2. **Documents.** A visualization PDF is built (§8) and becomes the annotation's document. For a PDF upload the embedded XML is stored separately.
3. **Validation and canonical JSON.** The XML is validated by B2Brouter (§7) and B2Brouter's normalized JSON of the invoice is downloaded.
4. **Mapping.** Canonical JSON → fields, then `xml_mapping` on the XML (§6).
5. **Annotation.** Created with training disabled and flagged as an e-invoice, plus a document relation of type `einvoice`.

Documents created per e-invoice (file names are stable on purpose — SAs rely on them):

| Document | Name | Where |
|---|---|---|
| Visualization PDF | `<name>.pdf` | the annotation's document |
| Embedded XML (PDF uploads only) | `<name>-einvoice.xml` | `einvoice` relation |
| Uploaded XML (XML uploads) | the original file | `einvoice` relation |
| Canonical JSON | `<name>-canonical.json` | `einvoice` relation |
| Validation report | `<name>-validation.json` | `einvoice` relation |

`<name>` is the uploaded file name without extension. Polled invoices are uploaded as `einvoice<B2Brouter id>.xml` or `.pdf`. The uploaded original gets `attachment_status: filtered_by_hook_custom` and no annotation of its own [live]. Purging the annotation also removes the relation documents and the source XML [live].

---

## 2. Prerequisites

- [ ] The org group has the **`einvoicing` feature** enabled (Rossum-side). Without it every e-invoice fails with "E-invoicing feature is not enabled." [source]
- [ ] A **B2Brouter account** per receiving company and an **API key** that can read it.
- [ ] For uploads: an account for `b2b_router_account_id_upload`. A buyer company on the invoice that is not registered in that account is rejected by B2Brouter with a `422` [reported from a production deployment; not re-verified].
- [ ] The queue accepts XML: `application/xml` and `text/xml` in `accepted_mime_types` [live]. For XML specifics (organization group `store_only_mime_types`) see `sfi-reference`.
- [ ] Optional schema fields the extension fills when present (§6.5): `e_invoice_id`, `issuer_email`, `e_invoice_validation_json`.
- [ ] A hook token owner who can create documents and annotations in the queue.

---

## 3. B2Brouter extension setup

### Dispatcher URL

| Region | URL |
|---|---|
| EU1 | `https://elis.einvoice-dispatcher.rossum-ext.app/api/v1/dispatch` [live] |
| EU2 | `https://shared-eu2.einvoice-dispatcher.rossum-ext.app/api/v1/dispatch` [live] |
| Other regions | Ask Rossum. The service is deployed to the EU regions only [source]; a health check is no evidence — the host answers for any name [live]. |

### The hook

There is no Store tile for the importer (the Store's B2Brouter tiles are the French jobs in §10) [live]. Create a **Webhook** (Extensions → My extensions) with:

```json
{
  "type": "webhook",
  "name": "E-invoicing B2Brouter",
  "queues": ["https://<org>.rossum.app/api/v1/queues/<queue-id>"],
  "events": ["upload.created", "annotation_status.changed",
             "invocation.scheduled", "invocation.manual"],
  "sideload": ["features"],
  "config": {
    "url": "https://elis.einvoice-dispatcher.rossum-ext.app/api/v1/dispatch",
    "schedule": {"cron": "*/10 * * * *"},
    "timeout_s": 360,
    "max_polling_time_s": 3600
  },
  "settings": {
    "b2b_router_base_url": "https://app.b2brouter.net",
    "b2b_router_account_id": [<account-id>],
    "b2b_router_account_id_upload": <account-id>,
    "configuration": {"fields": []}
  }
}
```

Secret (hook **Secrets**, entered by a person in the UI): `{"b2b_router_api_key": "<key>"}` [source].

Notes:

- **Polling needs exactly one queue** on the hook ("The hook must be connected to exactly one queue.") [source]. One hook per receiving queue.
- Drop `invocation.scheduled` and `b2b_router_account_id` for an upload-only setup (validation of emailed/uploaded e-invoices without network polling).
- `timeout_s: 360`, `max_polling_time_s: 3600`, cron `*/10 * * * *` are the values production hooks run with [live]. The import job is sized to finish within a one-hour poll window, so a short `max_polling_time_s` can report a failure for an import that later succeeds [source; the 300 s default was seen on a working dev hook, live].
- `sideload: ["features"]` is recommended by the service (it saves a lookup of the feature flag); hooks without it work [source, live].
- For a dated B2Brouter API version, set `b2b_router_api_version` and use `https://api.b2brouter.net` (§5).

---

## 4. Settings reference

Defaults are the service's [source].

| Key | Default | Meaning / when to change |
|---|---|---|
| `b2b_router_base_url` | required | B2Brouter host; must match the API version (§5). Staging: `https://app-staging.b2brouter.net` (Legacy) or `https://api-staging.b2brouter.net` (dated). |
| `b2b_router_api_version` | `"Legacy"` | Any version B2Brouter supports, e.g. `"2026-06-26"`; sent as the `X-B2B-API-Version` header. Changes the canonical JSON shape (§5) — change mapping and version together. |
| `b2b_router_account_id` | none | Int or list of ints. Accounts polled for received invoices. None = no network polling. |
| `b2b_router_account_id_upload` | none | Account Rossum-uploaded e-invoices are imported into. None = uploaded e-invoices fail with "B2Brouter account ID for upload not provided, import of e-invoices from any source other than B2Brouter is not possible." |
| `api_upload_enabled` | `true` | `false` = e-invoices uploaded by API/UI are treated as normal documents (OCR). Invoices uploaded by the poll itself are always imported. |
| `email_upload_enabled` | `true` | `false` = e-invoices arriving by email are treated as normal documents. |
| `treat_warnings_as_errors` | `false` | `true` = `valid_with_warnings` also adds the error message (§7). |
| `configuration.extends` | `"b2b_router_native_json"` | The only value. |
| `configuration.fields` | `[]` | Canonical JSON → schema field mappings (§6). |
| `configuration.pdf_file` | — | Accepted but not used by this extension; use `visualization`. |
| `xml_mapping` | `[]` | XPath mappings on the original XML (§6.4). |
| `visualization.sources` | `["original_pdf", "xml_invoice_attachment_pdf", "b2b_canonical_pdf", "xml_noninvoice_attachments_pdf"]` | §8. |
| `visualization.merge` | `false` | §8. |

Secrets: `b2b_router_api_key`.

---

## 5. B2Brouter API versions, hosts and the canonical JSON

B2Brouter runs two API generations [B2Brouter docs, live]:

| | `Legacy` (extension default) | Dated version, e.g. `2026-06-26` |
|---|---|---|
| Host | `https://app.b2brouter.net` (staging `app-staging.`) [live] | `https://api.b2brouter.net` (staging `api-staging.`) [live, B2Brouter docs] |
| Supplier party | `invoice.client` with `name`, `taxcode`, `address`, `postalcode`, `city`, `country`, `party_identification` [live] | `invoice.contact` with `name`, `tin_value`, address fields [live] |
| Buyer party | `invoice.company` with `taxcode` [live] | `invoice.company` with `tin_value`; also `invoice.account` [live] |
| Header charges | one `invoice.charge_amount` + `charge_reason`; `discount_amount` [live] | `invoice.allowance_charges[]` — B2Brouter returns it only on request; whether the extension's download includes it: [not verified] |

A hook on one generation silently maps nothing from selectors written for the other.

Fields common to both (selectors start at `invoice.`) [live]: `number`, `date`, `due_date`, `tax_point_date`, `ponumber`, `buyer_reference`, `currency`, `subtotal`, `total`, `payable_amount`, `taxes[]` (`percent`, `base`, `amount`), `lines[]` (`description`, `quantity`, `price`, `gross_amount`, `taxes[]`, `article_code`, …), `is_credit_note`, `payment_method` (numeric code), `payment_terms`, `remittance_information` (the payment reference, e.g. a Norwegian KID), `contact_iban`, `contact_bic`, `document_type_code` (e.g. `xml.ubl.invoice.bis3`), `language`, delivery fields, `invoice_references[]`. Legacy also has `payment_method_info` (human-readable text with `<br />` separators).

**Bank details warning [live, one controlled test].** `contact_iban` and `payment_method_info` were filled from B2Brouter's **stored contact** for the supplier, not from the invoice: an invoice whose XML carried a different (domestic) account still showed the IBAN from the supplier's earlier invoice, and the domestic number appeared nowhere in the canonical JSON. B2Brouter's `bank_account` object is write-only and is not returned [B2Brouter docs]. Map bank details from the XML (§6.6).

The canonical JSON of each annotation is in its `einvoice` relation as `<name>-canonical.json` — open it to see exactly what a selector can reach.

---

## 6. Field mapping

### 6.1 `configuration.fields`

A list of field mappings merged **by `schema_id`** over a built-in default that maps only `document_id ← invoice.number`: keys you set override, new `schema_id`s are appended [source].

```json
{
  "configuration": {
    "fields": [
      {"schema_id": "date_issue", "selectors": ["invoice.date"], "skip_non_existing_schema_ids": true},
      {"schema_id": "amount_total_tax",
       "selectors": ["round(sum(invoice.taxes[*].amount || `[]`), `2`)"],
       "skip_non_existing_schema_ids": true},
      {"schema_id": "document_type", "selectors": ["invoice.is_credit_note"],
       "mapping": {"true": "credit_note", "false": "tax_invoice", "__default__": "tax_invoice"},
       "skip_non_existing_schema_ids": true},
      {"schema_id": "line_items", "selectors": ["invoice.lines"], "skip_non_existing_schema_ids": true,
       "fields": [
         {"schema_id": "item_description", "selectors": ["description"], "skip_non_existing_schema_ids": true},
         {"schema_id": "item_quantity", "selectors": ["quantity"], "skip_non_existing_schema_ids": true},
         {"schema_id": "item_rate", "selectors": ["taxes[0].percent"], "skip_non_existing_schema_ids": true}
       ]}
    ]
  }
}
```

- **Selectors** are JMESPath against the canonical JSON; the first selector that returns a value wins. Extra functions: `upper(value)`, `lower(value)`, `round(value, n)` (whole number for `n <= 0`; nothing if `value` is not a number) [source].
- **`mapping`** translates the selected value; it must contain `__default__` [source].
- **Multivalues**: a `selectors` list that returns an array, plus child `fields` with selectors relative to each row [source, live].
- **`skip_non_existing_schema_ids` defaults to `false`** here: a mapping to a `schema_id` missing from the queue's schema **fails the import** [source]. Set it to `true` on every field, as production configs do [live].
- Empty and null values are not written, so an unmatched field stays empty and unchecked rather than "filled with nothing" [source].
- Dates use `%Y-%m-%d` [source].

### 6.2 Common recipes [live]

| Target | Selector |
|---|---|
| Payment reference (KID, OCR reference) | `invoice.remittance_information` |
| Supplier address (Legacy) | ``join(', ', [invoice.client.address, invoice.client.address2, invoice.client.postalcode, invoice.client.city, invoice.client.country][?@])`` |
| Supplier address (dated) | same with `invoice.contact.*` |
| Payment method code as text | `to_string(invoice.payment_method)` + a `mapping` table |
| Header charge as one table row (Legacy) | ``[invoice][?charge_amount && charge_amount != `0.0`].{charge_amount: charge_amount}`` on a multivalue with child `charge_amount` |
| Charges (dated) | `invoice.allowance_charges[?allowance_charge_indicator == 'charge']` [selector seen in production; data presence not verified] |
| Original invoice of a credit note (dated) | `invoice.invoice_references[?reference_type == 'amend'][].number \| [0]` |
| E-invoice format | `invoice.document_type_code` |

### 6.3 Merge order

Canonical mappings first; `xml_mapping` results then **override** canonical values with the same `schema_id`; then the extension adds its own fields (§6.5) only where nothing was mapped [source].

### 6.4 `xml_mapping` — XPath on the original XML

```json
{
  "xml_mapping": [
    {
      "trigger_condition": {"file_type": "xml"},
      "fields": [
        {"schema_id": "iban",
         "selectors": ["/*[local-name()='Invoice']/*[local-name()='PaymentMeans']/*[local-name()='PayeeFinancialAccount']/*[local-name()='ID']"]}
      ]
    }
  ]
}
```

- A list of configurations; **every** configuration whose `trigger_condition` matches is applied, later ones win on conflicts [source]. Add a `selector` to the trigger (e.g. `/*[local-name()='CreditNote']`) to scope a configuration to one document type.
- Selectors are XPath 1.0 (syntax: `sfi-reference`). Both namespace-safe `local-name()` paths and prefixed relative paths such as `cac:LegalMonetaryTotal/cbc:PayableAmount/text()` work in production [live].
- **`skip_non_existing_schema_ids` defaults to `true`** here — the opposite of `configuration.fields` [source].
- Use it for anything the canonical JSON lacks or gets wrong: bank account, national fields (production maps a Polish KSeF bank account from `//*[local-name()='NrRB']/text()` [live]), document-level values you want exactly as sent.

### 6.5 Fields the extension fills itself [source, live]

Filled only if the queue schema has the field and your mapping has not filled it:

| `schema_id` | Value |
|---|---|
| `e_invoice_id` | B2Brouter invoice ID — the key the French jobs (§10) and reconciliation use |
| `issuer_email` | Sender address of the email, for e-invoices uploaded by email |
| `e_invoice_validation_json` | Validation result, only when it counts as an error (§7) |

Recommended definition: hidden, read-only data fields:

```json
{"category": "datapoint", "id": "e_invoice_id", "label": "E-invoice ID", "type": "string",
 "hidden": true, "ui_configuration": {"type": "data", "edit": "disabled"},
 "constraints": {"required": false}, "rir_field_names": [], "default_value": null}
```

### 6.6 Bank account recipe

Map the account from the XML, not from `contact_iban` (§5):

- **UBL / Peppol BIS (BT-84)**: the selector in §6.4. It holds an IBAN or a domestic account number, whatever the supplier sent. Verified live: it replaced a stale canonical IBAN [live].
- **CII (XRechnung/ZUGFeRD)**: `//*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='IBANID']`, with `…/*[local-name()='ProprietaryID']` for a domestic account [not verified].
- BIC (UBL): `/*[local-name()='Invoice']/*[local-name()='PaymentMeans']/*[local-name()='PayeeFinancialAccount']/*[local-name()='FinancialInstitutionBranch']/*[local-name()='ID']` [not verified].

### 6.7 Testing a mapping change

Mapping runs only at import. To re-run it on an existing e-invoice use Re-extract — which **replaces** the annotation (§9) — or upload a test copy. Test on a disposable annotation, never on one a user has worked on.

---

## 7. Validation and showing it to users

B2Brouter validates the structured XML (XSD, then the schematrons for the detected format, e.g. `CEN-EN16931-UBL.sch` and `PEPPOL-EN16931-UBL.sch`) [source, live].

| Status | Annotation gets "E-invoice is not valid."? |
|---|---|
| `valid` | no |
| `valid_with_warnings` | only with `treat_warnings_as_errors: true` |
| `invalid` | **yes** |
| `not_applicable` | **yes** (format not validatable; reported for old formats such as ZUGFeRD 1.0 and openTRANS [reported from a production deployment; not re-verified]) |

The message is an annotation-level **error** and blocks automation; the invoice is still imported [source, live]. The result is **not** stored on the B2Brouter invoice (`state` stays `new`, no error message there) [live].

Where the result lives:

- **Always**: `<name>-validation.json` in the `einvoice` relation [source, live]:
  ```json
  {"validation_result": {
    "info": {"document_type_code": "xml.ubl.invoice.bis3", "model": "Invoice"},
    "status": "invalid",
    "xsd": {"status": "valid", "errors": []},
    "sch": {"status": "invalid", "schematrons": ["CEN-EN16931-UBL.sch", "PEPPOL-EN16931-UBL.sch"],
            "errors": [{"code": "BR-CO-16", "category": "CEN-EN16931-UBL.sch",
                        "message": "Amount due for payment (BT-115) = …", "property_path": "Invoice/LegalMonetaryTotal"}],
            "warnings": []}}}
  ```
- **Only in the error cases above**: schema field `e_invoice_validation_json`, holding the inner object **without** the `validation_result` wrapper [live].

### Recipe: readable errors on the annotation [live]

1. Add `e_invoice_validation_json` (hidden, read-only data field, §6.5).
2. Add a visible formula field in a visible section, e.g. `e_invoice_validation_errors`:

```python
import json
raw = field.e_invoice_validation_json
r = json.loads(raw) if not is_empty(raw) else {}
msgs = []
for part in ("xsd", "sch"):
    for e in (r.get(part) or {}).get("errors") or []:
        if e.get("message"):
            msgs.append(((e.get("code") + ": ") if e.get("code") else "") + e["message"])
"\n".join(msgs)
```

Verified on a live queue: `import json` is allowed in formulas, and the field showed `BR-CO-16: Amount due for payment (BT-115) = Invoice total amount with VAT (BT-112) -Paid amount (BT-113) +Rounding amount (BT-114).` [live]. For a valid invoice the input is empty and the formula returns `""`. To also list warnings, add a loop over `sch.warnings`.

---

## 8. Visualization

`visualization.sources` is an ordered list; sources a given e-invoice does not have are skipped [source]:

| Source | What |
|---|---|
| `original_pdf` | The uploaded PDF/A (ZUGFeRD/Factur-X); absent for XML uploads |
| `xml_invoice_attachment_pdf` | A PDF embedded in the XML marked as the invoice (type code 130) |
| `b2b_canonical_pdf` | B2Brouter's rendering |
| `b2b_xslt_pdf` | B2Brouter's alternative XSLT rendering (falls back to the canonical rendering when B2Brouter has none) |
| `xml_noninvoice_attachments_pdf` | Other PDFs embedded in the XML (delivery notes, …) |

`merge: false` (default) uses the first available source; `merge: true` concatenates all available ones in order. No source available = the import fails with "No visualization sources available - check your visualization configuration" [source]. Production configurations use `merge: true`, often with `b2b_xslt_pdf` first for national formats [live].

---

## 9. Re-extract and recovery

Clicking **Re-extract** on an e-invoice annotation (or any annotation whose document is an e-invoice, e.g. after a failed import) runs a recovery [source, live]:

1. The original e-invoice is fetched — from B2Brouter by `einvoice_id` when known (so B2Brouter gets **no duplicate**), else the annotation's own document.
2. It is uploaded to the queue again, which runs the full import with the **current** settings.
3. The original annotation is **deleted and purged**.

The result is a **new annotation with a new ID**; the old one's edits, notes, comments and history are gone. The hook reports "E-invoice recovery completed." [live]. Use it after a mapping change or to retry a failed import — never on documents users have worked on.
