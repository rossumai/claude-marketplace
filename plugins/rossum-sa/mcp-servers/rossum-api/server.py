#!/usr/bin/env python3
"""MCP server for Rossum APIs."""

import json
import re
import ssl
import sys
import time
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
        headers=_auth_headers(token=token),
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


_SERVER_VERSION = "0.23.0"
_USER_AGENT = f"rossum-sa-mcp/{_SERVER_VERSION}"
_current_tool = None  # name of the in-flight tool; emitted as X-Rossum-MCP-Tool


def _auth_headers(extra=None, token=None):
    """Auth + telemetry headers for every Rossum API request.

    Always sets Authorization (Bearer) and a stable User-Agent. When a tool call
    is in flight, adds X-Rossum-MCP-Tool so server-side telemetry can attribute
    traffic per tool. `extra` merges in/overrides (e.g. Content-Type). `token`
    overrides _cached_token (used by the pre-auth validation probe)."""
    headers = {"Authorization": f"Bearer {token or _cached_token}", "User-Agent": _USER_AGENT}
    if _current_tool:
        headers["X-Rossum-MCP-Tool"] = _current_tool
    if extra:
        headers.update(extra)
    return headers


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

    headers = _auth_headers()
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


def _http_request_silent(url, *, method="GET"):
    """Like _http_request but never sends a tool_result on failure. Returns the HTTP
    status code (int) on any HTTP-level outcome, or None on network/SSL error.
    Used for finally-cancel and other best-effort cleanup paths."""
    token = _cached_token
    if not token:
        return None
    req = urllib.request.Request(
        url, data=None, headers=_auth_headers(), method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _http_request_status(url, *, method="GET", body=None):
    """Make an authenticated request and return (status_code, parsed_body).

    Never sends a tool_result and never raises: HTTP errors return their real
    status code with the (JSON-parsed when possible) error body; network/SSL
    failures return (None, error_message). Used by tools that must degrade
    gracefully instead of erroring, e.g. rossum_get_automation_projections.
    """
    token = _cached_token
    if not token:
        return None, "Not connected to Rossum."
    headers = _auth_headers()
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=130, context=_ssl_context) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Reading/decoding the error body can itself fail (dropped connection,
        # non-UTF-8 proxy page) — that must not escape the never-raise contract.
        try:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        except Exception:
            error_body = str(e)
        try:
            error_body = json.loads(error_body)
        except (json.JSONDecodeError, ValueError):
            pass
        return e.code, error_body
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _http_request_raw(request_id, url, *, method="POST", raw_body=b"", content_type=None):
    """POST raw bytes (e.g. multipart upload). Returns parsed JSON or None (error sent)."""
    token = _cached_token
    if not token:
        return None
    headers = _auth_headers({"Content-Type": content_type} if content_type else None)
    req = urllib.request.Request(url, data=raw_body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=130, context=_ssl_context) as resp:
            data = resp.read()
            if not data:
                return {}
            return json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        tool_result(request_id, f"HTTP {e.code}: {error_body}", is_error=True)
        return None
    except Exception as e:
        tool_result(request_id, f"Error: {e}", is_error=True)
        return None


def _http_get_bytes(request_id, url):
    """GET that returns raw bytes (for downloading PDFs etc). None on error (already sent)."""
    token = _cached_token
    if not token:
        return None
    req = urllib.request.Request(url, headers=_auth_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=130, context=_ssl_context) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        tool_result(request_id, f"HTTP {e.code}: {error_body}", is_error=True)
        return None
    except Exception as e:
        tool_result(request_id, f"Error: {e}", is_error=True)
        return None


def _http_get_typed(request_id, url):
    """GET that distinguishes JSON from non-JSON. Returns (content_type, parsed_json).

    JSON response -> ("application/json", <parsed dict/list>). Non-JSON (PDF, zip,
    csv, ...) -> (content_type, None), body not read. On error: sends a tool_result
    and returns (None, None). urllib follows 3xx automatically, so a tasks/{id} 303
    resolves to its result object."""
    if not _cached_token:
        return (None, None)
    req = urllib.request.Request(url, headers=_auth_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=130, context=_ssl_context) as resp:
            ctype = resp.headers.get_content_type()
            if ctype == "application/json":
                return (ctype, json.loads(resp.read().decode("utf-8")))
            return (ctype, None)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        if e.code == 401:
            _invalidate_connection()
            tool_result(request_id,
                        f"Authentication failed (HTTP 401). Token may be expired. "
                        f"Call rossum_set_token to re-authenticate.\n{error_body}",
                        is_error=True)
            return (None, None)
        tool_result(request_id, f"HTTP {e.code}: {error_body}", is_error=True)
        return (None, None)
    except Exception as e:
        tool_result(request_id, f"Error: {e}", is_error=True)
        return (None, None)


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
    "annotations", "engine", "dedicated_engine", "generic_engine",
})


def _paginate(request_id, url, *, max_results=None, pick_fields=None, initial_page=None):
    """Auto-paginate a Rossum list endpoint. Returns (results, api_total) or None on error.

    `initial_page` (an already-parsed first page) is used in place of fetching page 1,
    so callers that already read page 1 don't pay for a second round-trip."""
    all_results = []
    api_total = None
    page = initial_page
    while url:
        if page is None:
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
        page = None
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
_QUEUE_FIELDS = ("id", "name", "workspace", "schema", "hooks", "status", "engine", "dedicated_engine", "generic_engine")
_HOOK_FIELDS = ("id", "name", "type", "events", "queues", "active", "run_after", "token_owner")
_RULE_FIELDS = ("id", "name", "enabled", "queues")
_RULE_EXEC_LOG_FIELDS = (
    "rule_id", "rule_name", "queue_id", "annotation_id", "trigger_event",
    "execution_result", "execution_error", "created_at", "request_id",
)
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


# --- Compact annotation view ---

# Most-specific source wins; reflects how Rossum populates `validation_sources`.
_SOURCE_PRIORITY = (
    "human", "formula", "connector", "rules",
    "data_matching", "score", "NA",
)


def _id_from_url(url):
    """Extract the trailing integer from a Rossum API URL, or return None."""
    if not url or not isinstance(url, str):
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _best_source(validation_sources):
    """Reduce a validation_sources list to the most-specific entry."""
    if not validation_sources:
        return None
    sources = set(validation_sources)
    for candidate in _SOURCE_PRIORITY:
        if candidate in sources:
            return candidate
    return validation_sources[0]


def _compact_datapoint(node, *, include_ocr=True, verbose=False):
    """Project a single datapoint node to its useful subset.

    Default keys: value, ocr (if distinct), normalized, src, score (when score-sourced).
    Verbose also adds: page, position, options.
    """
    content = node.get("content") or {}
    value = content.get("value")
    ocr = content.get("ocr_text")
    normalized = content.get("normalized_value")
    source = _best_source(node.get("validation_sources"))

    projected = {"value": value}
    if include_ocr and ocr and ocr != value:
        projected["ocr"] = ocr
    if normalized not in (None, "", value):
        projected["normalized"] = normalized
    if source:
        projected["src"] = source
    if source == "score":
        score = content.get("rir_confidence")
        if score is not None:
            projected["score"] = score
    if verbose:
        page = content.get("page")
        if page is not None:
            projected["page"] = page
        position = content.get("position")
        if position:
            projected["position"] = position
        options = node.get("options")
        if options:
            projected["options"] = [
                o.get("value") if isinstance(o, dict) else o for o in options
            ]
    return projected


