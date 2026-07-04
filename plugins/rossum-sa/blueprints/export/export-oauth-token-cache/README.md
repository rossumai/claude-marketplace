# export-oauth-token-cache

Use this blueprint when an external API requires an OAuth 2.0 client_credentials bearer token and you want the token automatically cached across hook executions and refreshed whenever the API returns a 401. Rather than re-fetching a token on every invocation, the Request Processor stores the token in hook secrets and reuses it until it expires or is rejected.

## Params

- `oauth_url` — the token endpoint URL (e.g. `https://api.example.com/oauth/token`)
- `client_id` — the OAuth client identifier issued by the API provider
- `client_secret` — the **name** of the hook secret that holds the client secret value; never inline the secret value here
- `scope` — the OAuth grant scope string, if the provider requires one; defaults to empty string (omitted from the request when blank)
- `token_path` — JMESPath expression pointing to the token in the auth response; defaults to `access_token`, but use e.g. `data.token` for non-standard responses

## Produces / Consumes

- Produces: nothing in the schema — the token is available only as `{token}` within the same stage's `request.headers`.
- Consumes: no schema fields directly; reads `payload.secrets.«client_secret»` from hook secrets at runtime.

## Adapt

The `auth` block is reusable across multiple `call_api` entries. Copy the same `auth` object into each stage that needs the token — the engine will serve the cached token to all of them without re-fetching. To add a scope, set `scope` to the exact string the provider expects (e.g. `"openid api"`) — it is passed as the `scope` form field. If the provider returns the token under a nested key (e.g. `"result.access_token"`), set `token_path` to that dotted path.

The fragment's `request.url` is set to `{field.api_endpoint.value}` as a placeholder — replace it with your real target endpoint URL, or drop the `request` block entirely and copy only the `auth` block into your actual call stage.

Declare the hook's `secrets_schema` with the **open string-map shape**: the `«client_secret»` key under `properties` (so the Secrets editor prefills it as `__change_me__`) plus `additionalProperties: {"type": "string"}` — the engine **writes the cached token back into hook secrets at runtime**, and a closed `additionalProperties: false` schema would reject that write on the first refresh. Snippet in `export-pipeline-reference#authentication`.

See `export-pipeline-reference` for the Request Processor stage model.
