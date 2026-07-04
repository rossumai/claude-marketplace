# export-sftp-via-file-storage

Use this blueprint when you need to push an exported file to an SFTP server but want to avoid embedding SFTP credentials in the hook code or managing SSH keys directly. The Request Processor delegates to Rossum's `file-storage-export` service, which handles the SFTP connection, credential rotation, and file transfer — the hook only passes configuration and the base64-encoded payload.

## Params

- `host` — SFTP server hostname or IP address (e.g. `sftp.partner.example.com`)
- `port` — SFTP port number; defaults to `22`
- `username` — SFTP login username
- `auth_secret` — the **name** of the hook secret containing the password or SSH private key; the value is read at runtime from `payload.secrets.<auth_secret>`
- `directory` — absolute remote path on the SFTP server where the file should be written (e.g. `/upload/invoices`)
- `filename_template` — output filename pattern without extension (e.g. `invoice_{field.document_id.value}`); the file-storage-export service appends the document extension automatically

## Produces / Consumes

- Produces: `sftp_status_code` written to a schema field, reflecting the HTTP status returned by the file-storage-export service (not the SFTP transfer status itself).
- Consumes: no schema fields directly; reads `payload.base_url`, `payload.rossum_authorization_token`, and the hook secret referenced by `auth_secret`.

## Adapt

The static pass-through fields (`request_id`, `timestamp`, `hook`, `base_url`, `action`, `event`) must be forwarded exactly as shown — the file-storage-export service validates them. If you need SSH key authentication instead of password, replace the `password` key in `secrets` with `ssh_key` pointing to the appropriate hook secret. To prevent overwriting existing files, set `filename_collision.replace` to `false`.

Declare a **closed** `secrets_schema` (`additionalProperties: false`) with the `«auth_secret»` key under `properties` (`minLength: 1` + a description) — nothing writes secrets back at runtime here, and the Secrets editor prefills the declared key as `__change_me__` so whoever pastes the SFTP password or SSH key sees exactly what to provide.

See `export-pipeline-reference` for the Request Processor stage model.