def _walk_compact_content(content_tree, *, fields=None, include_ocr=True, verbose=False):
    """Walk a Rossum content tree and return ({field schema_id -> value}, {table schema_id -> rows})."""
    fields_filter = set(fields) if fields else None
    flat_fields = {}
    tables = {}

    def visit(node):
        category = node.get("category")
        if category == "section":
            for child in node.get("children") or ():
                visit(child)
        elif category == "datapoint":
            schema_id = node.get("schema_id")
            if not schema_id:
                return
            if fields_filter is not None and schema_id not in fields_filter:
                return
            flat_fields[schema_id] = _compact_datapoint(
                node, include_ocr=include_ocr, verbose=verbose
            )
        elif category == "multivalue":
            schema_id = node.get("schema_id")
            if not schema_id:
                return
            rows = []
            for tuple_node in node.get("children") or ():
                if tuple_node.get("category") != "tuple":
                    continue
                row = {}
                for cell in tuple_node.get("children") or ():
                    if cell.get("category") != "datapoint":
                        continue
                    cell_schema = cell.get("schema_id")
                    if not cell_schema:
                        continue
                    if fields_filter is not None and cell_schema not in fields_filter:
                        continue
                    row[cell_schema] = _compact_datapoint(
                        cell, include_ocr=include_ocr, verbose=verbose
                    )
                rows.append(row)
            tables[schema_id] = {"count": len(rows), "rows": rows}

    for top in content_tree or ():
        visit(top)
    return flat_fields, tables


def _compact_blocker(blocker_payload):
    """Project an automation_blocker resource into its useful items."""
    if not blocker_payload or not isinstance(blocker_payload, dict):
        return None
    items = []
    for item in blocker_payload.get("content") or ():
        if not isinstance(item, dict):
            continue
        items.append({
            "type": item.get("type"),
            "level": item.get("level"),
            "schema_id": item.get("schema_id"),
            "content": item.get("content"),
            "details": item.get("details"),
        })
    if not items:
        return None
    return {"id": blocker_payload.get("id"), "items": items}


