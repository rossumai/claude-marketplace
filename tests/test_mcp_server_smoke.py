"""Smoke test: the MCP server boots and advertises its tools — no token needed.

The static guards check files on disk; this checks that server.py actually
*starts*, speaks the stdio JSON-RPC protocol, and registers its tools.
`initialize`, `tools/list`, and `ping` require no auth, so this runs fully
offline in CI and catches a class of bugs the static guards can't: a server
that crashes on import/startup, broken JSON-RPC framing, or a tool-registration
regression.
"""
from __future__ import annotations

import json
import subprocess
import sys

import repo_lib as R


def _rpc(method: str, id_: int, params=None) -> str:
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _drive_server(requests: list[str]):
    """Feed JSON-RPC requests on stdin, close it, collect responses by id.

    The server loops on stdin and exits when it hits EOF, so writing all the
    requests and letting subprocess close stdin gives a clean shutdown.
    """
    proc = subprocess.run(
        [sys.executable, str(R.SERVER_PY)],
        input="\n".join(requests) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    responses: dict = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # _log writes to stderr, so stdout should be clean, but be safe
        if isinstance(obj, dict) and "id" in obj:
            responses[obj["id"]] = obj
    return responses, proc


def test_server_boots_handshakes_and_lists_tools():
    responses, proc = _drive_server([
        _rpc("initialize", 1, {"capabilities": {}}),
        _rpc("tools/list", 2),
        _rpc("ping", 3),
    ])
    assert proc.returncode == 0, f"server exited {proc.returncode}\nstderr:\n{proc.stderr}"

    # initialize → serverInfo
    init = responses.get(1)
    assert init and "result" in init, f"no initialize response: {responses}"
    info = init["result"]["serverInfo"]
    assert info["name"] == "rossum-api"

    # The version the *running* server reports must match plugin.json — an
    # end-to-end version check through the real entry point.
    plugin_version = json.loads(R.RSA_PLUGIN_JSON.read_text("utf-8"))["version"]
    assert info["version"] == plugin_version, (
        f"server serverInfo version {info['version']!r} != "
        f"plugin.json version {plugin_version!r}"
    )

    # tools/list → every @_tool is advertised with a well-formed definition
    tlist = responses.get(2)
    assert tlist and "result" in tlist, f"no tools/list response: {responses}"
    tools = tlist["result"]["tools"]
    assert len(tools) == R.mcp_tool_count(), (
        f"server advertises {len(tools)} tools but @_tool count is {R.mcp_tool_count()}"
    )
    for t in tools:
        assert t.get("name"), f"tool missing name: {t}"
        assert t.get("description"), f"tool {t.get('name')!r} missing description"
        schema = t.get("inputSchema")
        assert isinstance(schema, dict), f"tool {t.get('name')!r} inputSchema not an object"
        assert schema.get("type") == "object", (
            f"tool {t['name']!r} inputSchema type is {schema.get('type')!r}, expected 'object'"
        )
        assert "properties" in schema, f"tool {t['name']!r} inputSchema missing 'properties'"

    # ping → empty result
    assert responses.get(3, {}).get("result") == {}, "ping should return an empty result"
