# export-workday-soap-attachment-data

Use this block as the `Attachment_Data` of a Workday `Submit_Supplier_Invoice` mapping (see `export-workday-soap-invoice-mapping`) when the submission must carry **more documents than the scanned invoice** — e-invoice source files, email siblings, delivery notes, DMS confirmations. It iterates a multivalue table with one row per document; for each row the connector fetches the Rossum document content URL stored in the row (`$FETCH_DOCUMENT_CONTENT$`) and embeds it base64 into the SOAP payload.

## Attachment sizing

The connector imposes **no size cap of its own**; the practical ceilings are the Workday tenant's attachment limits and total processing time. Base64 encoding inflates content by ~33 % (a 30 MB PDF travels as ~40 MB of payload), and a very slow submit can time out platform-side *after* the invoice was created in Workday — `Add_Only` plus Workday's duplicate checks are the guard against timeout-then-retry double submission. Test the real document-size profile before promising large-attachment support, and filter oversize files in the collector hook so one attachment never stalls the whole submission.

## Params

- `attachment_table` — multivalue tuple, one row per document to attach
- `name_field` / `mime_field` — row columns with filename and MIME type
- `content_url_field` — row column holding the Rossum content URL (`…/documents/{id}/content`)

## Produces / Consumes

- Produces: nothing.
- Consumes: the attachment table and its row columns — populated upstream by a **collector hook**, e.g. one of the observed feeders:
  - email siblings found via `annotations/search` on a shared email-id field and uploaded/linked as documents;
  - attachments carried inside a structured e-invoice (SFI) converted to Rossum documents — pair with an `upload.created` hook that sets `prevent_importing` on files flagged as attachments so they skip extraction;
  - an archive/DMS hook writing its response rows (name, MIME, content URL) into the same table.

## Adapt

- Single-document case: skip this blueprint — `export-workday-soap-invoice-mapping` already embeds the source document inline as `Attachment_Data: [{"Encoding": "base64", "Filename": "@{file_name}", "Content_Type": "application/pdf", "File_Content": "{document_content}"}]`.
- A free-text row column (e.g. an archive response) can ride along as `Comment` per attachment.
- For REST-style targets, the same related-documents problem is solved by `export-related-document-attachment` (Request Processor, multipart upload) instead of payload embedding.

See `workday-reference` (§ Attachments) for the documented attachment contract and `export-workday-soap-invoice-mapping` for hook wiring.