def _compact_hook_log(entry):
    """Project a hook log entry into its useful subset."""
    timing = entry.get("timing") or {}
    start = entry.get("start") or timing.get("start")
    end = entry.get("end") or timing.get("end")
    took_ms = None
    if start and end:
        try:
            # Both are ISO 8601 timestamps; subtract for milliseconds.
            from datetime import datetime
            t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
            took_ms = int((t1 - t0).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass
    return {
        "hook_id": entry.get("hook_id"),
        "event": entry.get("event"),
        "action": entry.get("action"),
        "status": entry.get("status"),
        "log_level": entry.get("log_level"),
        "timestamp": entry.get("timestamp"),
        "took_ms": took_ms,
        "message": entry.get("message"),
    }


def _cache_full_payload(annotation_id, payload):
    """Write the raw merged payload to .rossum-cache/annotations/<aid>.json (CWD-relative).

    Returns the path string (relative if it lives under CWD, absolute otherwise),
    or None on failure. The cache is best-effort — failures must not break the response.
    """
    try:
        import os
        cache_dir = os.path.join(os.getcwd(), ".rossum-cache", "annotations")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"{annotation_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        try:
            return os.path.relpath(path, os.getcwd())
        except ValueError:
            return path
    except OSError:
        return None


def _build_annotation_compact_response(
    annotation, content_tree, blocker_payload, hook_log_entries,
    *, view="compact", fields=None,
):
    """Assemble the merged compact response for rossum_get_annotation."""
    include_ocr = True
    verbose = (view == "verbose")
    flat_fields, tables = _walk_compact_content(
        content_tree, fields=fields, include_ocr=include_ocr, verbose=verbose,
    )
    compact = {
        "annotation_id": annotation.get("id"),
        "status": annotation.get("status"),
        "queue_id": _id_from_url(annotation.get("queue")),
        "document_id": _id_from_url(annotation.get("document")),
        "modifier_id": _id_from_url(annotation.get("modifier")),
        "modified_at": annotation.get("modified_at"),
        "confirmed_at": annotation.get("confirmed_at"),
        "exported_at": annotation.get("exported_at"),
        "labels": annotation.get("labels") or [],
        "metadata": annotation.get("metadata") or {},
        "blocker": _compact_blocker(blocker_payload),
        "messages": annotation.get("messages") or [],
        "fields": flat_fields,
        "tables": tables,
        "recent_hooks": [_compact_hook_log(e) for e in (hook_log_entries or ())],
    }
    return compact


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


_GENERIC_GET_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Rossum API path beginning '/api/v1/' (e.g. '/api/v1/engines', "
                "'/api/v1/automation_blockers?annotation=123'), or a full object URL "
                "returned by another call. Query string allowed."
            ),
        },
        "max_results": {
            "type": "integer",
            "description": "Cap for paginated list endpoints (default 100).",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


@_tool(
    "rossum_get",
    "Read-only escape hatch: GET any Rossum API resource that has no dedicated tool "
    "(engines, engine_fields, labels, automation_blockers, workflows, triggers, "
    "relations, pages, tasks, ...). GET-only — cannot create, update, or delete. Pass "
    "an '/api/v1/...' path. Full path catalog: the rossum-reference 'API coverage' doc "
    "(api-coverage.md). Prefer a dedicated tool when one exists.",
    _GENERIC_GET_SCHEMA,
    annotations=_READ_ONLY,
)
def handle_rossum_get(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    raw = (arguments.get("path") or "").strip()
    if not raw:
        tool_result(request_id, "path is required.", is_error=True)
        return
    if raw.startswith(("http://", "https://")):
        if _validate_base_url(raw) != _validate_base_url(base_url):
            tool_result(request_id,
                        f"Refusing: URL is not on the connected org ({base_url}).",
                        is_error=True)
            return
        parsed = urlparse(raw)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    elif raw.startswith("/api/v1/"):
        path = raw
    else:
        tool_result(request_id,
                    "path must start with '/api/v1/' or be a full org URL.",
                    is_error=True)
        return
    url = f"{base_url}{path}"
    ctype, payload = _http_get_typed(request_id, url)
    if ctype is None:
        return  # error already sent (or token vanished — pre-existing guard pattern)
    if ctype != "application/json":
        tool_result(request_id, json.dumps({
            "content_type": ctype,
            "url": url,
            "note": "Non-JSON (binary/export) response — open the URL to retrieve it.",
        }, indent=2))
        return
    if isinstance(payload, dict) and "results" in payload and "pagination" in payload:
        result = _paginate(request_id, url, max_results=arguments.get("max_results", 100), initial_page=payload)
        if result is not None:
            results, api_total = result
            tool_result(request_id, json.dumps(
                {"total": api_total, "returned": len(results), "results": results}, indent=2))
        return
    tool_result(request_id, json.dumps(payload, indent=2))


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
    "likely disabled — check rossum_get with path /api/v1/organizations/{id} to verify feature flags. "
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
    "each represents a document intake pipeline with its own schema and hooks. Each result "
    "includes the extraction binding triple (engine / dedicated_engine / generic_engine): "
    "a non-null 'engine' means a custom engine is bound and schema fields follow engine "
    "binding rules (see rossum-reference → Extraction Engines).",
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


def _cache_automation_payload(queue_id, kind, payload):
    """Write a full automation payload to .rossum-cache/automation/queue_<id>_<kind>.json.

    Returns the CWD-relative path string, or None on failure. Best-effort —
    failures must not break the response.
    """
    try:
        import os
        cache_dir = os.path.join(os.getcwd(), ".rossum-cache", "automation")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"queue_{queue_id}_{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        try:
            return os.path.relpath(path, os.getcwd())
        except ValueError:
            return path
    except OSError:
        return None


_EXAMPLE_IDS_KEPT = 5


def _compact_automation_blockers(blockers, *, keep_example_ids):
    """Project document_blockers items; truncate the up-to-50-ID example lists."""
    compacted = []
    for item in blockers or ():
        ids = item.get("example_annotation_ids") or []
        row = {
            "blocker": item.get("blocker"),
            "granularity": item.get("granularity"),
            "document_count": item.get("document_count"),
        }
        if keep_example_ids:
            row["example_annotation_ids"] = ids[:_EXAMPLE_IDS_KEPT]
            row["example_annotation_count"] = len(ids)
        compacted.append(row)
    return compacted


def _automation_insights_digest(payload):
    """Compact digest of an automation_insights payload: totals, window, blockers,
    per-field statistics — without raw example-ID lists or full timeseries."""
    timeseries = payload.get("document_automation_timeseries") or []
    digest = {
        "document_automation_rate": payload.get("document_automation_rate"),
        "document_touchless_rate": payload.get("document_touchless_rate"),
        "is_aurora_queue": payload.get("is_aurora_queue"),
        "window": {
            "start": timeseries[0]["date"] if timeseries else None,
            "end": timeseries[-1]["date"] if timeseries else None,
            "days": len(timeseries),
            "total_documents": sum(
                (d.get("automated_count") or 0) + (d.get("non_automated_count") or 0)
                for d in timeseries
            ),
            "automated_documents": sum(d.get("automated_count") or 0 for d in timeseries),
            "touchless_documents": sum(d.get("touchless_count") or 0 for d in timeseries),
        },
        "document_blockers": _compact_automation_blockers(
            payload.get("document_blockers"), keep_example_ids=True
        ),
    }
    fields = []
    for stat in payload.get("datapoint_statistics") or ():
        fields.append({
            "schema_id": stat.get("schema_id"),
            "confidence_threshold": stat.get("confidence_threshold"),
            "estimated_error_rate": stat.get("estimated_error_rate"),
            "is_quality_estimate": stat.get("is_quality_estimate"),
            "blocked_document_counts": stat.get("blocked_document_counts"),
            "blockers": _compact_automation_blockers(
                stat.get("blockers"), keep_example_ids=False
            ),
        })
    fields.sort(
        key=lambda f: -sum((f.get("blocked_document_counts") or {}).values())
    )
    digest["datapoint_statistics"] = fields
    error_ts = payload.get("estimated_error_rate_timeseries") or []
    digest["estimated_error_rate_timeseries_points"] = len(error_ts)
    if error_ts:
        digest["estimated_error_rate_latest"] = error_ts[-1]
    return digest


@_tool(
    "rossum_get_automation_insights",
    "Retrieves queue-level automation analytics: automation/touchless rates, a per-day "
    "automation timeseries, document-level blockers with example annotation IDs, and "
    "per-field (datapoint) statistics with confidence thresholds and estimated error "
    "rates. Available on every queue; empty queues return zeroed data. By default "
    "returns a compact digest and caches the full payload to "
    ".rossum-cache/automation/ for follow-up analysis.",
    {
        "type": "object",
        "required": ["queue_id"],
        "properties": {
            "queue_id": {
                "type": "integer",
                "description": "The queue ID.",
            },
            "summary": {
                "type": "boolean",
                "description": "When true (default), return a compact digest (truncated "
                "example-ID lists, summarized timeseries) and cache the full payload to "
                ".rossum-cache/automation/. When false, return the full raw payload.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_automation_insights(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    queue_id = arguments["queue_id"]
    payload = _http_request(
        request_id, f"{base_url}/api/v1/queues/{queue_id}/automation_insights"
    )
    if payload is None:
        return
    if arguments.get("summary") is False:
        tool_result(request_id, json.dumps(payload, indent=2))
        return
    digest = _automation_insights_digest(payload)
    cache_path = _cache_automation_payload(queue_id, "insights", payload)
    if cache_path:
        digest["full_payload_cache"] = cache_path
    tool_result(request_id, json.dumps(digest, indent=2))


@_tool(
    "rossum_get_automation_projections",
    "Simulates automation at recalibrated confidence thresholds for a queue (POST "
    "automation_projections). Returns baseline + projected scenarios with automation "
    "rates, estimated error rates, and per-field thresholds. Never errors on "
    "unavailability: returns {available: false, status_code, reason} when the endpoint "
    "is missing, forbidden, or has no projection scenarios (queues without enough "
    "reviewed documents return HTTP 200 with an empty projections list). Full payload "
    "is cached to .rossum-cache/automation/ when available.",
    {
        "type": "object",
        "required": ["queue_id"],
        "properties": {
            "queue_id": {
                "type": "integer",
                "description": "The queue ID.",
            },
            "fields": {
                "type": "array",
                "description": "Optional per-field error-rate constraints for the "
                "simulation. Each entry sets the maximum acceptable estimated error "
                "rate for one field. Defaults to [] (server picks scenarios).",
                "items": {
                    "type": "object",
                    "required": ["error_rate_limit"],
                    "properties": {
                        "schema_id": {
                            "type": "string",
                            "description": "Schema field ID the limit applies to.",
                        },
                        "error_rate_limit": {
                            "type": "number",
                            "description": "Maximum acceptable estimated error rate "
                            "(e.g. 0.01 for 1%).",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_automation_projections(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    queue_id = arguments["queue_id"]
    status, body = _http_request_status(
        f"{base_url}/api/v1/queues/{queue_id}/automation_projections",
        method="POST",
        body={"fields": arguments.get("fields") or []},
    )
    if status != 200:
        tool_result(
            request_id,
            json.dumps(
                {"available": False, "status_code": status, "reason": body}, indent=2
            ),
        )
        return
    if not isinstance(body, dict):
        tool_result(
            request_id,
            json.dumps(
                {
                    "available": False,
                    "status_code": 200,
                    "reason": "malformed response: expected a JSON object, got "
                    f"{type(body).__name__}",
                },
                indent=2,
            ),
        )
        return
    projections = body.get("projections") or []
    if not projections:
        tool_result(
            request_id,
            json.dumps(
                {
                    "available": False,
                    "status_code": 200,
                    "reason": "no projection scenarios returned — the queue does not "
                    "have enough reviewed documents for a simulation",
                    "total_document_count": body.get("total_document_count"),
                    "used_document_count": body.get("used_document_count"),
                },
                indent=2,
            ),
        )
        return
    baseline = body.get("baseline") or {}
    response = {
        "available": True,
        "total_document_count": body.get("total_document_count"),
        "used_document_count": body.get("used_document_count"),
        "baseline": {
            "document_automation_rate": baseline.get("document_automation_rate"),
            "estimated_error_rate": baseline.get("estimated_error_rate"),
            "document_touchless_rate": baseline.get("document_touchless_rate"),
        },
        "projections": [
            {
                "document_automation_rate": p.get("document_automation_rate"),
                "estimated_error_rate": p.get("estimated_error_rate"),
                "document_touchless_rate": p.get("document_touchless_rate"),
                "field_count": len(p.get("datapoint_statistics") or ()),
            }
            for p in projections
        ],
    }
    cache_path = _cache_automation_payload(queue_id, "projections", body)
    if cache_path:
        response["full_payload_cache"] = cache_path
    tool_result(request_id, json.dumps(response, indent=2))


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


# --- Custom Format Templating export-template helpers ---
# These three tools support authoring the legacy "Custom Format Templating" export
# step, whose Jinja2 template lives in settings.export_configs[]. They are the MCP
# side of the render-export-template skill; faithful Jinja2 rendering itself runs in
# that skill's bundled render_export_template.py (the MCP server stays dependency-free).


def _template_text_to_multiline(template_text):
    r"""Split a template string into the file_content_template_multiline array.

    Inverse of extract's "\n".join(...): one array entry per line, a single
    trailing empty line (from a final newline) dropped, and CRLF tolerated, so a
    round-trip extract -> edit -> generate is line-stable.
    """
    lines = [line.rstrip("\r") for line in template_text.split("\n")]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


@_tool(
    "rossum_extract_export_template",
    "Pulls the Jinja2 template out of a Custom Format Templating export hook so it can be saved "
    "to a local file and edited. Reads settings.export_configs[]: returns the template (joining "
    "file_content_template_multiline with newlines, or the legacy single-string file_content_template), "
    "its export_reference_key, and content_encoding. If the hook has several export_configs and no key "
    "is given, it lists the available keys so you can pick one. Errors clearly if the hook has no "
    "export_configs (e.g. it is a Request Processor, not Custom Format Templating). Read-only.",
    {
        "type": "object",
        "required": ["hook_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The export hook ID. Use rossum_list_hooks / rossum_get_hook to find it.",
            },
            "export_reference_key": {
                "type": "string",
                "description": (
                    "Which export_config to extract, by its export_reference_key. "
                    "Optional when the hook has exactly one export_config; required when it has several."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_extract_export_template(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    hook_id = arguments["hook_id"]
    hook = _http_request(request_id, f"{base_url}/api/v1/hooks/{hook_id}")
    if hook is None:
        return
    configs = (hook.get("settings") or {}).get("export_configs")
    if not configs:
        tool_result(
            request_id,
            f"Hook {hook_id} ('{hook.get('name', '')}') has no settings.export_configs — it is not a "
            "Custom Format Templating export hook. If its settings contain 'stages'/'requests'/'call_api', "
            "it is a Request Processor; use the export-pipeline-reference skill instead.",
            is_error=True,
        )
        return
    key = arguments.get("export_reference_key")
    if key is None:
        if len(configs) == 1:
            cfg = configs[0]
        else:
            keys = [c.get("export_reference_key", f"<index {i}>") for i, c in enumerate(configs)]
            tool_result(
                request_id,
                "Hook has multiple export_configs. Re-call with export_reference_key set to one of: "
                + ", ".join(repr(k) for k in keys),
                is_error=True,
            )
            return
    else:
        matches = [c for c in configs if c.get("export_reference_key") == key]
        if not matches:
            keys = [c.get("export_reference_key") for c in configs]
            tool_result(
                request_id,
                f"No export_config with export_reference_key={key!r}. Available keys: "
                + ", ".join(repr(k) for k in keys),
                is_error=True,
            )
            return
        cfg = matches[0]
    if "file_content_template_multiline" in cfg:
        template = "\n".join(cfg["file_content_template_multiline"])
        source_field = "file_content_template_multiline"
    elif "file_content_template" in cfg:
        template = cfg["file_content_template"]
        source_field = "file_content_template"
    else:
        tool_result(
            request_id,
            f"export_config {cfg.get('export_reference_key')!r} has neither file_content_template_multiline "
            "nor file_content_template — no template content to extract.",
            is_error=True,
        )
        return
    out = {
        "hook_id": hook_id,
        "base_url": base_url,
        "export_reference_key": cfg.get("export_reference_key"),
        "content_encoding": cfg.get("content_encoding"),
        "source_field": source_field,
        "line_count": template.count("\n") + 1,
        "template": template,
    }
    tool_result(request_id, json.dumps(out, indent=2))


@_tool(
    "rossum_generate_export_settings",
    "Turns a local Jinja2 export template back into the settings.export_configs JSON block that a Custom "
    "Format Templating hook expects. Splits the template into file_content_template_multiline (one array "
    "entry per line) and wraps it with the given export_reference_key and content_encoding. Provide the "
    "template via template_text or template_path. Returns the JSON block ready to merge into a hook's "
    "settings — it does NOT push anything; apply it with rossum_patch_hook (or prd2 push) after review. "
    "Pure local transform, no API call.",
    {
        "type": "object",
        "required": ["export_reference_key"],
        "properties": {
            "template_text": {
                "type": "string",
                "description": "The template content as a single string (newline-separated). Use this OR template_path.",
            },
            "template_path": {
                "type": "string",
                "description": "Path to a local template file to read. Use this OR template_text.",
            },
            "export_reference_key": {
                "type": "string",
                "description": "The export_reference_key naming this output (must match what the downstream integration expects).",
            },
            "content_encoding": {
                "type": "string",
                "description": "Output encoding (default 'utf-8'). Preserve the hook's existing value unless changing it deliberately.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_generate_export_settings(request_id, arguments):
    template_text = arguments.get("template_text")
    template_path = arguments.get("template_path")
    if template_text is None and template_path is None:
        tool_result(request_id, "Provide either template_text or template_path.", is_error=True)
        return
    if template_text is None:
        try:
            with open(template_path, encoding="utf-8") as fh:
                template_text = fh.read()
        except OSError as exc:
            tool_result(request_id, f"Could not read template_path {template_path!r}: {exc}", is_error=True)
            return
    lines = _template_text_to_multiline(template_text)
    block = {
        "export_configs": [
            {
                "export_reference_key": arguments["export_reference_key"],
                "content_encoding": arguments.get("content_encoding", "utf-8"),
                "file_content_template_multiline": lines,
            }
        ]
    }
    tool_result(request_id, json.dumps(block, indent=2))


@_tool(
    "rossum_generate_export_payload",
    "Generates the export payload an annotation would produce for a Custom Format Templating hook, via "
    "POST /hooks/{id}/generate_payload simulating the export event. Returns the payload JSON (with the "
    "'annotation' content tree) that the render-export-template skill feeds to the local render script to "
    "faithfully preview the template's output. Non-mutating — it only generates a payload, it does not "
    "export or change the annotation. SECURITY: the returned payload includes the hook's "
    "rossum_authorization_token and secrets — treat it as a credential. Write it to a temp file, never "
    "echo it into the conversation, and delete it after rendering.",
    {
        "type": "object",
        "required": ["hook_id", "annotation_id"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The Custom Format Templating export hook ID.",
            },
            "annotation_id": {
                "type": "integer",
                "description": "The annotation to build the export payload from (use a confirmed/exported sandbox annotation).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_generate_export_payload(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    hook_id = arguments["hook_id"]
    annotation_id = arguments["annotation_id"]
    body = {
        "event": "annotation_content",
        "action": "export",
        "annotation": f"{base_url}/api/v1/annotations/{annotation_id}",
        "previous_status": "confirmed",
        "status": "exporting",
    }
    _rossum_post(request_id, f"/api/v1/hooks/{hook_id}/generate_payload", body)


_ANNOTATION_HOOK_EVENTS = {"annotation_content", "annotation_status"}


def _resolve_annotation_url_for_hook(request_id, base_url, hook_id):
    """Find an annotation URL on one of the hook's queues for payload generation.

    Returns the annotation URL, "" if the hook has no usable annotation (caller
    should surface a guidance error), or None if a request failed (error already sent).
    """
    hook = _http_request(request_id, f"{base_url}/api/v1/hooks/{hook_id}")
    if hook is None:
        return None
    for queue_url in hook.get("queues") or []:
        params = urlencode([
            ("queue", _url_to_id(queue_url)),
            ("page_size", 1),
            ("status", "to_review,confirmed,exported,importing"),
        ])
        listing = _http_request(request_id, f"{base_url}/api/v1/annotations?{params}")
        if listing is None:
            return None
        results = listing.get("results") or []
        if results:
            return results[0].get("url")
    return ""


@_tool(
    "rossum_test_hook",
    "Tests a single hook in isolation: auto-generates a realistic payload for the given event/action "
    "and executes the hook against it — without uploading a document, running the full hook chain, or "
    "mutating any annotation (it is a dry-run). Optionally override the hook's config (e.g. pass "
    "config.code) to try unsaved changes. Returns the hook's response: messages, operations, automation "
    "blockers, or the error/traceback if it raised. The rule analog is the validation pipeline; for an "
    "end-to-end re-fire against a real annotation use rossum_refire_annotation instead. This executes "
    "hook code, which can have side effects (webhook hooks POST externally, function hooks call APIs via "
    "their token_owner), so it is a write operation.",
    {
        "type": "object",
        "required": ["hook_id", "event", "action"],
        "properties": {
            "hook_id": {
                "type": "integer",
                "description": "The hook ID to test.",
            },
            "event": {
                "type": "string",
                "description": "Event type: 'annotation_content', 'annotation_status', 'email', 'invocation', or 'upload'.",
            },
            "action": {
                "type": "string",
                "description": (
                    "Action for the event. Valid event.action pairs: "
                    "annotation_content.{initialize, started, updated, user_update, confirm, export}, "
                    "annotation_status.changed, upload.created, email.received, "
                    "invocation.{manual, scheduled, interface}."
                ),
            },
            "annotation_id": {
                "type": "integer",
                "description": (
                    "Annotation used to build the payload (for annotation_content / annotation_status "
                    "events). If omitted for those events, an annotation is auto-resolved from the hook's "
                    "queues; pass it explicitly (see rossum_list_annotations) to target a specific document."
                ),
            },
            "status": {
                "type": "string",
                "description": "Annotation status in the generated payload (annotation events; default 'to_review').",
            },
            "previous_status": {
                "type": "string",
                "description": "Previous status in the generated payload (annotation events; default 'importing').",
            },
            "config": {
                "type": "object",
                "description": (
                    "Override the hook's config for this test run only (not persisted) — e.g. "
                    "{\"code\": \"def rossum_hook_request_handler(payload):\\n    ...\"} to dry-run unsaved code."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_test_hook(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    hook_id = arguments["hook_id"]
    gen_body = {"event": arguments["event"], "action": arguments["action"]}
    if arguments["event"] in _ANNOTATION_HOOK_EVENTS:
        annotation_id = arguments.get("annotation_id")
        if annotation_id is not None:
            annotation_url = f"{base_url}/api/v1/annotations/{annotation_id}"
        else:
            annotation_url = _resolve_annotation_url_for_hook(request_id, base_url, hook_id)
            if annotation_url is None:
                return
            if not annotation_url:
                return tool_result(
                    request_id,
                    f"Event '{arguments['event']}' needs an annotation, but none were found on hook "
                    f"{hook_id}'s queues. Pass annotation_id explicitly (find one via "
                    "rossum_list_annotations) or upload a document to one of its queues first.",
                    is_error=True,
                )
        gen_body["annotation"] = annotation_url
        gen_body["status"] = arguments.get("status", "to_review")
        gen_body["previous_status"] = arguments.get("previous_status", "importing")

    payload = _http_request(
        request_id, f"{base_url}/api/v1/hooks/{hook_id}/generate_payload",
        method="POST", body=gen_body,
    )
    if payload is None:
        return

    test_body = {"payload": payload}
    if "config" in arguments:
        test_body["config"] = arguments["config"]
    result = _http_request(
        request_id, f"{base_url}/api/v1/hooks/{hook_id}/test",
        method="POST", body=test_body,
    )
    if result is not None:
        tool_result(request_id, json.dumps(result, indent=2))


@_tool(
    "rossum_list_rules",
    "Lists Rossum business rules (/v1/rules). A rule evaluates a boolean trigger_condition (a Rossum "
    "formula) at validation time and, when True, emits actions such as automation blockers, messages, "
    "or field show/hide toggles. Filter by queue to see only rules attached to a specific queue.",
    {
        "type": "object",
        "properties": {
            "queue": {
                "type": "integer",
                "description": "Filter by queue ID — return only rules attached to this queue.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_rules(request_id, arguments):
    params = [("page_size", 100)]
    if "queue" in arguments:
        params.append(("queue", arguments["queue"]))
    _rossum_list(request_id, "/api/v1/rules", params, pick_fields=_RULE_FIELDS)


@_tool(
    "rossum_get_rule",
    "Retrieves full details of a single Rossum business rule including its trigger_condition, "
    "actions, and attached queues. Use rossum_list_rules first to find rule IDs.",
    {
        "type": "object",
        "required": ["rule_id"],
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "The rule ID.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_rule(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/rules/{arguments['rule_id']}")


@_tool(
    "rossum_create_rule",
    "Creates a new Rossum business rule (/v1/rules). The rule evaluates trigger_condition (a Rossum "
    "formula — a boolean Python expression) at validation time and, when it is True, emits its actions "
    "(e.g. add_automation_blocker, show_message, show_hide_field). This is a write operation.",
    {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Display name for the rule.",
            },
            "description": {
                "type": "string",
                "maxLength": 255,
                "description": "Free-text description of what the rule does and why (max 255 characters).",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether the rule is enabled (default: true).",
            },
            "trigger_condition": {
                "type": "string",
                "description": (
                    "A Rossum formula — a boolean Python expression evaluated at validation time. "
                    "The rule fires (emits its actions) when this evaluates to True, e.g. "
                    "\"not is_empty(field.duplicate_order_match)\"."
                ),
            },
            "actions": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Actions emitted when the rule fires. Each action is "
                    "{\"id\": <unique non-empty string>, \"enabled\": true, \"type\": <action type>, "
                    "\"event\": \"validation\", \"payload\": {...}}. type is one of add_automation_blocker, "
                    "show_message, show_hide_field. payload for show_message / add_automation_blocker: "
                    "{\"content\": <text>, \"schema_id\": <field id>}; for show_hide_field: "
                    "{\"schema_ids\": [<field id>, ...]} (fields are shown when the rule fires, hidden otherwise)."
                ),
            },
            "queue_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Queue IDs to attach this rule to. Omit to create unattached.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_create_rule(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    body = {
        "name": arguments["name"],
        "enabled": arguments.get("enabled", True),
        "queues": [f"{base_url}/api/v1/queues/{qid}" for qid in arguments.get("queue_ids", [])],
    }
    for key in ("description", "trigger_condition", "actions"):
        if key in arguments:
            body[key] = arguments[key]
    _rossum_post(request_id, "/api/v1/rules", body)


@_tool(
    "rossum_patch_rule",
    "Updates an existing Rossum business rule. Only provide the fields you want to change — "
    "unspecified fields are left untouched. Use this to edit the trigger_condition, toggle the "
    "enabled state, replace actions, or reassign queues without recreating the rule. "
    "This is a write operation.",
    {
        "type": "object",
        "required": ["rule_id"],
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "The rule ID to update.",
            },
            "name": {
                "type": "string",
                "description": "New display name.",
            },
            "description": {
                "type": "string",
                "maxLength": 255,
                "description": "New description (max 255 characters).",
            },
            "enabled": {
                "type": "boolean",
                "description": "Enable or disable the rule.",
            },
            "trigger_condition": {
                "type": "string",
                "description": "Updated trigger_condition (Rossum formula — boolean Python expression; fires when True).",
            },
            "actions": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Replace the full actions list (see rossum_create_rule for the action shape).",
            },
            "queue_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Replace attached queues (full list, not additive).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_patch_rule(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    rule_id = arguments["rule_id"]
    body = {}
    for key in ("name", "description", "enabled", "trigger_condition", "actions"):
        if key in arguments:
            body[key] = arguments[key]
    if "queue_ids" in arguments:
        body["queues"] = [f"{base_url}/api/v1/queues/{qid}" for qid in arguments["queue_ids"]]
    _rossum_patch(request_id, f"/api/v1/rules/{rule_id}", body)


@_tool(
    "rossum_delete_rule",
    "Deletes a Rossum business rule from the organization. "
    "This is a destructive operation that cannot be undone.",
    {
        "type": "object",
        "required": ["rule_id"],
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "The rule ID to delete.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_DESTRUCTIVE,
)
def handle_delete_rule(request_id, arguments):
    _rossum_delete(request_id, f"/api/v1/rules/{arguments['rule_id']}")


@_tool(
    "rossum_list_rule_execution_logs",
    "Lists rule execution logs (/v1/rules_execution_logs) — the per-evaluation record of when "
    "business rules ran, whether their trigger_condition fired, and any errors. The rule analog of "
    "rossum_list_hook_logs; use it to debug why a rule did or did not fire on a given annotation. "
    "Filter by rule, queue, annotation, trigger event, execution result, or time range. Compacted to "
    "{rule_id, rule_name, queue_id, annotation_id, trigger_event, execution_result, execution_error, "
    "created_at, request_id} — call without a pick to inspect full trigger_condition_values/actions via the API.",
    {
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "Filter by rule ID.",
            },
            "queue_id": {
                "type": "integer",
                "description": "Filter by queue ID.",
            },
            "annotation_id": {
                "type": "integer",
                "description": "Filter by annotation ID.",
            },
            "trigger_event": {
                "type": "string",
                "description": "Filter by trigger event (e.g. 'validation').",
            },
            "execution_result": {
                "type": "string",
                "description": "Filter by outcome: 'success', 'failure', or 'partial_success'.",
            },
            "created_at_after": {
                "type": "string",
                "description": "Only logs created at or after this ISO 8601 timestamp (e.g. '2026-01-15T00:00:00Z').",
            },
            "created_at_before": {
                "type": "string",
                "description": "Only logs created at or before this ISO 8601 timestamp.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return (default: 20, max: 200). This endpoint is high-volume — filter by rule/queue/annotation rather than raising this.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_list_rule_execution_logs(request_id, arguments):
    max_results = min(arguments.get("max_results", 20), 200)
    params = [("page_size", min(max_results, 100))]
    for arg_key, query_key in (
        ("rule_id", "rule"),
        ("queue_id", "queue"),
        ("annotation_id", "annotation"),
        ("trigger_event", "trigger_event"),
        ("execution_result", "execution_result"),
        ("created_at_after", "created_at_after"),
        ("created_at_before", "created_at_before"),
    ):
        if arg_key in arguments:
            params.append((query_key, arguments[arg_key]))
    _rossum_list(
        request_id, "/api/v1/rules_execution_logs", params,
        max_results=max_results, pick_fields=_RULE_EXEC_LOG_FIELDS,
    )


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
    "rossum_get_annotation_meta",
    "Retrieves the raw annotation metadata only (status, timestamps, queue/document/modifier "
    "URLs, automation_blocker URL, hook-state metadata, email info). Does NOT include extracted "
    "content or the resolved blocker items. Use this when you specifically need the unprojected "
    "annotation resource. For the common 'tell me everything useful about annotation X' case, "
    "use rossum_get_annotation — it returns a compact view that merges metadata + content + "
    "blocker items + recent hook logs in one call.",
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
def handle_get_annotation_meta(request_id, arguments):
    _rossum_get(request_id, f"/api/v1/annotations/{arguments['annotation_id']}")


@_tool(
    "rossum_get_annotation",
    "Compact merged view of an annotation: metadata + extracted fields + tables + resolved "
    "automation_blocker items + recent hook logs, all in one response. Drops noisy fields "
    "(connector/ocr positions, time_spent, redundant URLs, raw confidence text) by default to "
    "keep the payload small. The full raw payload is written to "
    ".rossum-cache/annotations/<annotation_id>.json under the current working directory — "
    "Read that file if you need positions, OCR coordinates, raw RIR text, or any field this "
    "view drops. Use this as the default way to inspect an annotation; fall back to "
    "rossum_get_annotation_meta / rossum_get_annotation_content for unprojected access.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID.",
            },
            "view": {
                "type": "string",
                "enum": ["compact", "verbose"],
                "description": (
                    "compact (default): value, ocr (when distinct), normalized, src, score. "
                    "verbose: also includes page, position, options for enum fields."
                ),
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of schema_ids to include. Filters both top-level fields "
                    "and row cells inside tables. Useful when you only care about a few fields."
                ),
            },
            "hook_logs": {
                "type": "integer",
                "description": (
                    "Number of most-recent hook log entries to include (default 10, max 50). "
                    "Pass 0 to skip the hook-log fetch entirely for a faster call."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_READ_ONLY,
)
def handle_get_annotation(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    view = arguments.get("view", "compact")
    fields = arguments.get("fields")
    hook_logs_n = arguments.get("hook_logs", 10)
    if hook_logs_n is None:
        hook_logs_n = 10
    hook_logs_n = max(0, min(int(hook_logs_n), 50))

    # 1. annotation metadata
    annotation = _http_request(request_id, f"{base_url}/api/v1/annotations/{annotation_id}")
    if annotation is None:
        return

    # 2. content tree
    content_resp = _http_request(
        request_id, f"{base_url}/api/v1/annotations/{annotation_id}/content"
    )
    if content_resp is None:
        return
    content_tree = (
        content_resp.get("results")
        if isinstance(content_resp, dict) and "results" in content_resp
        else content_resp
    )
    if not isinstance(content_tree, list):
        content_tree = []

    # 3. blocker (optional — only if URL present)
    blocker_payload = None
    blocker_url = annotation.get("automation_blocker")
    if blocker_url:
        blocker_payload = _http_request(request_id, blocker_url)
        if blocker_payload is None:
            # Error already sent. Bail out silently — caller saw the HTTP error.
            return

    # 4. recent hook logs (optional)
    hook_log_entries = []
    if hook_logs_n > 0:
        params = urlencode([
            ("annotation", annotation_id),
            ("page_size", hook_logs_n),
            ("ordering", "-timestamp"),
        ])
        hook_logs_resp = _http_request(
            request_id, f"{base_url}/api/v1/hooks/logs?{params}"
        )
        if hook_logs_resp is None:
            return
        hook_log_entries = (
            hook_logs_resp.get("results", []) if isinstance(hook_logs_resp, dict) else []
        )

    # 5. assemble compact response
    compact = _build_annotation_compact_response(
        annotation, content_tree, blocker_payload, hook_log_entries,
        view=view, fields=fields,
    )

    # 6. cache the raw merged payload for "give me everything" follow-ups
    raw_payload = {
        "annotation": annotation,
        "content": content_tree,
        "automation_blocker": blocker_payload,
        "hook_logs": hook_log_entries,
    }
    cache_path = _cache_full_payload(annotation_id, raw_payload)
    compact["_meta"] = {
        "view": view,
        "fields_filter": fields,
        "hook_logs_returned": len(hook_log_entries),
        "full_payload_cache": cache_path,
        "hint": (
            "Compact view — value/ocr/normalized/src/score per field. For positions, OCR "
            "coordinates, raw RIR text, full hook payloads, or anything else: Read the file "
            "at full_payload_cache (it has annotation + content + automation_blocker + "
            "hook_logs merged), or call this tool again with view=\"verbose\" / "
            "rossum_get_annotation_content for the raw content tree."
            if cache_path else
            "Compact view. Cache write failed (no writable CWD). For more detail call this "
            "tool with view=\"verbose\" or rossum_get_annotation_content."
        ),
    }
    tool_result(request_id, json.dumps(compact, indent=2))


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
    "rossum_start_annotation",
    "Starts a review session on an annotation — transitions to 'reviewing' status and "
    "locks the annotation to the calling user. Fires 'annotation_content.started' hook events. "
    "Use as part of an inner-loop iteration; remember to call rossum_cancel_annotation "
    "afterwards to release the lock (otherwise no other user can edit the annotation). "
    "For the typical 'start → validate → cancel' soft re-fire, prefer rossum_refire_annotation "
    "with mode='validate' — it handles cancel-in-finally automatically.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID to start.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_start_annotation(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    status_code = _http_request(
        request_id, f"{base_url}/api/v1/annotations/{annotation_id}/start",
        method="POST", parse_json=False,
    )
    if status_code is None:
        return
    if 200 <= status_code < 300:
        tool_result(
            request_id,
            f"Annotation {annotation_id} started — status now 'reviewing', locked to caller. "
            f"Remember to call rossum_cancel_annotation to release the lock.",
        )
    else:
        tool_result(request_id, f"Start returned HTTP {status_code}.", is_error=True)


@_tool(
    "rossum_cancel_annotation",
    "Cancels a review session on an annotation — releases the 'reviewing' lock and returns "
    "the annotation to 'to_review' status. Mandatory after rossum_start_annotation, even on "
    "the error path. Tolerates HTTP 409 (already not in reviewing) silently.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID to cancel.",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_cancel_annotation(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    # Use silent path so 409 (not in reviewing) does not surface as an error.
    status_code = _http_request_silent(
        f"{base_url}/api/v1/annotations/{annotation_id}/cancel", method="POST",
    )
    if status_code is None:
        tool_result(request_id, "Cancel failed (network/SSL error).", is_error=True)
        return
    if status_code == 409:
        tool_result(request_id, f"Annotation {annotation_id} was not in reviewing — nothing to cancel (HTTP 409).")
        return
    if not (200 <= status_code < 300):
        tool_result(request_id, f"Cancel returned HTTP {status_code}.", is_error=True)
        return
    tool_result(request_id, f"Annotation {annotation_id} cancelled (HTTP {status_code}).")


@_tool(
    "rossum_confirm_annotation",
    "Confirms an annotation via POST /annotations/{id}/confirm — transitions it to "
    "'exported'/'exporting' (or 'confirmed' if the confirmed-state feature is enabled, "
    "or 'in_workflow' if an approval workflow is configured on the queue) and FIRES THE "
    "DOWNSTREAM EXPORT / approval routing. This is a real, not-easily-reversible side "
    "effect: use it to complete the validation→export path or to bulk-confirm. This is the "
    "correct way to confirm correct data — do not patch the status directly. "
    "PRECONDITION: the annotation must be in 'reviewing' (i.e. started) — confirming a "
    "'to_review' annotation returns HTTP 409 ('Document is not being annotated'). Call "
    "rossum_start_annotation first. "
    "Optionally set skip_workflows to bypass approval workflows (requires "
    "bypass_workflows_allowed in the queue settings).",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID to confirm.",
            },
            "skip_workflows": {
                "type": "boolean",
                "description": (
                    "Skip approval-workflow evaluation (default false). Only effective "
                    "when bypass_workflows_allowed is set in the queue's workflow settings."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_confirm_annotation(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    body = None
    if "skip_workflows" in arguments:
        body = {"skip_workflows": arguments["skip_workflows"]}
    # 204 No Content on success -> parse_json=False returns the status code.
    status_code = _http_request(
        request_id, f"{base_url}/api/v1/annotations/{annotation_id}/confirm",
        method="POST", body=body, parse_json=False,
    )
    if status_code is None:
        return
    if 200 <= status_code < 300:
        tool_result(
            request_id,
            f"Annotation {annotation_id} confirmed (HTTP {status_code}) — status now "
            f"'exported'/'exporting' (or 'confirmed' if the confirmed-state feature is on, "
            f"or 'in_workflow' if an approval workflow is configured). The downstream "
            f"export / approval routing has been fired; this is not easily reversible.",
        )
    else:
        # 409 'Document is not being annotated' means it was not in 'reviewing' —
        # start it first (rossum_start_annotation) before confirming.
        tool_result(request_id, f"Confirm returned HTTP {status_code}.", is_error=True)


@_tool(
    "rossum_validate_content",
    "Fires the hook chain against an annotation by POSTing to /content/validate with the "
    "specified actions. Returns the freshly computed datapoints projected to the same compact "
    "shape as rossum_get_annotation (value, ocr, normalized, src, score). This does NOT take "
    "or release a reviewing lock — the annotation must already be in a valid state for "
    "validation. For the typical iterate-on-deliverable flow, prefer rossum_refire_annotation "
    "with mode='validate' — it wraps start/validate/cancel correctly.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation ID.",
            },
            "actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Hook actions to fire. Common values: 'user_update' (rules and field-update "
                    "hooks), 'started' (lazy-lookup hooks). Default ['user_update', 'started']."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_validate_content(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    actions = arguments.get("actions") or ["user_update", "started"]
    resp = _http_request(
        request_id,
        f"{base_url}/api/v1/annotations/{annotation_id}/content/validate",
        method="POST", body={"actions": actions},
    )
    if resp is None:
        return
    updated = resp.get("updated_datapoints", []) if isinstance(resp, dict) else []
    projected = {}
    for node in updated:
        sid = node.get("schema_id")
        if sid:
            projected[sid] = _compact_datapoint(node)
    result = {
        "annotation_id": annotation_id,
        "actions": actions,
        "updated_datapoints_count": len(updated),
        "updated_datapoints": projected,
        "raw_messages": resp.get("messages", []) if isinstance(resp, dict) else [],
    }
    tool_result(request_id, json.dumps(result, indent=2))


@_tool(
    "rossum_update_annotation_content",
    "Writes extracted field values onto an annotation via the bulk content-operations endpoint "
    "(POST /annotations/{id}/content/operations). Self-managing: it starts the annotation (locking "
    "it), applies the operations, then releases the lock in a finally block — the edits persist and "
    "the status returns to to_review. Each operation targets a DATAPOINT ID from the content tree "
    "(find them via rossum_get_annotation_content or rossum_get_annotation), NOT a schema_id. "
    "Operation shapes: replace a value — "
    "{\"op\": \"replace\", \"id\": <datapoint_id>, \"value\": {\"content\": {\"value\": \"<new>\"}}}; "
    "add a table row — {\"op\": \"add\", \"id\": <multivalue_id>, "
    "\"value\": [{\"schema_id\": \"<col>\", \"content\": {\"value\": \"<v>\"}}]}; "
    "remove a table row — {\"op\": \"remove\", \"id\": <row_datapoint_id>}. "
    "Section, multivalue and tuple containers cannot be replaced; only multivalue children can be "
    "removed. This writes real data to the annotation, so it is a write operation.",
    {
        "type": "object",
        "required": ["annotation_id", "operations"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The annotation to edit. Must be in a startable state (e.g. to_review).",
            },
            "operations": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Content operations to apply in one call, each "
                    "{\"op\": \"replace|add|remove\", \"id\": <datapoint_id>, \"value\": {...}} — see the "
                    "tool description for the per-op shape. IDs are datapoint IDs from the content tree, "
                    "not schema_ids."
                ),
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_update_annotation_content(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    operations = arguments["operations"]
    # start — locks the annotation for editing (returns 204 No Content)
    start_status = _http_request(
        request_id, f"{base_url}/api/v1/annotations/{annotation_id}/start",
        method="POST", parse_json=False,
    )
    if start_status is None or not (200 <= start_status < 300):
        if start_status is not None:
            tool_result(
                request_id,
                f"Start returned HTTP {start_status} — the annotation may not be in a startable "
                "state (e.g. already confirmed/exported, or locked by another user).",
                is_error=True,
            )
        return
    result = None
    try:
        result = _http_request(
            request_id, f"{base_url}/api/v1/annotations/{annotation_id}/content/operations",
            method="POST", body={"operations": operations},
        )
    finally:
        # release the review lock; tolerate 409 if no longer in reviewing
        _http_request_silent(
            f"{base_url}/api/v1/annotations/{annotation_id}/cancel", method="POST",
        )
    if result is None:
        return
    tool_result(request_id, json.dumps({
        "annotation_id": annotation_id,
        "operations_applied": len(operations),
        "result": result,
    }, indent=2))


@_tool(
    "rossum_refire_annotation",
    "Re-fire an annotation through the hook chain — the main inner-loop iteration primitive. "
    "Three modes:\n"
    "  - 'validate' (default, fastest): start → content/validate(actions) → cancel-in-finally. "
    "Fires hooks listening on 'user_update' and 'started' actions. Returns the resulting "
    "compact annotation view (same shape as rossum_get_annotation).\n"
    "  - 'toggle': PATCH status postponed → to_review → wait → read. Fires "
    "'annotation_content.started' plus any status-listening hooks. Slower; use when soft "
    "validate is not enough.\n"
    "  - 'reupload': fetch source PDF → upload to same queue → poll past 'importing' → "
    "auto-restore if the new annotation lands in 'deleted' (defensive: handles customer-custom "
    "dedup hooks that PATCH status:deleted on initialize. The stock Rossum Duplicate Handling "
    "extension only flags duplicates, it does not transition status — so this branch typically "
    "does not fire on stock setups). Use when iterating on initialize hooks or OCR-adjacent "
    "logic. Produces a NEW annotation ID (returned in response).\n"
    "Always returns the compact merged view (metadata + fields + tables + blocker + recent "
    "hook logs) plus a _refire section describing what was done. Raw payload cached to "
    ".rossum-cache/annotations/<aid>.json.",
    {
        "type": "object",
        "required": ["annotation_id"],
        "properties": {
            "annotation_id": {
                "type": "integer",
                "description": "The source annotation ID to re-fire.",
            },
            "mode": {
                "type": "string",
                "enum": ["validate", "toggle", "reupload"],
                "description": "Re-fire pattern (default: 'validate').",
            },
            "actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "validate mode only — hook actions to fire. "
                    "Default ['user_update', 'started']."
                ),
            },
            "wait_seconds": {
                "type": "integer",
                "description": "toggle mode only — seconds to wait between to_review and read-back (default 15).",
            },
            "poll_timeout": {
                "type": "integer",
                "description": "reupload mode only — max seconds to wait for the new annotation to leave 'importing' (default 180).",
            },
            "view": {
                "type": "string",
                "enum": ["compact", "verbose"],
                "description": "Projection of the final response (default 'compact').",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional schema_id filter applied to the final compact view.",
            },
            "hook_logs": {
                "type": "integer",
                "description": "Number of recent hook log entries in the final response (default 10, max 50, 0 to skip).",
            },
        },
        "additionalProperties": False,
    },
    annotations=_WRITE,
)
def handle_refire_annotation(request_id, arguments):
    base_url, _ = _ensure_connection(request_id)
    if not base_url:
        return
    annotation_id = arguments["annotation_id"]
    mode = arguments.get("mode", "validate")
    view = arguments.get("view", "compact")
    fields = arguments.get("fields")
    hook_logs_n = arguments.get("hook_logs", 10)
    if hook_logs_n is None:
        hook_logs_n = 10
    hook_logs_n = max(0, min(int(hook_logs_n), 50))

    refire_meta = {"mode": mode, "source_annotation_id": annotation_id}
    target_aid = annotation_id

    if mode == "validate":
        actions = arguments.get("actions") or ["user_update", "started"]
        # start (returns 204 No Content)
        start_status = _http_request(
            request_id, f"{base_url}/api/v1/annotations/{annotation_id}/start",
            method="POST", parse_json=False,
        )
        if start_status is None or not (200 <= start_status < 300):
            if start_status is not None:
                tool_result(request_id, f"Start returned HTTP {start_status}.", is_error=True)
            return
        try:
            # validate
            validate_resp = _http_request(
                request_id,
                f"{base_url}/api/v1/annotations/{annotation_id}/content/validate",
                method="POST", body={"actions": actions},
            )
            if validate_resp is None:
                return
            refire_meta["actions"] = actions
            updated = validate_resp.get("updated_datapoints", []) if isinstance(validate_resp, dict) else []
            refire_meta["updated_datapoints_count"] = len(updated)
        finally:
            # cancel — silent, tolerate 409 (already not in reviewing)
            _http_request_silent(
                f"{base_url}/api/v1/annotations/{annotation_id}/cancel", method="POST",
            )

    elif mode == "toggle":
        wait_seconds = int(arguments.get("wait_seconds", 15))
        for next_status in ("postponed", "to_review"):
            patch_resp = _http_request(
                request_id, f"{base_url}/api/v1/annotations/{annotation_id}",
                method="PATCH", body={"status": next_status},
            )
            if patch_resp is None:
                return
        refire_meta["wait_seconds"] = wait_seconds
        time.sleep(wait_seconds)

    elif mode == "reupload":
        poll_timeout = int(arguments.get("poll_timeout", 180))
        # 1. fetch source annotation
        source_ann = _http_request(
            request_id, f"{base_url}/api/v1/annotations/{annotation_id}",
        )
        if source_ann is None:
            return
        document_url = source_ann.get("document")
        queue_url = source_ann.get("queue")
        if not document_url or not queue_url:
            tool_result(request_id, "Source annotation missing document or queue URL.", is_error=True)
            return
        # 2. document → content URL + filename
        document = _http_request(request_id, document_url)
        if document is None:
            return
        content_url = document.get("content")
        original_name = document.get("original_file_name") or f"refire_{annotation_id}.pdf"
        if not content_url:
            tool_result(request_id, "Document missing content URL.", is_error=True)
            return
        # 3. download PDF bytes (raw, no JSON parse)
        pdf_bytes = _http_get_bytes(request_id, content_url)
        if pdf_bytes is None:
            return
        # 4. multipart upload
        queue_id = queue_url.rstrip("/").rsplit("/", 1)[-1]
        upload_url = f"{base_url}/api/v1/queues/{queue_id}/upload"
        boundary = f"----refire-{int(time.time())}"
        pre = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"; filename="{original_name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        post = f"\r\n--{boundary}--\r\n".encode()
        body = pre + pdf_bytes + post
        upload_resp = _http_request_raw(
            request_id, upload_url, method="POST", raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        if upload_resp is None:
            return
        # Rossum's upload response shape: {"annotation": "<URL>", "document": "<URL>",
        # "results": [{"annotation": "<URL>", "document": "<URL>"}]}.
        # Older shapes may use "annotations" (plural list). Try in priority order.
        new_url = None
        if isinstance(upload_resp, dict):
            top_ann = upload_resp.get("annotation")
            if isinstance(top_ann, str):
                new_url = top_ann
            if not new_url:
                results = upload_resp.get("results") or upload_resp.get("annotations") or []
                if results:
                    first = results[0]
                    if isinstance(first, str):
                        new_url = first
                    elif isinstance(first, dict):
                        new_url = first.get("annotation") or first.get("url")
        if not new_url:
            tool_result(
                request_id,
                f"Upload response missing annotation URL: {upload_resp!r}",
                is_error=True,
            )
            return
        target_aid = int(new_url.rstrip("/").rsplit("/", 1)[-1])
        refire_meta["target_annotation_id"] = target_aid
        # 5. poll past 'importing'
        deadline = time.time() + poll_timeout
        new_ann = {}
        while time.time() < deadline:
            new_ann = _http_request(request_id, f"{base_url}/api/v1/annotations/{target_aid}")
            if new_ann is None:
                return
            if new_ann.get("status") not in ("importing", "created"):
                break
            time.sleep(3)
        # 6. Dedup auto-restore — defensive for customer-custom hooks that PATCH
        # status:deleted on annotation_content.initialize. The stock Duplicate
        # Handling extension does not transition status (its valid actions are
        # fill_field, forward_annotation, mark_duplicate, show_message,
        # stop_automation, apply_label), so this branch typically does not fire.
        # When a customer customization does delete, restoring keeps the
        # iteration loop alive.
        if isinstance(new_ann, dict) and new_ann.get("status") == "deleted":
            refire_meta["dedup_restore"] = True
            restore = _http_request(
                request_id, f"{base_url}/api/v1/annotations/{target_aid}",
                method="PATCH", body={"status": "to_review"},
            )
            if restore is None:
                return

    else:
        tool_result(request_id, f"Unknown mode: {mode!r}", is_error=True)
        return

    # Build compact response for the final annotation (same shape as rossum_get_annotation)
    annotation = _http_request(request_id, f"{base_url}/api/v1/annotations/{target_aid}")
    if annotation is None:
        return
    content_resp = _http_request(
        request_id, f"{base_url}/api/v1/annotations/{target_aid}/content"
    )
    if content_resp is None:
        return
    content_tree = (
        content_resp.get("results")
        if isinstance(content_resp, dict) and "results" in content_resp
        else content_resp
    )
    if not isinstance(content_tree, list):
        content_tree = []
    blocker_payload = None
    blocker_url = annotation.get("automation_blocker")
    if blocker_url:
        blocker_payload = _http_request(request_id, blocker_url)
        if blocker_payload is None:
            return
    hook_log_entries = []
    if hook_logs_n > 0:
        params = urlencode([
            ("annotation", target_aid),
            ("page_size", hook_logs_n),
            ("ordering", "-timestamp"),
        ])
        hook_logs_resp = _http_request(
            request_id, f"{base_url}/api/v1/hooks/logs?{params}",
        )
        if hook_logs_resp is None:
            return
        hook_log_entries = (
            hook_logs_resp.get("results", []) if isinstance(hook_logs_resp, dict) else []
        )
    compact = _build_annotation_compact_response(
        annotation, content_tree, blocker_payload, hook_log_entries,
        view=view, fields=fields,
    )
    raw_payload = {
        "annotation": annotation,
        "content": content_tree,
        "automation_blocker": blocker_payload,
        "hook_logs": hook_log_entries,
    }
    cache_path = _cache_full_payload(target_aid, raw_payload)
    compact["_refire"] = refire_meta
    compact["_meta"] = {
        "view": view,
        "fields_filter": fields,
        "hook_logs_returned": len(hook_log_entries),
        "full_payload_cache": cache_path,
        "hint": (
            f"Refire {mode!r} completed. Compact view of target annotation {target_aid}. "
            "For raw payload (positions, OCR coords, full hook logs): Read the file at "
            "full_payload_cache, or call with view='verbose'."
        ),
    }
    tool_result(request_id, json.dumps(compact, indent=2))


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
                    "serverInfo": {"name": "rossum-api", "version": _SERVER_VERSION},
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
                    global _current_tool
                    _current_tool = name
                    try:
                        handler(request_id, params.get("arguments") or {})
                    finally:
                        _current_tool = None
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
