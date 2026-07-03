# export-related-document-attachment

Use this Request Processor stage when, after creating a resource in the target system, every document related to the annotation (email attachments, supporting documents, rendered payloads) must be uploaded to it via a REST multipart endpoint. The `get_content` phase selects document relations whose `key` matches a regex and fetches their document metadata + content; the `call_api` phase then iterates the result, POSTing one multipart upload per relation.

This is the **REST counterpart** of `export-workday-soap-attachment-data` — SOAP-mapping targets embed attachments base64 inside the submission payload instead of uploading them separately.

## Params

- `relation_pattern` — regex over relation keys (e.g. `^attachment_email_attachments_\d{8,10}(?:_\d+)?$`)
- `auth_ref` — OAuth client_credentials token endpoint; keep it byte-identical with the export's other stages so the cached token is reused
- `upload_url` — the attachment endpoint, usually templated with the created resource id (e.g. `{field.api_base_url.value}/invoices/{field.created_id.value}/attachments`)
- `file_field` — the multipart form-field name for the file part (e.g. `attachment[file]`, `file`)
- `status_field` — schema field receiving the upload HTTP status (per iteration; multiple writes auto-collect into a list)

## Produces / Consumes

- Produces: the `status_field` (must **pre-exist** in the schema with `ui_configuration.type: "data"` — a missing target fails the whole export with the generic exception blocker).
- Consumes: no schema fields. It consumes **document relations**, which something upstream must have created; generating them (email-attachment relations, `document_relation` response handlers from earlier stages) is out of this blueprint's scope.

## Gotchas

- **Only the first document per relation is fetched** — one upload per relation, by engine design.
- **Attachments >50 MB fail through Rossum infra** — there is no streaming path; filter oversize relations upstream rather than letting one file fail the stage.
- `get_content` yields a **single object, not a 1-item list, when exactly one relation matches**; verify `iterate_over` behavior for that shape on your engine version.
- Add an `evaluate` guard on the prior stage's success (see `export-evaluate-guard`) so attachments are not uploaded against a failed create.

See `export-pipeline-reference` (Get Content Phase, Iteration Over Document Relations) for the grammar.
