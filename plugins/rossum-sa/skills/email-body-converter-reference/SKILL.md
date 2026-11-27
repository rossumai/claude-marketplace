---
name: email-body-converter-reference
description: Email Body Converter (also "convertor") reference — Rossum's hosted webhook extension that turns an inbound email's HTML body into a PDF and imports it as a document, for when the invoice or order data is in the email body rather than an attachment. Covers setup (a manually created webhook on email.received pointing at a Rossum-hosted URL, not a Store tile), regional hosting, the full settings schema including keys the KB article omits, the upload.created /convert-files mode for HTML/TXT uploads, and interactions with other email.received hooks. Use whenever the user asks about emails with no attachments, invoices sent in the email body, converting email bodies or HTML/TXT files to PDF, or why Rossum ignores the email body — even without naming the extension. Not for email_header/email_body rir_field_names (rossum-reference) or structured XML/JSON e-invoices (sfi-reference).
user-invocable: false
---

# Email Body Converter Reference

The **Email Body Converter** is a Rossum-maintained hosted webhook ("A simple extension that can convert the email HTML body into a PDF and upload it to a queue as a new document") that exists to work around a platform default: **"Rossum does not process the body of the email itself"** — only attachments are imported. When the invoice, order, or other data arrives *in the email body*, this extension renders the body as a PDF so it enters the queue as a normal document and gets extracted. It can also convert HTML/TXT *attachments* to PDF, and (via a second, undocumented mode) HTML/TXT *uploads*.

See [reference.md](reference.md) for the full reference. Consult it when:

- Setting the extension up — it is a **manually created Webhook** on `email.received` pointing at a Rossum-hosted service URL, with a region table (EU1 + US only) and an admin **token owner** requirement.
- Choosing settings — all 12 `configurations[]` keys, including the seven that the KB article does not document (each tagged by how it was verified).
- Building "convert the body only when nothing else is processable" recipes (`skip_if_supported_files_present`), attachment conversion, or PDF styling.
- Wiring the `upload.created` → `/convert-files` mode to convert uploaded HTML/TXT files.
- Troubleshooting: body-only emails bouncing as "no processable attachments", nothing imported, replies being converted, attachments not converting.

> **Read this first:** the extension is **not in the Rossum Store** — you create the webhook yourself (Extensions → My extensions). The service is hosted in **EU1 and US**; orgs in other regions (EU2 Frankfurt, Japan Tokyo) point their webhook at one of those URLs cross-region. The webhook needs a **token owner with Admin access**.

Cross-references:

- `rossum-reference` — inbox/email API, the `email.received` payload/response contract, and the *field-level* email sources (`email_body:text_html`, `email_header:subject`, `email:*` in `rir_field_names`) which populate fields on an existing annotation; this extension instead creates a **new document**.
- `sfi-reference` — structured XML/JSON/e-invoice import (a different extension for machine-readable formats).
