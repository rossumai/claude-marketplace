# export-create-upload-submit

Use this part when an external API requires a three-step sequence: first create a resource (returning an id), then upload the source document against that id, and finally submit the resource for approval or processing. Each step is gated on the prior step succeeding, so a failed create stops the pipeline before any upload is attempted.

## Params

- `create_url` — the resource-creation endpoint (e.g. `https://api.example.com/invoices`)
- `upload_url` — the document-upload endpoint; typically templated with the id returned by the create call (e.g. `https://api.example.com/invoices/{field.created_id.value}/scan`)
- `submit_url` — the submit or approval endpoint, similarly templated (e.g. `https://api.example.com/invoices/{field.created_id.value}/submit`)
- `auth_ref` — OAuth token endpoint URL used by all three stages; the same cached token is shared across the cascade
- `id_response_key` — JMESPath into the create response body that extracts the new resource id (e.g. `id`, `data.invoice_id`)

## Produces / Consumes

- Produces: `api_status_code` (HTTP status from the create call) and `created_id` (the extracted resource id), both written to schema fields so downstream stages can gate and template against them.
- Consumes: no schema fields directly; the upload and submit URLs should reference `{field.created_id.value}` from the create stage.

## Adapt

The `evaluate` block on stages 2 and 3 checks that `api_status_code` is `200` or `201`; extend the `$in` list if your API returns `202`. If the upload step uses a different HTTP method (e.g. `POST` instead of `PUT`), update `request.method` in that stage. To add request body fields to the create call, extend `request.content` in stage 1. The auth object is duplicated across all three stages so the engine can cache and reuse the same token without a separate auth stage.

See `export-pipeline-reference` for the Request Processor stage model.
