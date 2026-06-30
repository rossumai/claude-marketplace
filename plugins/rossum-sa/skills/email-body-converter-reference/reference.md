# Rossum Email Body Converter — Full Reference

The Email Body Converter is a **Rossum-maintained hosted webhook** that converts an inbound email's HTML body into a PDF and uploads it to a queue as a new document, so that data living *in the email body* (not in an attachment) gets imported and extracted like any other document. It can also convert HTML and TXT **attachments** into PDFs, and — via a second, undocumented endpoint — HTML/TXT **uploads**.

**Sourcing.** Grounded in the official KB article ([Email Body Converter](https://knowledge-base.rossum.ai/docs/email-body-converter), updated 2025-09-15), the converter service's own OpenAPI spec (`https://elis.rossum.ai/svc/email-converter/api/openapi.json`, title `elis_webhook_email_converter`, v0.1.0), **and two live probe rounds** (org 313278, June 2026: real webhooks on a `*.rossum.app` org, twenty synthetic emails via `POST /v1/emails/import`, two raw HTML uploads, generated PDFs downloaded and visually inspected — covering styling, long bodies, notifications, inbox filters, multi-hook coexistence, cross-queue routing, and cross-region calls). Where docs and live behavior differed, live wins and the discrepancy is noted. Every behavioral claim is tagged:

- **[KB]** — stated verbatim in the KB article (or another official prose page, cited inline).
- **[spec]** — present in the service's OpenAPI spec (key names/types/defaults only; the spec gives no prose semantics).
- **[live]** — verified by the live probe.
- **[field]** — confirmed in production use by the maintaining SA (June 2026), not exercised in the probe.
- **[unverified]** — inference or untested; treat with care.

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
- [live] nuance: HTML/TXT *attachments* are not simply ignored — the platform creates annotation attempts for them which end in **`failed_import`** (observed with the converter deactivated). "Not processed" in practice means "imported but failing", not "invisible".

**What the extension does.** [KB] "A simple extension that can convert the email HTML body into a PDF and upload it to a queue as a new document. An additional feature is that it can also convert HTML attachments into PDFs." Per the config schema it converts `html` and `txt` attachments [KB, live], and the service exposes additional capabilities (header rendering, MIME-based skipping, an upload-conversion endpoint) that the article does not mention [spec, live].

**Use it when** vendors send invoices/orders *as* the email (HTML tables in the body, body-only notifications from ERP systems, forwarded order confirmations) — anything where a body-only email would otherwise import nothing and notify the sender "no processable attachments" (see [Interactions & gotchas](#interactions--gotchas)).

**Don't confuse it with field-level email sources.** Schema datapoints can pull values straight from the email via `rir_field_names` (`email_header:subject|from|to|reply-to|message-id|date`, `email_body:text_html|text_plain`, `email:*` values set by an `email.received` hook) — that mechanism *populates fields on an annotation created from some other file* and is covered in `rossum-reference`. The Email Body Converter instead creates a **new document/annotation from the body itself**. For regex-parsing the body into fields with a custom function, see the KB article [Extracting Data from Email Content with Custom Function](https://knowledge-base.rossum.ai/docs/extracting-data-from-email-content-with-custom-function). The two mechanisms can coexist.

## How it works

The extension is an `email.received` webhook. The platform contract (official API docs, `elis.rossum.ai/api/docs`):

- The hook **payload** carries the email's metadata and content: `files` (attachment metadata), `headers` (same values as `email_header:*` — from, to, reply-to, subject, message-id, date), and `body` with `body_text_plain` and `body_text_html`.
- The hook **response** may return `files` (which incoming files to import, with optional `email:*` values) and `additional_files`. Each `additional_files` entry has `document` (required), `values`, and `import_document` — verbatim: "Set to true if Rossum should import document and create an annotation for it, otherwise it will be just linked as an email attachment. Only applicable if document hasn't already an annotation attached."

**Live-confirmed flow** [live]: the service renders the HTML body to PDF, **creates the document via the API as the hook's token owner** (the produced document's `creator` is the token-owner user), and returns it in `additional_files` with `import_document: true` — the resulting document carries **`attachment_status: "hook_additional_file"`**, in contrast to a normal imported attachment's `"processed"`. The annotation lands in `to_review` and both the document and annotation are linked on the email object (`email.documents`, `email.annotations`, and `annotation.email`).

What you'll see after a body-only email arrives [live]:

| Artifact | Observed value |
|---|---|
| Document `original_file_name` | `Email <unix-epoch>_<16-hex>.pdf` (e.g. `Email 1781265698_fbef484e49f3caea.pdf`) |
| Document `mime_type` / `attachment_status` / `creator` | `application/pdf` / `hook_additional_file` / the token owner |
| Annotation status | `to_review` (normal pipeline from there) |
| PDF appearance | clean A4 portrait render of the HTML body; **no header block by default** |
| Timing | hook run 1–13 s; document/annotation appear ~10–60 s after the email (async) |

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

The table lists where the **service is hosted**, not which orgs can use it. Orgs in regions without a listed URL (EU2 Frankfurt, Japan Tokyo) point their webhook **cross-region** at whichever hosted URL is more convenient — confirmed in production from a Japan Tokyo org using the US URL [field], and exercised live from an EU-side org via the US URL [live]. Mind data residency: the email content is sent to and rendered in the chosen service region.

[live] For multi-tenant `*.rossum.app` organizations, the org's **own domain serves the service path** — `https://<org>.rossum.app/svc/email-converter/api/v1/convert` worked end-to-end in the probe (and `GET …/svc/email-converter/api/healthz` answered 200 on the org domain, EU1, and US hosts alike).

**Creating the same hook via API** — exact payload accepted live [live]:

```json
POST /v1/hooks
{
  "name": "Email body converter",
  "type": "webhook",
  "queues": ["https://<domain>/api/v1/queues/<id>"],
  "events": ["email.received"],
  "active": true,
  "config": {"url": "https://<domain>/svc/email-converter/api/v1/convert"},
  "token_owner": "https://<domain>/api/v1/users/<admin-user-id>",
  "settings": { "configurations": [ { "queue_ids": [<id>] } ] }
}
```

The token owner must have Admin access [KB] and **must be a member of the org** [live]: a support/`organization_group_admin` user from outside the org is rejected with `{"token_owner": ["Invalid hyperlink - Object does not exist."]}` (such a user's `/users/{id}` URL 404s inside the org scope). The service creates the converted document as this user [live].

## Configuration reference

The hook `settings` hold a `configurations` list; [KB] "Each object in the `configurations` list represents a specific configuration (distinguished by the queue IDs)." A single configuration can serve multiple queues via `queue_ids` [KB].

All keys of one `configurations[]` entry (`Configuration` model [spec]):

| Key | Type (default) | Semantics | Tag |
|---|---|---|---|
| `queue_ids` | array[int] (required) | "List of queue IDs this configuration applies to. A single configuration can be used for multiple queues." | [KB] |
| `minimal_email_character_count` | int (`0`) | "Minimum number of characters in the email body to convert it to PDF." Bodies shorter than this are not converted — and the email then counts as having no processable documents (see the notification gotcha). | [KB+live] |
| `skip_if_supported_files_present` | bool (`false`) | "Skip conversion if supported files are present (`true` to skip, `false` to convert the email body to PDF). Supported files include email attachments supported by Rossum and any additional files converted to PDF as part of the webhook call (e.g., HTML attachments converted to PDF)." Live: with a PDF attached, only the attachment imported; body skipped. | [KB+live] |
| `skip_if_mime_types_present` | array[string] | Skip body conversion when any attachment with one of these MIME types is present. Live: `["application/pdf"]` + PDF attachment → body not converted, attachment imported normally. | [spec], semantics [live] |
| `convert_attachments` | array[string] | "List of attachment types to convert to PDF. Supported values: `html`, `txt`." Each listed attachment becomes its own converted-PDF annotation. **Note the duplicate-noise gotcha** in [Interactions & gotchas](#interactions--gotchas). | [KB+live] |
| `html_style` | string\|null | CSS injected as a style tag when rendering the HTML body to PDF. Live: `body { color: …; font-size: … }` clearly applied to the output; font-family substitution appears limited to the service's bundled fonts (a `serif` request still rendered sans). | [spec], semantics [live] |
| `txt_style` | string\|null | "Specifies the style for TXT files, which are first converted to HTML and then to PDF. This configuration is added as an HTML style tag to affect the appearance of the TXT in the converted PDF." Example: `"@page { size: letter landscape; margin: 2cm; } pre { white-space: pre-wrap; }"` | [KB] |
| `create_document_only` | bool (`false`) | Create the converted document and link it to the email **without importing it as an annotation** (the `import_document: false` path — "just linked as an email attachment"). Live: document appeared with `hook_additional_file`, zero annotations. | [spec], semantics [live] |
| `include_header_in_pdf` | bool (`false`) | Render an email-header block above the body in the PDF. Live: **From, To, Subject, Date, Message-ID** rendered (bold labels). | [spec], semantics [live] |
| `header_style` | string\|null | CSS injected into the rendered document when `include_header_in_pdf` is on — **not scoped to the header block**: live, a universal selector (`* { color: … }`) restyled the *entire* PDF including the body, while a `div {…}` selector matched nothing (the header block is not a `div`; its markup is undocumented). Scope selectors carefully. | [spec], effect [live] |
| `skip_if_email_is_a_reply` | bool (`false`) | Presumably: don't convert reply emails. Live test was **inconclusive**: a "Re:"-subject email with `In-Reply-To` imported via `/v1/emails/import` was converted anyway — but the platform never linked it as a reply (`parent: null`, new thread), and the hook's `headers` payload carries no In-Reply-To. The trigger signal is unknown. | [spec], semantics [unverified] |
| `send_email_body_as_plain_document` | bool (`false`) | **Warning** [live]: with this `true`, the probe got **no document at all** — for both a multipart (plain+HTML) and a plain-text-only email; the hook fast-completes (~1 s) without producing anything. Intended semantics unknown; leave `false`. | [spec], behavior [live-observed], semantics [unverified] |

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

With `skip_if_supported_files_present: true`, an email carrying a processable attachment imports only that attachment; a body-only email gets its body converted [KB+live]. The `minimal_email_character_count` floor avoids converting one-line bodies ("see attached… oops, forgot the attachment") into junk documents — though note such an email then imports nothing and triggers the no-attachment notification (see [Interactions & gotchas](#interactions--gotchas)).

For finer control, skip only on specific attachment types: `"skip_if_mime_types_present": ["application/pdf"]` — body converts unless a PDF is attached [live].

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

Converted HTML/TXT attachments count as "supported files" for the skip logic [KB: "Supported files include … any additional files converted to PDF as part of the webhook call"] — so this combination converts attachments *and* skips the body when they exist. Expect the raw `.html`/`.txt` originals to ALSO appear as `failed_import` annotations — that's platform behavior, not the converter (see [Interactions & gotchas](#interactions--gotchas)).

**Render email headers into the PDF** — useful when the sender/subject/date are themselves data to extract [live: From/To/Subject/Date/Message-ID block appears above the body]:

```json
{
  "configurations": [
    {
      "queue_ids": [<id>],
      "include_header_in_pdf": true
    }
  ]
}
```

(`header_style` works but is **not scoped to the header block** — it styles the whole rendered document; see the configuration table.)

## The /convert-files (upload.created) mode

The service exposes a **second endpoint that no KB article mentions** [spec]: `POST /api/v1/convert-files`, shaped for the **`upload.created`** hook event (request model: `action: "created"`, `event: "upload"`). It converts HTML/TXT **uploaded files** (API/UI uploads, not emails) into PDFs. **Live-verified end-to-end** (June 2026).

Hook creation (accepted live):

```json
POST /v1/hooks
{
  "name": "Email body converter files",
  "type": "webhook",
  "queues": ["https://<domain>/api/v1/queues/<id>"],
  "events": ["upload.created"],
  "active": true,
  "config": {"url": "https://<domain>/svc/email-converter/api/v1/convert-files"},
  "token_owner": "https://<domain>/api/v1/users/<admin-user-id>",
  "settings": {"configurations": [{"target_queue_id": <id>, "convert_attachments": ["html", "txt"]}]}
}
```

The settings wrapper is the same `{"configurations": [...]}`; entries follow the `FileConfiguration` model [spec]:

| Key | Type (default) | Notes |
|---|---|---|
| `target_queue_id` | int (required) | Queue the converted document lands in — **routes cross-queue** [live]: an upload into queue A produced the converted annotation in queue B, with no raw-HTML residue left in A. |
| `convert_attachments` | array[string] | As above (`html`, `txt`). [live for html] |
| `include_upload_metadata` | bool (`false`) | Upload `metadata` lands on the converted **annotation's** `metadata` (the document's `metadata` stays empty) [live]. |
| `include_upload_values` | bool (`false`) | `upload:*` values from the original upload carry through to the converted document's import — schema fields bound via `rir_field_names: ["upload:<id>"]` get initialized [live]. |
| `html_style` / `txt_style` | string\|null | As above. |
| `create_document_only` | bool (`false`) | As above ([live] in email mode; untested in upload mode). |

**Behavior differs from the email mode** [live]: the conversion happens **in-flight in the upload pipeline** — an uploaded `invoice.html` produced a *single* `to_review` annotation on a document named `invoice.pdf` (`application/pdf`, `attachment_status: "processed"`), with **no** raw-HTML `failed_import` twin and no `hook_additional_file`. I.e. the hook *replaces* the file before import (the `FileUploadResponseModel`'s `prevent_importing`/`documents` mechanism [spec]) instead of adding an extra document. Original filename stem is preserved, extension swapped to `.pdf`.

## Interactions & gotchas

- **Regional availability.** The converter service is hosted in EU1 Ireland and US east coast only [KB], but that constrains where the *service* runs, not who can call it: orgs in other regions (EU2 Frankfurt, Japan Tokyo) point their webhook cross-region at the more convenient hosted URL — confirmed in production from a Japan Tokyo org using the US URL [field]. The email content is processed in the chosen service region — weigh that against data-residency requirements. `*.rossum.app` orgs: see [Setup](#setup) — the org domain serves the service [live].
- **Raw HTML/TXT attachments import (and fail) regardless of the converter** [live]: the platform creates annotations for `.html`/`.txt` attachments that end in **`failed_import`** — observed with the converter completely disabled. With `convert_attachments` on, each such attachment therefore yields TWO annotations: the raw `failed_import` one (platform) and the converted `to_review` PDF (converter). Don't misread the `failed_import` entries as converter failures, and expect them as noise in the queue. (This also nuances the KB's "HTML documents are not processed" — they are *attempted* and fail.)
- **The no-attachment notification: requires an automated template, and conversion suppresses it** [live]. The rejection email only sends if the queue's `email_with_no_processable_attachments` **email template has `automate: true`** — the queue-settings flag `email_with_no_attachments: true` alone is not sufficient (and `automate` was `false` by default on every queue probed, including an established one). With the template automated: a body-only email **below** `minimal_email_character_count` triggered the "No processable documents" reply (outbound email, parent-linked to the inbound); a body that **converted** triggered nothing — the annotation satisfies the "processable documents" check. (The inbox-level `bounce_email_with_no_attachments` is deprecated in favor of the queue setting.)
- **Multiple `email.received` hooks coexist cleanly with the converter** [live]. The documented merge rule — "annotations are created only for files mentioned in all the responses (their values are merged together with the latest called hooks having the highest priority)" — governs *incoming files only*: live, a filtering hook returning `"files": []` vetoed a PDF attachment (it got `attachment_status: "filtered_by_hook_custom"`, counted in `filtered_out_document_count`, no annotation) while the converter's `additional_files` PDF imported regardless, in the same email. So a filtering hook (e.g. Advanced Email Filtering) cannot accidentally veto the converted body — but it also cannot be used to filter it.
- **Long bodies convert in full; only the stored email object truncates** [live] — a 27 kB HTML body rendered completely (12-page PDF, end marker intact) while the email *object*'s `body_text_html` held only the first ~4 kB ("shortened to 4kB", rossum.app OpenAPI). The truncation limits the API view — and therefore the `email_body:*` schema-field sources — not the conversion, which receives the full body in the hook payload.
- **Inbox rejection filters do not apply to the generated PDF** [live] — with the inbox's `document_rejection_conditions` set to reject all `application/pdf` and anything below 20000×20000 px, the converter's PDF still imported. The filters act on *incoming attachments* before hooks run; hook-injected `additional_files` bypass them. (Sender allow/deny lists act on the email itself and still gate everything [unverified for this combination].)
- **`POST /v1/emails/import` quirks** [live]: it rejects messages without a `Date` header / with bare-LF line endings as HTTP 400 "Invalid e-mail format", and it does **not** thread imported emails by `In-Reply-To` (`parent` stays null) — relevant for anyone testing reply handling via this endpoint, and the reason `skip_if_email_is_a_reply`'s semantics remain unverified. The 202 response's task URL is **not retrievable** — `GET /tasks/{id}` 404s for this task type (confirmed live, June 2026), despite the API reference suggesting otherwise; poll the **email object** (e.g. `GET /emails?to=<inbox>&type=incoming&ordering=-created_at`) to confirm the import, not the task. The **`rossum_import_email` MCP tool** wraps this endpoint and is the easiest way to simulate an inbound email for testing the converter — when you let it build the message from `subject`/`body_html`/`attachments` it emits CRLF + a `Date` header for you, sidestepping the 400; pass `raw_message_path` only when you need a hand-crafted `.eml` (then ensure CRLF + Date yourself).
- **This extension vs. `email_body:*` fields.** `rir_field_names: ["email_body:text_html"]` copies raw body text into a *field* of an annotation created from some other file; the converter creates a *new document* from the body. They solve different problems and can coexist.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Body-only email imports nothing | Wrong region URL in `config.url`; queue not in `queue_ids`; body shorter than `minimal_email_character_count`; hook inactive; or you didn't wait — the converted document appears **asynchronously, ~10–60 s after the email** [live] | Hook logs (status + runtime: a run that converted something takes seconds, a no-op completes in ~1 s [live]); `queue_ids` vs the inbox's queue id; region table in [Setup](#setup) |
| Hook creation fails: `token_owner: ["Invalid hyperlink - Object does not exist."]` | Token owner user is not a member of the org (e.g. a support/organization_group_admin account) [live] | Pick an in-org user with Admin access |
| Body converted even though a PDF was attached | `skip_if_supported_files_present` left `false` (default) | Set it `true` [KB+live], or use `skip_if_mime_types_present` [live] |
| Extra `failed_import` annotations for `.html`/`.txt` attachments | Platform-default raw import attempt — **not** a converter failure [live] | Expected noise; the converter's `to_review` PDFs are the processable copies |
| Reply emails ("Re: …") get converted into junk documents | `skip_if_email_is_a_reply` left `false` — but note its trigger signal is unverified and it did NOT fire for an unthreaded "Re:" email in the live test | Test in your flow before relying on it |
| Nothing imports with `send_email_body_as_plain_document: true` | Live-observed behavior of this key — it suppressed all output in tests | Leave it `false` |
| `POST /v1/emails/import` returns 400 "Invalid e-mail format" | Missing `Date` header / bare-LF line endings in the .eml [live] | Add `Date`, use CRLF — or use the `rossum_import_email` MCP tool's constructed-message mode, which does both for you |
| Sender gets "no processable attachments" rejection | Body wasn't converted (too short / skipped / hook failed) — a successful conversion suppresses the notification [live] | `minimal_email_character_count`, skip flags, hook logs |
| Rejection notification never sends even for genuinely empty emails | The queue's `email_with_no_processable_attachments` template has `automate: false` — the default, even with the queue notification setting on [live] | `PATCH /v1/email_templates/{id}` with `{"automate": true}` |
| Org is in a region with no listed URL (EU2/Japan) and nothing imports | `config.url` points at a non-existent same-region path | Use a hosted region's URL — cross-region use works [field]; see [Setup](#setup) |
