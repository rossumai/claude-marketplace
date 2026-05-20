#!/usr/bin/env python3
"""MCP server for Rossum APIs."""

import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = ssl.create_default_context()

_cached_base_url = None
_cached_token = None
_token_validated = False
_client_capabilities = {}
_server_request_counter = 0


# --- MCP protocol ---


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def read_message():
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError) as e:
        _log(f"Failed to parse message: {e}")
        return None


def write_message(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def respond(request_id, result):
    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def respond_error(request_id, code, message):
    write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def tool_result(request_id, text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    respond(request_id, result)


def _next_server_id():
    global _server_request_counter
    _server_request_counter += 1
    return f"s-{_server_request_counter}"


def _elicit(message, schema):
    """Request user input via MCP elicitation. Returns content dict or None."""
    if "elicitation" not in _client_capabilities:
        return None
    req_id = _next_server_id()
    write_message({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "elicitation/create",
        "params": {
            "message": message,
            "requestedSchema": schema,
        },
    })
    while True:
        resp = read_message()
        if resp is None:
            return None
        if resp.get("id") == req_id:
            result = resp.get("result", {})
            if result.get("action") == "accept":
                return result.get("content", {})
            return None


# --- URL validation ---


_BEARER_RE = re.compile(r"[Bb]earer\s+([^\s\"']+)")
_HTTPS_URL_RE = re.compile(r"https://[^\s\"']+")


def _parse_connection_string(text):
    """Extract (token, base_url) from a curl-style snippet.

    Returns (token, base_url) — either may be None if not found.
    The base URL is returned as-is for the caller to validate/normalize.
    """
    if not text:
        return (None, None)
    token_match = _BEARER_RE.search(text)
    url_match = _HTTPS_URL_RE.search(text)
    token = token_match.group(1) if token_match else None
    base_url = url_match.group(0) if url_match else None
    return (token, base_url)


def _validate_base_url(url):
    """Validate and normalize a base URL. Returns origin or None.

    Normalizes common Rossum URL variants so callers don't need to know the
    canonical hostname.  For example ``us.api.rossum.ai`` is rewritten to
    ``us.app.rossum.ai`` because the ``*.api.*`` subdomain does not expose
    the Data Storage service (``/svc/data-storage/...``).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme != "https":
        return None
    if not parsed.hostname:
        return None
    hostname = parsed.hostname
    # *.api.rossum.ai → *.app.rossum.ai (data-storage lives on *.app.*)
    if hostname.endswith(".api.rossum.ai"):
        hostname = hostname.replace(".api.rossum.ai", ".app.rossum.ai")
    origin = f"https://{hostname}"
    if parsed.port and parsed.port != 443:
        origin += f":{parsed.port}"
    return origin


# --- Connection state ---


def _check_health(base_url):
    """Check if the Data Storage API is reachable (no auth required)."""
    req = urllib.request.Request(
        f"{base_url}/svc/data-storage/api/healthz",
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context) as resp:
            return resp.status == 200
    except Exception:
        return False


def _probe_token(base_url, token):
    """Validate a token with a lightweight API call. Returns (ok, error_detail)."""
    req = urllib.request.Request(
        f"{base_url}/api/v1/auth/user",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context) as resp:
            return (True, None)
    except urllib.error.HTTPError as e:
        return (False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
    except ssl.SSLError as e:
        return (False, f"SSL error: {e}. Try: python3 -m pip install certifi")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def _login_with_password(base_url, username, password):
    """Exchange username+password for an API token via /v1/auth/login. Returns (token, error)."""
    req = urllib.request.Request(
        f"{base_url}/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("key")
            if not token:
                return (None, "Login succeeded but no token returned.")
            return (token, None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        if e.code == 401:
            return (None, "Invalid username or password.")
        return (None, f"HTTP {e.code}: {body}")
    except ssl.SSLError as e:
        return (None, f"SSL error: {e}. Try: python3 -m pip install certifi")
    except Exception as e:
        return (None, f"{type(e).__name__}: {e}")


def _invalidate_connection():
    """Clear cached connection state."""
    global _cached_base_url, _cached_token, _token_validated
    _cached_base_url = None
    _cached_token = None
    _token_validated = False


def _ensure_connection(request_id):
    """Guard: return cached (base_url, token) or send an error directing to rossum_set_token."""
    if _token_validated and _cached_base_url and _cached_token:
        return (_cached_base_url, _cached_token)

    tool_result(
        request_id,
        "Not connected to Rossum. Call rossum_set_token to establish a connection.",
        is_error=True,
    )
    return (None, None)


# --- HTTP helpers ---


def _http_request(request_id, url, *, method="GET", body=None, parse_json=True):
    """Make an authenticated HTTP request. Returns parsed JSON or None (error sent).

    When *parse_json* is False, returns the HTTP status code (int) instead of
    parsed JSON — useful for DELETE (204 No Content) and other empty responses.
    Callers must call _ensure_connection first for correct URL construction.
    """
    token = _cached_token
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=130, context=_ssl_context) as resp:
            if not parse_json:
                return resp.status
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        if e.code == 401:
            _invalidate_connection()
            tool_result(
                request_id,
                f"Authentication failed (HTTP 401). Token may be expired. "
                f"Call rossum_set_token to re-authenticate.\n{error_body}",
                is_error=True,
            )
            return None
        tool_result(request_id, f"HTTP {e.code}: {error_body}", is_error=True)
        return None
    except Exception as e:
        tool_result(request_id, f"Error: {e}", is_error=True)
        return None


def _data_storage_call(request_id, path, body):
    """POST to a Data Storage API endpoint."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    url = f"{base_url}/svc/data-storage/api{path}"
    result = _http_request(request_id, url, method="POST", body=body)
    if result is not None:
        tool_result(request_id, json.dumps(result, indent=2))


def _rossum_get(request_id, path):
    """GET a single Rossum API resource and return it as JSON."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    result = _http_request(request_id, f"{base_url}{path}")
    if result is not None:
        tool_result(request_id, json.dumps(result, indent=2))


def _rossum_post(request_id, path, body):
    """POST to a Rossum API endpoint and return the result as JSON."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    result = _http_request(request_id, f"{base_url}{path}", method="POST", body=body)
    if result is not None:
        tool_result(request_id, json.dumps(result, indent=2))


def _rossum_delete(request_id, path):
    """DELETE a Rossum API resource. Expects 204 No Content."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    status = _http_request(request_id, f"{base_url}{path}", method="DELETE", parse_json=False)
    if status is not None:
        tool_result(request_id, f"Deleted successfully (HTTP {status}).")


def _rossum_patch(request_id, path, body):
    """PATCH a Rossum API resource and return the result as JSON."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    result = _http_request(request_id, f"{base_url}{path}", method="PATCH", body=body)
    if result is not None:
        tool_result(request_id, json.dumps(result, indent=2))


def _url_to_id(value):
    """Extract the trailing integer ID from a Rossum API URL.

    'https://elis.rossum.ai/api/v1/hooks/12345' → 12345
    Returns the original value unchanged if it is not a parseable URL.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    try:
        return int(value.rsplit("/", 1)[-1])
    except (ValueError, IndexError):
        return value


def _compact_item(item, url_fields):
    """Convert URL reference fields to bare integer IDs in *item* (in-place).

    *url_fields* is a set of field names whose values are either a single API URL
    string or a list of API URL strings.
    """
    for key in url_fields:
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            item[key] = [_url_to_id(v) for v in val]
        else:
            item[key] = _url_to_id(val)
    return item


# Fields whose values are Rossum API URLs (single or list) and should be
# compacted to bare integer IDs in list responses to save tokens.
_URL_REF_FIELDS = frozenset({
    "queue", "workspace", "schema", "hooks", "queues", "run_after",
    "token_owner", "organization", "document", "modifier", "inbox",
    "parent", "children", "email_thread", "root_email", "documents",
    "annotations",
})


def _paginate(request_id, url, *, max_results=None, pick_fields=None):
    """Auto-paginate a Rossum list endpoint. Returns (results, api_total) or None on error."""
    all_results = []
    api_total = None
    while url:
        page = _http_request(request_id, url)
        if page is None:
            return None
        if api_total is None:
            api_total = page.get("pagination", {}).get("total")
        for item in page.get("results", []):
            if max_results and len(all_results) >= max_results:
                break
            row = {k: item[k] for k in pick_fields if k in item} if pick_fields else dict(item)
            _compact_item(row, _URL_REF_FIELDS)
            all_results.append(row)
        if max_results and len(all_results) >= max_results:
            break
        next_url = page.get("pagination", {}).get("next")
        if not next_url:
            break
        if _validate_base_url(next_url) != _validate_base_url(url):
            break
        url = next_url
    return (all_results, api_total)


def _rossum_list(request_id, endpoint, params, *, pick_fields=None, max_results=None):
    """Paginate a Rossum API list endpoint and return collected results."""
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    result = _paginate(
        request_id, f"{base_url}{endpoint}?{urlencode(params)}",
        max_results=max_results, pick_fields=pick_fields,
    )
    if result is not None:
        results, _ = result
        tool_result(request_id, json.dumps({"total": len(results), "results": results}, indent=2))


# --- Tool registration ---


TOOLS = {}
HANDLERS = {}


_READ_ONLY = {"readOnlyHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}


def _tool(name, description, schema, annotations=None):
    """Decorator: register a tool definition and its handler together."""
    def decorator(handler):
        tool_def = {"name": name, "description": description, "inputSchema": schema}
        if annotations:
            tool_def["annotations"] = annotations
        TOOLS[name] = tool_def
        HANDLERS[name] = handler
        return handler
    return decorator


# --- Field filters for list endpoints ---


_USER_FIELDS = ("id", "email", "first_name", "last_name", "is_active")
_HOOK_LOG_FIELDS = (
    "hook_id", "annotation_id", "queue_id", "event", "action",
    "status", "log_level", "message", "timestamp", "start", "end",
)
_ANNOTATION_FIELDS = ("id", "queue", "status", "document", "modifier", "modified_at", "confirmed_at", "exported_at")
_QUEUE_FIELDS = ("id", "name", "workspace", "schema", "hooks", "status", "dedicated_engine")
_HOOK_FIELDS = ("id", "name", "type", "events", "queues", "active", "run_after", "token_owner")
_SCHEMA_FIELDS = ("id", "name", "queues")
_WORKSPACE_FIELDS = ("id", "name", "organization", "queues", "autopilot")
_CONNECTOR_FIELDS = ("id", "name", "queues", "service_url", "authorization_type", "asynchronous")
_EMAIL_FIELDS = (
    "id", "queue", "inbox", "subject", "from", "to", "cc", "bcc",
    "type", "created_at", "documents", "annotations", "parent", "children",
    "email_thread", "annotation_counts", "labels", "metadata",
)
_EMAIL_THREAD_FIELDS = (
    "id", "queue", "root_email", "subject", "from", "has_replies",
    "has_new_replies", "created_at", "last_email_created_at",
    "annotation_counts", "labels",
)


# --- Tools ---


@_tool(
    "rossum_set_token",
    "Set the Rossum API connection for this session. Supports three authentication methods:\n"
    "1. **API token** (admins): Pass or be prompted for a Bearer token.\n"
    "2. **Username + password** (implementation partners): Pass username and password to "
    "obtain a session token via Rossum's login API.\n"
    "3. **Connection string** (admins): Paste a curl snippet from the Rossum admin UI; "
    "the token and base URL are extracted automatically.\n\n"
    "Prefer calling without arguments — the user will be prompted interactively "
    "for credentials, keeping secrets out of the conversation. "
    "Only pass arguments if the user has already typed them in the chat.",
    {
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "description": "Rossum API Bearer token. Use this OR username+password, not both.",
            },
            "username": {
                "type": "string",
                "description": "Rossum account email/username. Used with 'password' to obtain a token via login API.",
            },
            "password": {
                "type": "string",
                "description": "Rossum account password. Used with 'username' to obtain a token via login API.",
            },
            "baseUrl": {
                "type": "string",
                "description": (
                    "Base URL of the Rossum environment "
                    "(e.g. https://elis.rossum.ai). Omit to prompt interactively."
                ),
            },
            "connectionString": {
                "type": "string",
                "description": (
                    "A curl snippet containing 'Authorization: Bearer <token>' and an https URL "
                    "(as shown in the Rossum admin UI). When provided, token and baseUrl are "
                    "parsed from it and any explicit token/baseUrl arguments are ignored."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_set_token(request_id, arguments):
    global _cached_base_url, _cached_token, _token_validated

    token = arguments.get("token", "")
    username = arguments.get("username", "")
    password = arguments.get("password", "")
    raw_url = arguments.get("baseUrl", "")
    connection_string = arguments.get("connectionString", "")

    # Connection string overrides token/baseUrl when provided.
    if connection_string:
        parsed_token, parsed_url = _parse_connection_string(connection_string)
        if not parsed_token or not parsed_url:
            return tool_result(
                request_id,
                "Could not parse connection string. Expected a snippet with "
                "'Authorization: Bearer <token>' and an https://... URL.",
                is_error=True,
            )
        token = parsed_token
        raw_url = parsed_url
        username = ""
        password = ""

    # If we have both token and username+password, prefer token
    if token and username:
        username = ""
        password = ""

    # Interactive prompt when no credentials provided
    if not token and not username and not raw_url:
        content = _elicit(
            "Enter your Rossum API credentials.\n\n"
            "**Option A — Connection string** (admins, fastest): paste a curl snippet from "
            "the Rossum admin UI into Connection String — token and base URL are extracted "
            "automatically; leave the other fields empty.\n\n"
            "**Option B — API token**: fill in API Token + Base URL, leave the rest empty.\n\n"
            "**Option C — Username & password** (implementation partners): "
            "fill in Username, Password, and Base URL.",
            {
                "type": "object",
                "properties": {
                    "connectionString": {
                        "type": "string",
                        "title": "Connection String",
                        "description": (
                            "Paste a curl snippet containing 'Authorization: Bearer <token>' "
                            "and an https URL. Overrides the other fields when provided."
                        ),
                    },
                    "token": {
                        "type": "string",
                        "title": "API Token",
                        "description": "Rossum API Bearer token (leave empty to use username+password or connection string)",
                    },
                    "username": {
                        "type": "string",
                        "title": "Username",
                        "description": "Rossum account email (leave empty if using API token or connection string)",
                    },
                    "password": {
                        "type": "string",
                        "title": "Password",
                        "description": "Rossum account password (leave empty if using API token or connection string)",
                    },
                    "baseUrl": {
                        "type": "string",
                        "title": "Base URL",
                        "description": "e.g. https://elis.rossum.ai (ignored when connection string is set)",
                        "default": "https://elis.rossum.ai",
                    },
                },
            },
        )
        if content is None:
            return tool_result(
                request_id,
                "Credential prompt was cancelled or not supported by this client. "
                "Call rossum_set_token with explicit arguments instead:\n"
                "  - With token: rossum_set_token(token='...', baseUrl='...')\n"
                "  - With login: rossum_set_token(username='...', password='...', baseUrl='...')\n"
                "  - With connection string: rossum_set_token(connectionString='curl -H \"Authorization: Bearer ...\" https://...')",
                is_error=True,
            )
        cs = content.get("connectionString", "")
        if cs:
            parsed_token, parsed_url = _parse_connection_string(cs)
            if not parsed_token or not parsed_url:
                return tool_result(
                    request_id,
                    "Could not parse connection string. Expected a snippet with "
                    "'Authorization: Bearer <token>' and an https://... URL.",
                    is_error=True,
                )
            token = parsed_token
            raw_url = parsed_url
        else:
            token = content.get("token", token)
            username = content.get("username", username)
            password = content.get("password", password)
            raw_url = content.get("baseUrl", raw_url)

    # Interactive prompt when we have credentials but no URL
    if (token or username) and not raw_url:
        content = _elicit(
            "Enter the Rossum environment URL.",
            {
                "type": "object",
                "properties": {
                    "baseUrl": {
                        "type": "string",
                        "title": "Base URL",
                        "description": "e.g. https://elis.rossum.ai",
                        "default": "https://elis.rossum.ai",
                    },
                },
                "required": ["baseUrl"],
            },
        )
        if content is None:
            return tool_result(
                request_id,
                "URL prompt was cancelled. Pass baseUrl explicitly.",
                is_error=True,
            )
        raw_url = content.get("baseUrl", raw_url)

    if not token and not username:
        return tool_result(
            request_id,
            "No credentials provided. Supply either a token OR username+password.",
            is_error=True,
        )

    base_url = _validate_base_url(raw_url)
    if not base_url:
        return tool_result(request_id, f"Invalid base URL: {raw_url}. Must be HTTPS.", is_error=True)

    # Username+password login flow
    if not token and username:
        if not password:
            return tool_result(request_id, "Password is required when using username login.", is_error=True)
        token, err = _login_with_password(base_url, username, password)
        if err:
            _invalidate_connection()
            return tool_result(
                request_id,
                f"Login failed for {username} at {base_url}: {err}",
                is_error=True,
            )

    ok, detail = _probe_token(base_url, token)
    if not ok:
        _invalidate_connection()
        return tool_result(
            request_id,
            f"Cannot connect to {base_url}: {detail}. "
            f"If this is not an auth error, the token may still be valid — check the error above.",
            is_error=True,
        )

    _cached_base_url = base_url
    _cached_token = token
    _token_validated = True
    method = "username+password login" if username else "API token"
    return tool_result(request_id, f"Connected to {base_url} via {method}. Token validated for this session.")


@_tool(
    "rossum_whoami",
    "Returns the authenticated user's identity, organization, and role. "
    "Useful for checking permissions and orientation after connecting.",
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_whoami(request_id, arguments):
    _rossum_get(request_id, "/api/v1/auth/user")


@_tool(
    "data_storage_healthz",
    "Checks if the Rossum Data Storage API is reachable. Does not require authentication. "
    "Uses the connected environment if available, otherwise checks the default (elis.rossum.ai).",
    {
        "type": "object",
        "properties": {
            "baseUrl": {
                "type": "string",
                "description": "Base URL to check. Defaults to the connected environment or elis.rossum.ai.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_healthz(request_id, arguments):
    raw_url = arguments.get("baseUrl", "")
    if raw_url:
        validated = _validate_base_url(raw_url)
        if not validated:
            return tool_result(request_id, f"Invalid base URL: {raw_url}. Must be an HTTPS URL.", is_error=True)
        source = "provided"
    elif _cached_base_url:
        validated = _cached_base_url
        source = "connected environment"
    else:
        validated = "https://elis.rossum.ai"
        source = "default (no connection established)"

    if _check_health(validated):
        return tool_result(request_id, f"Data Storage API at {validated} is healthy ({source}).")

    return tool_result(request_id, f"Data Storage API at {validated} is not reachable ({source}).", is_error=True)


@_tool(
    "data_storage_list_collections",
    "Lists available collections in Rossum Data Storage.",
    {
        "type": "object",
        "properties": {
            "filter": {"type": "object", "description": "Optional query filter for collections."},
            "nameOnly": {"type": "boolean", "description": "Return only collection names (default: true)."},
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_collections(request_id, arguments):
    body = {}
    if "filter" in arguments:
        body["filter"] = arguments["filter"]
    if "nameOnly" in arguments:
        body["nameOnly"] = arguments["nameOnly"]
    return _data_storage_call(request_id, "/v1/collections/list", body)


@_tool(
    "data_storage_aggregate",
    "Performs a MongoDB aggregation pipeline on a Rossum Data Storage collection. "
    "Runtime is limited to 120 seconds. Always include a $limit stage to avoid unbounded results.",
    {
        "type": "object",
        "required": ["collectionName", "pipeline"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection to aggregate on."},
            "pipeline": {
                "type": "array",
                "items": {"type": "object"},
                "description": "The MongoDB aggregation pipeline stages.",
            },
            "collation": {"type": "object", "description": "Collation settings for the aggregation."},
            "let": {"type": "object", "description": "Variables accessible in the pipeline."},
            "options": {"type": "object", "description": "Additional aggregation options."},
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_aggregate(request_id, arguments):
    body = {"pipeline": arguments.get("pipeline", []), "collectionName": arguments["collectionName"]}
    for key in ("collation", "let", "options"):
        if key in arguments:
            body[key] = arguments[key]
    return _data_storage_call(request_id, "/v1/data/aggregate", body)


_INDEX_LIST_SCHEMA = {
    "type": "object",
    "required": ["collectionName"],
    "properties": {
        "collectionName": {"type": "string", "description": "The name of the collection."},
        "nameOnly": {"type": "boolean", "description": "Return only index names (default: true)."},
    },
    "additionalProperties": False,
}


def _handle_index_list(request_id, arguments, path):
    body = {"collectionName": arguments.get("collectionName", "")}
    if "nameOnly" in arguments:
        body["nameOnly"] = arguments["nameOnly"]
    return _data_storage_call(request_id, path, body)


@_tool("data_storage_list_indexes", "Lists all indexes of a Rossum Data Storage collection.", _INDEX_LIST_SCHEMA, annotations=_READ_ONLY)
def handle_list_indexes(request_id, arguments):
    return _handle_index_list(request_id, arguments, "/v1/indexes/list")


@_tool(
    "data_storage_list_search_indexes",
    "Lists all Atlas Search indexes of a Rossum Data Storage collection.",
    _INDEX_LIST_SCHEMA,
    annotations=_READ_ONLY,
)
def handle_list_search_indexes(request_id, arguments):
    return _handle_index_list(request_id, arguments, "/v1/search_indexes/list")


@_tool(
    "data_storage_create_index",
    "Creates a database index on a Rossum Data Storage collection. "
    "This is a write operation that modifies the collection's index configuration.",
    {
        "type": "object",
        "required": ["collectionName", "indexName", "keys"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "indexName": {"type": "string", "description": "Name for the index."},
            "keys": {
                "type": "object",
                "description": (
                    "Index key specification. Keys are field paths, values are "
                    "1 (ascending), -1 (descending), or 'text'."
                ),
            },
            "options": {
                "type": "object",
                "description": "Index options (e.g. unique, sparse, expireAfterSeconds).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_create_index(request_id, arguments):
    body = {
        "collectionName": arguments["collectionName"],
        "indexName": arguments["indexName"],
        "keys": arguments["keys"],
    }
    if "options" in arguments:
        body["options"] = arguments["options"]
    return _data_storage_call(request_id, "/v1/indexes/create", body)


@_tool(
    "data_storage_create_search_index",
    "Creates an Atlas Search index on a Rossum Data Storage collection. "
    "This is a write operation that modifies the collection's search index configuration.",
    {
        "type": "object",
        "required": ["collectionName", "mappings"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "mappings": {
                "type": "object",
                "description": "Atlas Search index mappings (e.g. {\"dynamic\": true}).",
            },
            "indexName": {
                "type": "string",
                "description": "Name for the search index. Defaults to 'default' if not specified.",
            },
            "analyzers": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Custom analyzer definitions for the search index.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_create_search_index(request_id, arguments):
    body = {"collectionName": arguments["collectionName"], "mappings": arguments["mappings"]}
    if "indexName" in arguments:
        body["indexName"] = arguments["indexName"]
    if "analyzers" in arguments:
        body["analyzers"] = arguments["analyzers"]
    return _data_storage_call(request_id, "/v1/search_indexes/create", body)


@_tool(
    "data_storage_drop_index",
    "Drops a database index from a Rossum Data Storage collection. "
    "This is a destructive write operation.",
    {
        "type": "object",
        "required": ["collectionName", "indexName"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "indexName": {"type": "string", "description": "The name of the index to drop."},
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_drop_index(request_id, arguments):
    return _data_storage_call(request_id, "/v1/indexes/drop", {
        "collectionName": arguments["collectionName"],
        "indexName": arguments["indexName"],
    })


@_tool(
    "data_storage_drop_search_index",
    "Drops an Atlas Search index from a Rossum Data Storage collection. "
    "This is a destructive write operation.",
    {
        "type": "object",
        "required": ["collectionName", "indexName"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "indexName": {"type": "string", "description": "The name of the search index to drop."},
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_drop_search_index(request_id, arguments):
    return _data_storage_call(request_id, "/v1/search_indexes/drop", {
        "collectionName": arguments["collectionName"],
        "indexName": arguments["indexName"],
    })


@_tool(
    "data_storage_drop_collection",
    "Drops a Rossum Data Storage collection and all its indexes. "
    "This is an async destructive operation (returns 202).",
    {
        "type": "object",
        "required": ["collectionName"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection to drop."},
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_drop_collection(request_id, arguments):
    return _data_storage_call(request_id, "/v1/collections/drop", {
        "collectionName": arguments["collectionName"],
    })


@_tool(
    "data_storage_rename_collection",
    "Renames a Rossum Data Storage collection.",
    {
        "type": "object",
        "required": ["collectionName", "target"],
        "properties": {
            "collectionName": {"type": "string", "description": "Current name of the collection."},
            "target": {"type": "string", "description": "New name for the collection."},
            "dropTarget": {
                "type": "boolean",
                "description": "Drop the target collection if it already exists (default: false).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_rename_collection(request_id, arguments):
    body = {"collectionName": arguments["collectionName"], "target": arguments["target"]}
    if "dropTarget" in arguments:
        body["dropTarget"] = arguments["dropTarget"]
    return _data_storage_call(request_id, "/v1/collections/rename", body)


@_tool(
    "data_storage_find",
    "Queries documents in a Rossum Data Storage collection. Simpler than aggregate "
    "for basic lookups. Returns matching documents up to the specified limit.",
    {
        "type": "object",
        "required": ["collectionName"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "query": {"type": "object", "description": "MongoDB query filter (default: {} returns all)."},
            "projection": {"type": "object", "description": "Fields to include (1) or exclude (0)."},
            "sort": {"type": "object", "description": "Sort specification (e.g. {\"createdAt\": -1})."},
            "limit": {
                "type": "integer",
                "description": "Maximum documents to return (default: 50, max: 1000).",
            },
            "skip": {
                "type": "integer",
                "description": "Number of documents to skip before returning results.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_find(request_id, arguments):
    query = arguments.get("query", {})
    if isinstance(query, str):
        query = json.loads(query)
    body = {"collectionName": arguments["collectionName"], "query": query}
    if "projection" in arguments:
        body["projection"] = arguments["projection"]
    if "sort" in arguments:
        body["sort"] = arguments["sort"]
    body["limit"] = min(arguments.get("limit", 50), 1000)
    if "skip" in arguments:
        body["skip"] = arguments["skip"]
    return _data_storage_call(request_id, "/v1/data/find", body)


@_tool(
    "data_storage_insert",
    "Inserts one or more documents into a Rossum Data Storage collection. "
    "Implicitly creates the collection if it does not exist. "
    "Pass a single object in 'documents' for insert_one, or multiple for insert_many.",
    {
        "type": "object",
        "required": ["collectionName", "documents"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "documents": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of documents to insert (1 for insert_one, >1 for insert_many).",
            },
            "ordered": {
                "type": "boolean",
                "description": "For insert_many: process inserts in order, stopping on first error (default: false).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_insert(request_id, arguments):
    collection = arguments["collectionName"]
    docs = arguments["documents"]
    if len(docs) == 1:
        body = {"collectionName": collection, "document": docs[0]}
        return _data_storage_call(request_id, "/v1/data/insert_one", body)
    body = {"collectionName": collection, "documents": docs}
    if "ordered" in arguments:
        body["ordered"] = arguments["ordered"]
    return _data_storage_call(request_id, "/v1/data/insert_many", body)


_UPDATE_SCHEMA = {
    "type": "object",
    "required": ["collectionName", "filter", "update"],
    "properties": {
        "collectionName": {"type": "string", "description": "The name of the collection."},
        "filter": {"type": "object", "description": "MongoDB query filter to select documents."},
        "update": {
            "description": (
                "MongoDB update document (e.g. {\"$set\": {\"field\": \"value\"}}) "
                "or an aggregation pipeline (array of stages)."
            ),
        },
        "options": {
            "type": "object",
            "description": "Update options (e.g. {\"upsert\": true}).",
        },
    },
    "additionalProperties": False,
}

_DELETE_SCHEMA = {
    "type": "object",
    "required": ["collectionName", "filter"],
    "properties": {
        "collectionName": {"type": "string", "description": "The name of the collection."},
        "filter": {"type": "object", "description": "MongoDB query filter to select documents to delete."},
        "options": {"type": "object", "description": "Delete options."},
    },
    "additionalProperties": False,
}


def _handle_ds_write(request_id, arguments, path):
    body = {"collectionName": arguments["collectionName"], "filter": arguments["filter"]}
    for key in ("update", "replacement", "options"):
        if key in arguments:
            body[key] = arguments[key]
    return _data_storage_call(request_id, path, body)


@_tool(
    "data_storage_update_one",
    "Updates the first document matching the filter in a Rossum Data Storage collection. "
    "Use MongoDB update operators like $set, $unset, $inc, $push, etc.",
    _UPDATE_SCHEMA,
    annotations=_WRITE,
)
def handle_update_one(request_id, arguments):
    return _handle_ds_write(request_id, arguments, "/v1/data/update_one")


@_tool(
    "data_storage_update_many",
    "Updates all documents matching the filter in a Rossum Data Storage collection. "
    "Use MongoDB update operators like $set, $unset, $inc, $push, etc.",
    _UPDATE_SCHEMA,
    annotations=_WRITE,
)
def handle_update_many(request_id, arguments):
    return _handle_ds_write(request_id, arguments, "/v1/data/update_many")


@_tool(
    "data_storage_delete_one",
    "Deletes the first document matching the filter from a Rossum Data Storage collection.",
    _DELETE_SCHEMA,
    annotations=_DESTRUCTIVE,
)
def handle_delete_one(request_id, arguments):
    return _handle_ds_write(request_id, arguments, "/v1/data/delete_one")


@_tool(
    "data_storage_delete_many",
    "Deletes all documents matching the filter from a Rossum Data Storage collection.",
    _DELETE_SCHEMA,
    annotations=_DESTRUCTIVE,
)
def handle_delete_many(request_id, arguments):
    return _handle_ds_write(request_id, arguments, "/v1/data/delete_many")


@_tool(
    "data_storage_replace_one",
    "Replaces the first document matching the filter in a Rossum Data Storage collection "
    "with the provided replacement document.",
    {
        "type": "object",
        "required": ["collectionName", "filter", "replacement"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "filter": {"type": "object", "description": "MongoDB query filter to select the document."},
            "replacement": {"type": "object", "description": "The replacement document (replaces the entire document except _id)."},
            "options": {
                "type": "object",
                "description": "Replace options (e.g. {\"upsert\": true}).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_replace_one(request_id, arguments):
    return _handle_ds_write(request_id, arguments, "/v1/data/replace_one")


@_tool(
    "data_storage_bulk_write",
    "Performs multiple write operations atomically on a Rossum Data Storage collection. "
    "This is an async operation (returns 202). Each operation is a single-key object: "
    "insertOne, updateOne, updateMany, deleteOne, deleteMany, or replaceOne.",
    {
        "type": "object",
        "required": ["collectionName", "operations"],
        "properties": {
            "collectionName": {"type": "string", "description": "The name of the collection."},
            "operations": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Array of write operations. Each is a single-key object: "
                    "{\"insertOne\": {\"document\": {...}}}, "
                    "{\"updateOne\": {\"filter\": {...}, \"update\": {...}}}, "
                    "{\"deleteOne\": {\"filter\": {...}}}, "
                    "{\"replaceOne\": {\"filter\": {...}, \"replacement\": {...}}}."
                ),
            },
            "options": {"type": "object", "description": "Bulk write options."},
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_bulk_write(request_id, arguments):
    body = {"collectionName": arguments["collectionName"], "operations": arguments["operations"]}
    if "options" in arguments:
        body["options"] = arguments["options"]
    return _data_storage_call(request_id, "/v1/data/bulk_write", body)


@_tool(
    "rossum_list_groups",
    "Lists available user roles (groups) and their IDs. "
    "Use these IDs for the group_ids parameter when creating users.",
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_groups(request_id, arguments):
    _rossum_list(request_id, "/api/v1/groups", [("page_size", 100)])


@_tool(
    "rossum_list_users",
    "Lists all users in the Rossum organization. Auto-paginates to return every user.",
    {
        "type": "object",
        "properties": {
            "is_active": {"type": "boolean", "description": "Filter by active status. Omit to return all users."},
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_users(request_id, arguments):
    params = [("page_size", 100)]
    if "is_active" in arguments:
        params.append(("is_active", "true" if arguments["is_active"] else "false"))
    _rossum_list(request_id, "/api/v1/users", params, pick_fields=_USER_FIELDS)


@_tool(
    "rossum_create_user",
    "Creates a new user in the Rossum organization. "
    "Use rossum_whoami to get the organization ID, rossum_list_groups to decide which group_ids to assign, "
    "and rossum_list_users to verify the user was created.",
    {
        "type": "object",
        "required": ["username", "first_name", "last_name", "organization_id", "group_ids"],
        "properties": {
            "username": {
                "type": "string",
                "description": "Login username (can be any string, does not have to be an email).",
            },
            "first_name": {
                "type": "string",
                "description": "User's first name.",
            },
            "last_name": {
                "type": "string",
                "description": "User's last name.",
            },
            "organization_id": {
                "type": "integer",
                "description": "Organization ID the user belongs to (from rossum_whoami).",
            },
            "password": {
                "type": "string",
                "description": "Initial password. If omitted, user must set password via activation email.",
            },
            "email": {
                "type": "string",
                "description": "User's email address.",
            },
            "group_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Group IDs for role assignment (e.g. organization admin, manager, annotator).",
            },
            "queue_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Queue IDs the user can access.",
            },
            "oidc_id": {
                "type": "string",
                "description": "OpenID Connect identifier for SSO users.",
            },
            "auth_type": {
                "type": "string",
                "description": "Authentication type (e.g. 'sso'). Omit for password-based auth.",
            },
            "is_active": {
                "type": "boolean",
                "description": "Whether the account is active (default: true).",
            },
            "metadata": {
                "type": "object",
                "description": "Custom JSON metadata (max 4 KB).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_create_user(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    body = {
        "username": arguments["username"],
        "first_name": arguments["first_name"],
        "last_name": arguments["last_name"],
        "organization": f"{base_url}/api/v1/organizations/{arguments['organization_id']}",
    }
    if "password" in arguments:
        body["password"] = arguments["password"]
    if "email" in arguments:
        body["email"] = arguments["email"]
    body["groups"] = [f"{base_url}/api/v1/groups/{gid}" for gid in arguments["group_ids"]]
    if "queue_ids" in arguments:
        body["queues"] = [f"{base_url}/api/v1/queues/{qid}" for qid in arguments["queue_ids"]]
    if "oidc_id" in arguments:
        body["oidc_id"] = arguments["oidc_id"]
    if "auth_type" in arguments:
        body["auth_type"] = arguments["auth_type"]
    if "is_active" in arguments:
        body["is_active"] = arguments["is_active"]
    if "metadata" in arguments:
        body["metadata"] = arguments["metadata"]
    _rossum_post(request_id, "/api/v1/users", body)


@_tool(
    "rossum_list_audit_logs",
    "List audit log entries. Requires admin or organization group admin role AND the audit log "
    "feature flag enabled on the organization. If this call returns HTTP 403, the feature is "
    "likely disabled — check rossum_get_organization to verify feature flags. "
    "Logs are retained for 1 year. Returns up to max_results entries (default 100).",
    {
        "type": "object",
        "required": ["object_type"],
        "properties": {
            "object_type": {
                "type": "string",
                "description": "Object type to query: 'document', 'annotation', or 'user'.",
            },
            "action": {
                "type": "string",
                "description": (
                    "Filter by action. Allowed values depend on object_type: "
                    "document: 'create'. "
                    "annotation: 'update-status'. "
                    "user: 'create', 'delete', 'purge', 'update', 'destroy', "
                    "'app_load', 'reset-password', 'change-password'."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return (default: 100, max: 1000).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_audit_logs(request_id, arguments):
    max_results = min(arguments.get("max_results", 100), 1000)
    params = [("page_size", min(max_results, 100)), ("object_type", arguments["object_type"])]
    if "action" in arguments:
        params.append(("action", arguments["action"]))
    _rossum_list(request_id, "/api/v1/audit_logs", params, max_results=max_results)


@_tool(
    "rossum_get_hook_secret_keys",
    "Retrieves the list of secret key names configured on a hook. "
    "Only key names are returned — values are encrypted and cannot be retrieved via the API.",
    {
        "type": "object",
        "required": ["hook_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The hook ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_hook_secret_keys(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/hooks/{arguments['hook_id']}/secrets_keys")


@_tool(
    "rossum_list_hook_logs",
    "Lists recent hook execution logs. Essential for debugging hook failures — shows "
    "which hooks ran, their status (succeeded/failed/skipped), timing, and error messages. "
    "Filter by hook ID, annotation, queue, status, or time range.",
    {
        "type": "object",
        "properties": {
            "hook": {
                "type": "integer",
                "description": "Filter by hook ID.",
            },
            "annotation": {
                "type": "integer",
                "description": "Filter by annotation ID.",
            },
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID.",
            },
            "status": {
                "type": "string",
                "description": "Filter by execution status.",
            },
            "log_level": {
                "type": "string",
                "description": "Filter by log level.",
            },
            "timestamp_after": {
                "type": "string",
                "description": "Filter: logs after this ISO 8601 timestamp.",
            },
            "timestamp_before": {
                "type": "string",
                "description": "Filter: logs before this ISO 8601 timestamp.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return (default: 20, max: 200).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_hook_logs(request_id, arguments):
    max_results = min(arguments.get("max_results", 20), 200)
    params = [("page_size", min(max_results, 100))]
    for key in ("hook", "annotation", "queue", "status", "log_level",
                "timestamp_after", "timestamp_before"):
        if key in arguments:
            params.append((key, arguments[key]))
    _rossum_list(
        request_id, "/api/v1/hooks/logs", params,
        max_results=max_results, pick_fields=_HOOK_LOG_FIELDS,
    )


@_tool(
    "rossum_list_annotations",
    "Lists annotations in a queue. Annotations represent documents being processed. "
    "Use this to find annotation IDs for rossum_get_annotation_content.",
    {
        "type": "object",
        "required": ["queue"],
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Queue ID to list annotations from.",
            },
            "status": {
                "type": "string",
                "description": (
                    "Filter by status: 'to_review', 'reviewing', 'confirmed', "
                    "'rejected', 'exporting', 'exported', 'failed_export', "
                    "'postponed', 'deleted', 'purged', 'split', 'importing'."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum annotations to return (default: 50, max: 500).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_annotations(request_id, arguments):
    max_results = min(arguments.get("max_results", 50), 500)
    params = [("page_size", min(max_results, 100)), ("queue", arguments["queue"])]
    if "status" in arguments:
        params.append(("status", arguments["status"]))
    _rossum_list(
        request_id, "/api/v1/annotations", params,
        max_results=max_results, pick_fields=_ANNOTATION_FIELDS,
    )


@_tool(
    "rossum_search_annotations",
    "Search annotations across queues with flexible filtering. More powerful than "
    "rossum_list_annotations: supports cross-queue search (no required queue), "
    "date ranges, ordering, and workspace filtering. Use this to find specific "
    "documents by status, date, or across multiple queues. "
    "Use rossum_get_annotation_content to retrieve extracted data for a specific result.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID. Omit to search across all queues.",
            },
            "status": {
                "type": "string",
                "description": (
                    "Filter by status: 'to_review', 'reviewing', 'confirmed', "
                    "'rejected', 'exporting', 'exported', 'failed_export', "
                    "'postponed', 'deleted', 'purged', 'split', 'importing'."
                ),
            },
            "workspace": {
                "type": "integer",
                "description": "Filter by workspace ID.",
            },
            "labels": {
                "type": "integer",
                "description": (
                    "Filter by label ID. Returns only annotations that have this "
                    "label assigned. Useful for checking whether a label is in "
                    "active use (max_results=1 is enough to confirm presence)."
                ),
            },
            "created_at_after": {
                "type": "string",
                "description": "Filter: created after this ISO 8601 date (e.g. '2024-01-01T00:00:00Z').",
            },
            "created_at_before": {
                "type": "string",
                "description": "Filter: created before this ISO 8601 date.",
            },
            "ordering": {
                "type": "string",
                "description": (
                    "Sort order. Use field name for ascending, prefix with '-' for "
                    "descending (e.g. '-created_at', 'modified_at')."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum annotations to return (default: 50, max: 500).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_search_annotations(request_id, arguments):
    max_results = min(int(arguments.get("max_results", 50)), 500)
    page_size = min(max_results, 100)

    params = [("page_size", page_size)]
    for key in ("queue", "status", "workspace", "labels"):
        if key in arguments:
            params.append((key, arguments[key]))
    if "created_at_after" in arguments:
        params.append(("created_at_after", arguments["created_at_after"]))
    if "created_at_before" in arguments:
        params.append(("created_at_before", arguments["created_at_before"]))
    if "ordering" in arguments:
        params.append(("ordering", arguments["ordering"]))

    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return

    result = _paginate(
        request_id, f"{base_url}/api/v1/annotations?{urlencode(params)}",
        max_results=max_results, pick_fields=_ANNOTATION_FIELDS,
    )
    if result is not None:
        results, api_total = result
        tool_result(request_id, json.dumps({
            "total": api_total,
            "returned": len(results),
            "results": results,
        }, indent=2))


@_tool(
    "rossum_get_annotation_content",
    "Retrieves the extracted data (content) of a single annotation. "
    "Returns the annotation's data tree: sections containing datapoints and multivalues (tables).",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_annotation_content(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/annotations/{arguments['annotation_id']}/content")


@_tool(
    "rossum_list_queues",
    "Lists all queues in the Rossum organization. Queues are the core processing unit — "
    "each represents a document intake pipeline with its own schema and hooks.",
    {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "integer",
                "description": "Filter by workspace ID.",
            },
            "status": {
                "type": "string",
                "description": "Filter by status: 'active', 'inactive', or 'deletion_requested'.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_queues(request_id, arguments):
    params = [("page_size", 100)]
    if "workspace" in arguments:
        params.append(("workspace", arguments["workspace"]))
    if "status" in arguments:
        params.append(("status", arguments["status"]))
    _rossum_list(request_id, "/api/v1/queues", params, pick_fields=_QUEUE_FIELDS)


@_tool(
    "rossum_get_queue",
    "Retrieves full details of a single queue including inbox, connector, locale, "
    "and all configuration. Use rossum_list_queues first to find queue IDs.",
    {
        "type": "object",
        "required": ["queue_id"],
        "properties": {
            "queue_id": {
                "type": "integer",
                "description": "The queue ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_queue(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/queues/{arguments['queue_id']}")


@_tool(
    "rossum_list_hooks",
    "Lists all hooks (extensions) in the Rossum organization. Hooks are serverless functions "
    "or webhook endpoints triggered by queue events.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID — return only hooks attached to this queue.",
            },
            "active": {
                "type": "boolean",
                "description": "Filter by active status.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_hooks(request_id, arguments):
    params = [("page_size", 100)]
    if "queue" in arguments:
        params.append(("queue", arguments["queue"]))
    if "active" in arguments:
        params.append(("active", "true" if arguments["active"] else "false"))
    _rossum_list(request_id, "/api/v1/hooks", params, pick_fields=_HOOK_FIELDS)


@_tool(
    "rossum_get_hook",
    "Retrieves full details of a single hook (extension) including its code, URL, "
    "settings, secrets key names, and configuration. Use rossum_list_hooks first to find hook IDs.",
    {
        "type": "object",
        "required": ["hook_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The hook ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_hook(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/hooks/{arguments['hook_id']}")


@_tool(
    "rossum_create_hook",
    "Creates a new hook (extension) in the Rossum organization. Hooks can be serverless functions "
    "(type='function') executed in Python 3.12 or webhooks (type='webhook') that POST to an external URL. "
    "This is a write operation.",
    {
        "type": "object",
        "required": ["name", "type", "events", "config"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Display name for the hook.",
            },
            "type": {
                "type": "string",
                "description": "Hook type: 'function' (serverless Python 3.12) or 'webhook' (external HTTP endpoint).",
            },
            "events": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Events that trigger this hook. Common values: "
                    "'annotation_content.initialize', 'annotation_content.started', "
                    "'annotation_content.updated', 'annotation_content.confirm', "
                    "'annotation_content.export', 'annotation_content.user_update', "
                    "'email.received', 'invocation.manual'."
                ),
            },
            "config": {
                "type": "object",
                "description": (
                    "Type-specific configuration. "
                    "For function: {\"runtime\": \"python3.12\", \"code\": \"def rossum_hook_request_handler(payload):\\n    return payload\"}. "
                    "For webhook: {\"url\": \"https://example.com/webhook\"}. "
                    "Optional config keys: timeout_s (default 30), retry_count, payload_logging_enabled."
                ),
            },
            "queue_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Queue IDs to attach this hook to. Omit to create unattached.",
            },
            "active": {
                "type": "boolean",
                "description": "Whether the hook is active (default: true).",
            },
            "run_after": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Hook IDs that must run before this one (execution ordering).",
            },
            "sideload": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional data to include in payloads (e.g. ['schemas']).",
            },
            "token_owner": {
                "type": "integer",
                "description": "User ID whose permissions the hook uses for API calls.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_create_hook(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    body = {
        "name": arguments["name"],
        "type": arguments["type"],
        "events": arguments["events"],
        "config": arguments["config"],
        "active": arguments.get("active", True),
        "queues": [f"{base_url}/api/v1/queues/{qid}" for qid in arguments.get("queue_ids", [])],
    }
    if "run_after" in arguments:
        body["run_after"] = [f"{base_url}/api/v1/hooks/{hid}" for hid in arguments["run_after"]]
    if "sideload" in arguments:
        body["sideload"] = arguments["sideload"]
    if "token_owner" in arguments:
        body["token_owner"] = f"{base_url}/api/v1/users/{arguments['token_owner']}"
    _rossum_post(request_id, "/api/v1/hooks", body)


@_tool(
    "rossum_delete_hook",
    "Deletes a hook (extension) from the Rossum organization. "
    "This is a destructive operation that cannot be undone.",
    {
        "type": "object",
        "required": ["hook_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The hook ID to delete.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_delete_hook(request_id, arguments):
    _rossum_delete(request_id, f"/api/v1/hooks/{arguments['hook_id']}")


@_tool(
    "rossum_patch_hook",
    "Updates an existing hook (extension). Only provide the fields you want to change — "
    "unspecified fields are left untouched. Use this to update hook code, toggle active state, "
    "change events, or reassign queues without recreating the hook. This is a write operation.",
    {
        "type": "object",
        "required": ["hook_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The hook ID to update.",
            },
            "name": {
                "type": "string",
                "description": "New display name.",
            },
            "config": {
                "type": "object",
                "description": (
                    "Updated config. For function hooks: {\"code\": \"...\"}. "
                    "Only include keys you want to change."
                ),
            },
            "events": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated event triggers (replaces the full list).",
            },
            "active": {
                "type": "boolean",
                "description": "Enable or disable the hook.",
            },
            "queue_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Replace attached queues (full list, not additive).",
            },
            "run_after": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Replace execution ordering dependencies.",
            },
            "sideload": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated sideload configuration.",
            },
            "token_owner": {
                "type": "integer",
                "description": "User ID whose permissions the hook uses.",
            },
            "settings": {
                "type": "object",
                "description": "Updated hook settings.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_patch_hook(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    hook_id = arguments["hook_id"]
    body = {}
    for key in ("name", "config", "events", "active", "sideload", "settings"):
        if key in arguments:
            body[key] = arguments[key]
    if "queue_ids" in arguments:
        body["queues"] = [f"{base_url}/api/v1/queues/{qid}" for qid in arguments["queue_ids"]]
    if "run_after" in arguments:
        body["run_after"] = [f"{base_url}/api/v1/hooks/{hid}" for hid in arguments["run_after"]]
    if "token_owner" in arguments:
        body["token_owner"] = f"{base_url}/api/v1/users/{arguments['token_owner']}"
    _rossum_patch(request_id, f"/api/v1/hooks/{hook_id}", body)


@_tool(
    "rossum_get_schema",
    "Retrieves the full schema definition of a queue. The schema defines all datapoints "
    "(fields), sections, multivalue (table) structures, and their validation rules.",
    {
        "type": "object",
        "required": ["schema_id"],
        "properties": {
            "schema_id": {
                "type": "integer",
                "description": "The schema ID (found in queue.schema URL).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_schema(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/schemas/{arguments['schema_id']}")


@_tool(
    "rossum_patch_schema",
    "Updates an existing schema. Only provide the fields you want to change. "
    "Most commonly used to update the 'content' field (the datapoint tree). "
    "This is a write operation that affects all queues using this schema.",
    {
        "type": "object",
        "required": ["schema_id"],
        "properties": {
            "schema_id": {
                "type": "integer",
                "description": "The schema ID to update.",
            },
            "name": {
                "type": "string",
                "description": "New name for the schema.",
            },
            "content": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Updated schema content (the full datapoint tree: sections, fields, multivalues).",
            },
            "metadata": {
                "type": "object",
                "description": "Custom metadata (max 4 KB).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_patch_schema(request_id, arguments):
    schema_id = arguments["schema_id"]
    body = {}
    for key in ("name", "content", "metadata"):
        if key in arguments:
            body[key] = arguments[key]
    _rossum_patch(request_id, f"/api/v1/schemas/{schema_id}", body)


@_tool(
    "rossum_list_schemas",
    "Lists all schemas in the Rossum organization. Schemas define the data structure "
    "(fields, sections, tables) for document extraction in queues.",
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_schemas(request_id, arguments):
    _rossum_list(request_id, "/api/v1/schemas", [("page_size", 100)], pick_fields=_SCHEMA_FIELDS)


@_tool(
    "rossum_list_workspaces",
    "Lists all workspaces in the Rossum organization. Workspaces group queues "
    "and define organizational boundaries.",
    {
        "type": "object",
        "properties": {
            "organization": {
                "type": "integer",
                "description": "Filter by organization ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_workspaces(request_id, arguments):
    params = [("page_size", 100)]
    if "organization" in arguments:
        params.append(("organization", arguments["organization"]))
    _rossum_list(request_id, "/api/v1/workspaces", params, pick_fields=_WORKSPACE_FIELDS)


@_tool(
    "rossum_get_workspace",
    "Retrieves full details of a single workspace including its queues, organization, "
    "and autopilot settings. Use rossum_list_workspaces first to find workspace IDs.",
    {
        "type": "object",
        "required": ["workspace_id"],
        "properties": {
            "workspace_id": {
                "type": "integer",
                "description": "The workspace ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_workspace(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/workspaces/{arguments['workspace_id']}")


@_tool(
    "rossum_get_organization",
    "Retrieves details of the organization including name, trial status, and feature flags. "
    "The organization ID can be found in rossum_whoami output.",
    {
        "type": "object",
        "required": ["organization_id"],
        "properties": {
            "organization_id": {
                "type": "integer",
                "description": "The organization ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_organization(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/organizations/{arguments['organization_id']}")


@_tool(
    "rossum_get_document",
    "Retrieves metadata of a document (original file name, MIME type, creation time, "
    "annotations). Documents are referenced by annotations — extract the document ID "
    "from the annotation's document URL.",
    {
        "type": "object",
        "required": ["document_id"],
        "properties": {
            "document_id": {
                "type": "integer",
                "description": "The document ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_document(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/documents/{arguments['document_id']}")


@_tool(
    "rossum_get_annotation",
    "Retrieves full metadata of a single annotation including status, messages (validation "
    "errors and automation blockers), metadata (hook state flags), timestamps, and email info. "
    "Use rossum_list_annotations first to find annotation IDs.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_annotation(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/annotations/{arguments['annotation_id']}")


@_tool(
    "rossum_patch_annotation",
    "Updates an annotation. Most commonly used to change status (e.g. confirm, reject, "
    "move to review, export). Only provide the fields you want to change. "
    "This is a write operation.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID to update.",
            },
            "status": {
                "type": "string",
                "description": (
                    "New status. Common transitions: "
                    "'to_review' (send back for review), "
                    "'confirmed' (confirm the annotation), "
                    "'rejected' (reject the annotation), "
                    "'exporting' (trigger export), "
                    "'postponed' (postpone processing), "
                    "'deleted' (soft-delete)."
                ),
            },
            "metadata": {
                "type": "object",
                "description": "Custom metadata (max 4 KB). Merged with existing metadata.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_patch_annotation(request_id, arguments):
    annotation_id = arguments["annotation_id"]
    body = {}
    for key in ("status", "metadata"):
        if key in arguments:
            body[key] = arguments[key]
    _rossum_patch(request_id, f"/api/v1/annotations/{annotation_id}", body)


@_tool(
    "rossum_get_inbox",
    "Retrieves details of a queue's inbox including email address, bounce email, "
    "and document processing settings. The inbox ID is found in the queue detail response.",
    {
        "type": "object",
        "required": ["inbox_id"],
        "properties": {
            "inbox_id": {
                "type": "integer",
                "description": "The inbox ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_inbox(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/inboxes/{arguments['inbox_id']}")


@_tool(
    "rossum_list_connectors",
    "Lists all connectors (export integrations) in the Rossum organization. "
    "Connectors define where confirmed documents are exported to.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_connectors(request_id, arguments):
    params = [("page_size", 100)]
    if "queue" in arguments:
        params.append(("queue", arguments["queue"]))
    _rossum_list(request_id, "/api/v1/connectors", params, pick_fields=_CONNECTOR_FIELDS)


@_tool(
    "rossum_get_connector",
    "Retrieves full details of a single connector (export integration) including "
    "service URL, authorization, parameters, and queue mapping. "
    "Use rossum_list_connectors first to find connector IDs.",
    {
        "type": "object",
        "required": ["connector_id"],
        "properties": {
            "connector_id": {
                "type": "integer",
                "description": "The connector ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_connector(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/connectors/{arguments['connector_id']}")


@_tool(
    "rossum_list_emails",
    "Lists emails associated with queues. Emails represent incoming messages (with document "
    "attachments) and outgoing auto-replies. Use this to find email IDs for rossum_get_email.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID.",
            },
            "type": {
                "type": "string",
                "description": "Filter by type: 'incoming' or 'outgoing'.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum emails to return (default: 50, max: 500).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_emails(request_id, arguments):
    max_results = min(arguments.get("max_results", 50), 500)
    params = [("page_size", min(max_results, 100))]
    if "queue" in arguments:
        params.append(("queue", arguments["queue"]))
    if "type" in arguments:
        params.append(("type", arguments["type"]))
    _rossum_list(
        request_id, "/api/v1/emails", params,
        max_results=max_results, pick_fields=_EMAIL_FIELDS,
    )


@_tool(
    "rossum_get_email",
    "Retrieves full details of a single email including subject, sender, recipients, "
    "plain text and HTML body, linked documents and annotations, and thread info. "
    "Use rossum_list_emails first to find email IDs.",
    {
        "type": "object",
        "required": ["email_id"],
        "properties": {
            "email_id": {
                "type": "integer",
                "description": "The email ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_email(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/emails/{arguments['email_id']}")


@_tool(
    "rossum_list_email_threads",
    "Lists email threads. Threads group related incoming and outgoing emails together. "
    "Use this to get an overview of email conversations per queue.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum threads to return (default: 50, max: 500).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_email_threads(request_id, arguments):
    max_results = min(arguments.get("max_results", 50), 500)
    params = [("page_size", min(max_results, 100))]
    if "queue" in arguments:
        params.append(("queue", arguments["queue"]))
    _rossum_list(
        request_id, "/api/v1/email_threads", params,
        max_results=max_results, pick_fields=_EMAIL_THREAD_FIELDS,
    )


@_tool(
    "rossum_get_email_thread",
    "Retrieves full details of a single email thread including root email, reply status, "
    "annotation counts, and labels. Use rossum_list_email_threads first to find thread IDs.",
    {
        "type": "object",
        "required": ["thread_id"],
        "properties": {
            "thread_id": {
                "type": "integer",
                "description": "The email thread ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_email_thread(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/email_threads/{arguments['thread_id']}")


# --- Main loop ---


def main():
    while True:
        message = read_message()
        if message is None:
            break

        if not isinstance(message, dict):
            continue

        method = message.get("method")
        request_id = message.get("id")

        try:
            if method == "initialize":
                global _client_capabilities
                _client_capabilities = message.get("params", {}).get("capabilities", {})
                respond(request_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "rossum-api", "version": "0.14.0"},
                    "instructions": (
                        "SAFETY RULE — confirmation before writes: "
                        "Do NOT call any write, update, patch, create, or delete tool "
                        "unless the user has explicitly requested or approved the operation. "
                        "This includes all rossum_create_*, rossum_patch_*, rossum_delete_* tools, "
                        "all data_storage write tools (insert, update, delete, replace, bulk_write, drop), "
                        "and any prd2 push/deploy commands. "
                        "Read-only tools (list, get, find, aggregate, whoami) are fine without confirmation. "
                        "When in doubt, describe what you intend to do and ask first. "
                        "EDITING RULE — local file workflow: "
                        "When modifying hook code or formula logic in a prd project, only edit the local .py files. "
                        "Never edit the code field in hook JSON or the formula property in schema.json — "
                        "prd2 push syncs .py files into JSON automatically. "
                        "Do not call rossum_patch_hook or rossum_patch_schema to push code changes that "
                        "should go through prd2 push instead."
                    ),
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                respond(request_id, {"tools": list(TOOLS.values())})
            elif method == "tools/call":
                params = message.get("params", {})
                name = params.get("name")
                handler = HANDLERS.get(name)
                if handler:
                    handler(request_id, params.get("arguments") or {})
                else:
                    tool_result(request_id, f"Unknown tool: {name}", is_error=True)
            elif method == "ping":
                respond(request_id, {})
            elif request_id is not None:
                respond_error(request_id, -32601, f"Method not found: {method}")
        except Exception as e:
            _log(f"Error handling {method}: {e}")
            if request_id is not None:
                respond_error(request_id, -32603, f"Internal error: {e}")


if __name__ == "__main__":
    main()
