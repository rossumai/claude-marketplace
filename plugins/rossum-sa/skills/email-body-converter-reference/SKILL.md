---
name: email-body-converter-reference
description: Email Body Converter (also spelled "convertor") reference — Rossum's hosted webhook extension that converts an inbound email's HTML body into a PDF and imports it as a new document/annotation, for when the invoice or order data lives in the email body itself rather than in an attachment. Covers setup (a manually created Webhook on email.received pointing at a Rossum-hosted service URL — NOT a one-click Store tile), regional hosting (service in EU1 + US; other-region orgs point cross-region), the full settings schema including the keys the KB article omits (queue_ids, minimal_email_character_count, skip_if_supported_files_present, skip_if_mime_types_present, convert_attachments, html_style/txt_style, create_document_only, include_header_in_pdf, header_style, skip_if_email_is_a_reply, send_email_body_as_plain_document), the second upload.created /convert-files mode for converting uploaded HTML/TXT files, and interactions with no-attachment bounces and other email.received hooks. Use whenever the user asks about processing emails with no attachments, invoices or orders sent in the email body, converting email bodies or HTML/TXT files to PDF, "email body converter/convertor", or why Rossum ignores the email body — even if they don't name the extension. NOT for populating schema fields from email metadata or body text (email_header:* / email_body:* rir_field_names — rossum-reference) and not for structured XML/JSON e-invoice import (sfi-reference).
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
