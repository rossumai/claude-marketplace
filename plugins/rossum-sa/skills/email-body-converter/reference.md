# Rossum Email Body Converter — Full Reference

The Email Body Converter is a **Rossum-maintained hosted webhook** that converts an inbound email's HTML body into a PDF and uploads it to a queue as a new document, so that data living *in the email body* (not in an attachment) gets imported and extracted like any other document. It can also convert HTML and TXT **attachments** into PDFs, and — via a second, undocumented endpoint — HTML/TXT **uploads**.

**Sourcing.** Grounded in the official KB article ([Email Body Converter](https://knowledge-base.rossum.ai/docs/email-body-converter), updated 2025-09-15) and the converter service's own OpenAPI spec (`https://elis.rossum.ai/svc/email-converter/api/openapi.json`, title `elis_webhook_email_converter`, v0.1.0). <!-- PROBE: sourcing — add live-probe line (org 313278, June 2026) -->
Every behavioral claim below is tagged:

- **[KB]** — stated verbatim in the KB article (or another official prose page, cited inline).
- **[spec]** — present in the service's OpenAPI spec (key names/types/defaults only; the spec gives no prose semantics).
- **[live]** — verified by a live probe (org 313278, June 2026).
- **[unverified]** — inference or untested; treat with care.

> Official spelling is "Email Body Convert**er**" — the `…/email-body-convertor` KB slug returns 404. Searches for "convertor" should land here too.

## Contents

- [What it is & when to use it](#what-it-is--when-to-use-it)
- [How it works](#how-it-works)
- [Setup](#setup)
- [Configuration reference](#configuration-reference)
- [Recipes](#recipes)
- [The /convert-files (upload.created) mode](#the-convert-files-uploadcreated-mode)
- [Interactions & gotchas](#interactions--gotchas)
- [Troubleshooting](#troubleshooting)

## What it is & when to use it

**The platform default it works around.** Rossum imports email *attachments*, not bodies:

- [KB, [Managing Documents](https://knowledge-base.rossum.ai/docs/managing-documents-in-workspaces-and-queues)] "Rossum will select attachments that it can process as documents. … It is important to note that Rossum does not process the body of the email itself – HTML documents are not processed."
- [Rossum University, [Document ingestion](https://rossum.university/docs/courses/basic-end-to-end-flow-with-rossum/document-ingestion)] "By default, email bodies are ignored. They can however be converted to a PDF file for processing."

**What the extension does.** [KB] "A simple extension that can convert the email HTML body into a PDF and upload it to a queue as a new document. An additional feature is that it can also convert HTML attachments into PDFs." Per the config schema it converts `html` and `txt` attachments [KB], and the service exposes additional capabilities (header rendering, plain-text body mode, an upload-conversion endpoint) that the article does not mention [spec].

**Use it when** vendors send invoices/orders *as* the email (HTML tables in the body, body-only notifications from ERP systems, forwarded order confirmations) — anything where a body-only email would otherwise import nothing and optionally bounce back "no processable attachments" (see [Interactions & gotchas](#interactions--gotchas)).

**Don't confuse it with field-level email sources.** Schema datapoints can pull values straight from the email via `rir_field_names` (`email_header:subject|from|to|reply-to|message-id|date`, `email_body:text_html|text_plain`, `email:*` values set by an `email.received` hook) — that mechanism *populates fields on an annotation created from some other file* and is covered in `rossum-reference`. The Email Body Converter instead creates a **new document/annotation from the body itself**. For regex-parsing the body into fields with a custom function, see the KB article [Extracting Data from Email Content with Custom Function](https://knowledge-base.rossum.ai/docs/extracting-data-from-email-content-with-custom-function).

## How it works

The extension is an `email.received` webhook. The platform contract (official API docs, `elis.rossum.ai/api/docs`):

- The hook **payload** carries the email's metadata and content: `files` (attachment metadata), `headers` (same values as `email_header:*` — from, to, reply-to, subject, message-id, date), and `body` with `body_text_plain` and `body_text_html`.
- The hook **response** may return `files` (which incoming files to import, with optional `email:*` values) and `additional_files`. Each `additional_files` entry has `document` (required — "URL of the document object that should be included. If document belongs to an annotation it must also belong to the same queue as email inbox."), `values`, and `import_document` — verbatim: "Set to true if Rossum should import document and create an annotation for it, otherwise it will be just linked as an email attachment. Only applicable if document hasn't already an annotation attached."

The converter service's response model matches this contract exactly (`files` + `additional_files` with `import_document`) [spec]. The end-to-end flow — service renders the HTML body to PDF, creates a document via the API as the hook's token owner, and returns it in `additional_files` with `import_document: true` so Rossum imports it and links it to the email — is consistent with all of the above but not spelled out in any single doc. <!-- PROBE: 1 — observe the actual linkage (document/annotation/email relations) and tag [live] -->

## Setup

[KB, verbatim] "Email body converter is a webhook maintained by Rossum. To use it, follow these steps:"

1. Log in to your Rossum account.
2. Navigate to **Extensions** → **My extensions**.
3. Click on **Create extension**.
4. Fill in the following fields:
   1. Name: `Email body converter`
   2. Trigger events: `email.received`
   3. Extension type: `Webhook`
   4. URL (see below)
   5. In "Advanced settings" select **Token owner** (should have Admin access)
5. Click **Create the webhook**.
6. Fill in the `Configuration` field (see [Configuration reference](#configuration-reference)).

It is **not a one-click Rossum Store tile** — the only documented install path is the manual webhook above [KB; the generic Store KB articles contain no mention of it].

**Webhook URLs by environment** [KB, verbatim table]:

| Environment | Webhook URL |
|---|---|
| EU1 Ireland | `https://elis.rossum.ai/svc/email-converter/api/v1/convert` |
| EU2 Frankfurt | — (not available) |
| US east coast | `https://us.app.rossum.ai/svc/email-converter/api/v1/convert` |
| Japan Tokyo | — (not available) |

For multi-tenant `*.rossum.app` organizations, use the URL of the cluster hosting your org. <!-- PROBE: 0 — confirm which URL serves a *.rossum.app org (egarage) and whether the org's own domain proxies the svc path -->

**Creating the same hook via API** (equivalent of the UI steps): <!-- PROBE: fixture — replace with the exact payload shape accepted live -->

```json
POST /v1/hooks
{
  "name": "Email body converter",
  "type": "webhook",
  "queues": ["https://<domain>/api/v1/queues/<id>"],
  "events": ["email.received"],
  "active": true,
  "config": {"url": "https://elis.rossum.ai/svc/email-converter/api/v1/convert"},
  "token_owner": "https://<domain>/api/v1/users/<admin-user-id>",
  "settings": { "configurations": [ { "queue_ids": [<id>] } ] }
}
```

The token owner must have Admin access [KB]; the service uses the hook's `rossum_authorization_token` to create the document [unverified — implied by the request model's `rossum_authorization_token`/`base_url` fields [spec]].

## Configuration reference

The hook `settings` hold a `configurations` list; [KB] "Each object in the `configurations` list represents a specific configuration (distinguished by the queue IDs)." A single configuration can serve multiple queues via `queue_ids` [KB].

All keys of one `configurations[]` entry (`Configuration` model [spec]):

| Key | Type (default) | Semantics | Tag |
|---|---|---|---|
| `queue_ids` | array[int] (required) | "List of queue IDs this configuration applies to. A single configuration can be used for multiple queues." | [KB] |
| `minimal_email_character_count` | int (`0`) | "Minimum number of characters in the email body to convert it to PDF." Bodies shorter than this are not converted. | [KB] |
| `skip_if_supported_files_present` | bool (`false`) | "Skip conversion if supported files are present (`true` to skip, `false` to convert the email body to PDF). Supported files include email attachments supported by Rossum and any additional files converted to PDF as part of the webhook call (e.g., HTML attachments converted to PDF)." | [KB] <!-- PROBE: 2 — upgrade to [live] --> |
| `skip_if_mime_types_present` | array[string] | Not documented. Presumably: skip body conversion when an attachment with one of these MIME types is present (a finer-grained `skip_if_supported_files_present`). | [spec], semantics [unverified] |
| `convert_attachments` | array[string] | "List of attachment types to convert to PDF. Supported values: `html`, `txt`." | [KB] <!-- PROBE: 5 — upgrade to [live] --> |
| `html_style` | string\|null | Not documented. Presumably the HTML-body sibling of `txt_style`: CSS injected as a style tag when rendering the HTML body/attachments. | [spec], semantics [unverified] <!-- PROBE: 3 may touch this --> |
| `txt_style` | string\|null | "Specifies the style for TXT files, which are first converted to HTML and then to PDF. This configuration is added as an HTML style tag to affect the appearance of the TXT in the converted PDF." Example: `"@page { size: letter landscape; margin: 2cm; } pre { white-space: pre-wrap; }"` | [KB] |
| `create_document_only` | bool (`false`) | Not documented. Presumably: create/link the document without importing it as an annotation (cf. the `import_document` flag in the hook response contract). | [spec], semantics [unverified] <!-- PROBE: 4 — observe actual behavior --> |
| `include_header_in_pdf` | bool (`false`) | Not documented. Presumably: render email headers (From/To/Subject/Date) into the generated PDF. | [spec], semantics [unverified] <!-- PROBE: 3 — observe which headers appear --> |
| `header_style` | string\|null | Not documented. Presumably CSS for the rendered header block (with `include_header_in_pdf`). | [spec], semantics [unverified] <!-- PROBE: 3 --> |
| `skip_if_email_is_a_reply` | bool (`false`) | Not documented. Presumably: don't convert bodies of reply emails (quoted threads would produce junk documents). | [spec], semantics [unverified] <!-- PROBE: 6 — observe; which signal (subject vs In-Reply-To) --> |
| `send_email_body_as_plain_document` | bool (`false`) | Not documented. Presumably: submit the body as a plain-text-derived document instead of rendering the HTML. | [spec], semantics [unverified] |

**Full example configuration** [KB, verbatim values]:

```json
{
  "configurations": [
    {
      "queue_ids": [172636],
      "minimal_email_character_count": 5,
      "skip_if_supported_files_present": false,
      "convert_attachments": ["html", "txt"],
      "txt_style": "@page { size: letter landscape; margin: 2cm; } pre { white-space: pre-wrap; }"
    }
  ]
}
```

## Recipes

**Convert the body only when nothing else is processable** — the most common production setup: vendors usually attach PDFs, but some send body-only emails.

```json
{
  "configurations": [
    {
      "queue_ids": [<id>],
      "minimal_email_character_count": 50,
      "skip_if_supported_files_present": true
    }
  ]
}
```

With `skip_if_supported_files_present: true`, an email carrying a processable attachment imports only that attachment; a body-only email gets its body converted [KB semantics]. <!-- PROBE: 2 — confirm live --> The `minimal_email_character_count` floor avoids converting one-line bodies ("see attached… oops, forgot the attachment") into junk documents — though note such an email then imports nothing and may trigger the no-attachment notification (see [Interactions & gotchas](#interactions--gotchas)).

**Also convert HTML/TXT attachments** (e.g. HTML invoices attached as files):

```json
{
  "configurations": [
    {
      "queue_ids": [<id>],
      "skip_if_supported_files_present": true,
      "convert_attachments": ["html", "txt"],
      "txt_style": "@page { size: A4; margin: 2cm; } pre { white-space: pre-wrap; }"
    }
  ]
}
```

Converted HTML/TXT attachments count as "supported files" for the skip logic [KB: "Supported files include … any additional files converted to PDF as part of the webhook call"] — so this combination converts attachments *and* skips the body when they exist.

**Render email headers into the PDF** — useful when the sender/subject/date are themselves data to extract: <!-- PROBE: 3 — confirm and show observed output -->

```json
{
  "configurations": [
    {
      "queue_ids": [<id>],
      "include_header_in_pdf": true,
      "header_style": "div.header { font-family: monospace; font-size: 10px; color: #444; }"
    }
  ]
}
```

(`include_header_in_pdf`/`header_style` are [spec]-only keys — semantics presumed, see the table above.)

## The /convert-files (upload.created) mode

The service exposes a **second endpoint that no KB article mentions** [spec]: `POST /api/v1/convert-files`, shaped for the **`upload.created`** hook event (request model: `action: "created"`, `event: "upload"`). It converts HTML/TXT **uploaded files** (API/UI uploads, not emails) into PDFs.

Its per-entry settings model (`FileConfiguration` [spec]):

| Key | Type (default) | Notes |
|---|---|---|
| `target_queue_id` | int (required) | Queue to place the converted document in. <!-- PROBE: 7 --> |
| `convert_attachments` | array[string] | As above (`html`, `txt`). |
| `include_upload_metadata` | bool (`false`) | [unverified] presumably copies upload metadata onto the result. |
| `include_upload_values` | bool (`false`) | [unverified] presumably carries `upload:*` values through to the converted document. |
| `html_style` / `txt_style` | string\|null | As above. |
| `create_document_only` | bool (`false`) | As above. |

To use it, create a second webhook with event `upload.created` pointing at `…/svc/email-converter/api/v1/convert-files`. <!-- PROBE: 7 — pin the exact settings wrapper shape and observed behavior; entire section currently [spec]-grounded, semantics [unverified] -->

## Interactions & gotchas

- **Regional availability.** EU1 Ireland and US east coast only [KB] — orgs on EU2 Frankfurt or Japan Tokyo have no listed converter URL. There is no documented workaround; pointing a webhook across regions is untested and likely unsupported [unverified].
- **Multiple `email.received` hooks** (e.g. converter + Advanced Email Filtering on the same queue): official API docs, verbatim — "If there are multiple hooks configured for the event, annotations are created only for files mentioned in all the responses (their values are merged together with the latest called hooks having the highest priority)." A filtering hook that omits files from its response can therefore veto imports; order and coexistence need care. [official docs; interplay with the converter's `additional_files` specifically: [unverified]]
- **No-attachment bounces/notifications.** A body-only email normally triggers the "no processable attachments" path: queue email-notification setting `email_with_no_attachments` (default `true`; the inbox-level `bounce_email_with_no_attachments` is deprecated in favor of it) sends the sender a rejection ("Unfortunately, we have not received any document in the email that we can process…"). Whether a successful body conversion suppresses this notification is not documented. <!-- PROBE: 1 — observe --> 
- **Stored email body is truncated at 4 kB** — the email *object*'s `body_text_plain`/`body_text_html` are "shortened to 4kB" (rossum.app OpenAPI). The hook payload is delivered at `email.received` time and may carry the full body, so conversion of long emails is likely unaffected — but this is [unverified]. <!-- PROBE: 1b optional -->
- **Inbox filters** (`filters.allowed_senders`/`denied_senders`, `document_rejection_conditions` like min resolution and MIME filters) are documented for *incoming attachments*; whether any of them apply to the converter-generated PDF is not documented [unverified].
- **Token owner** must have Admin access [KB]; a non-admin or deactivated token owner is the classic silent-failure mode for hosted webhooks [unverified for this extension specifically].
- **This extension vs. `email_body:*` fields.** `rir_field_names: ["email_body:text_html"]` copies raw body text into a *field* of an annotation created from some other file; the converter creates a *new document* from the body. They solve different problems and can coexist.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Body-only email imports nothing | Wrong region URL in `config.url`; queue not in `queue_ids`; body shorter than `minimal_email_character_count`; hook inactive or not bound to the inbox's queue | Hook logs; `queue_ids` vs the inbox's queue id; the region table above <!-- PROBE: 1/0 — confirm failure signatures observed live --> |
| Body converted even though a PDF was attached | `skip_if_supported_files_present` left `false` (default) | Set it `true` [KB] |
| Reply emails ("Re: …") get converted into junk documents | `skip_if_email_is_a_reply` left `false` (default) | Set it `true` ([spec]-only key, semantics [unverified]) <!-- PROBE: 6 --> |
| HTML/TXT attachment not converted | `convert_attachments` unset (it has no default) | Add `"convert_attachments": ["html", "txt"]` [KB] |
| Sender still gets "no processable attachments" rejection despite conversion | Notification interplay — see [Interactions & gotchas](#interactions--gotchas) | Queue email-notification settings <!-- PROBE: 1 --> |
| Everything configured, still nothing — org is on EU2/Japan | Converter not available in that region [KB] | Region table above |
