"""Optional credentials FILE for recon.py, so secrets never have to go
through a chat with an agent.

Today's baseline (still fully supported, unchanged) is environment
variables: ROSSUM_TOKEN and one or more B2B_API_KEY / B2B_API_KEY_<LABEL>.
That means an operator either exports them by hand or -- in practice, when
an agent is driving -- pastes secrets into the conversation so the agent can
export them. This module adds a second, file-based path: a human runs
`--init-credentials`, fills in the printed path THEMSELVES, and the tool
reads it back. The file's value never has to be typed into a chat.

Resolution order (see recon.py's `_credentials_source_path`): an explicit
`--credentials PATH` wins outright; otherwise the default path is used only
if it already exists; otherwise environment variables, exactly as before
this module existed. Whichever source is selected is used WHOLESALE -- a
credentials file with an unfilled required field is a hard refusal, never a
partial fall-through to the environment (see `load_credentials_file`).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Deliberately OUTSIDE any repository -- ~/.config, not a project directory
# -- so a filled-in file cannot be committed by accident.
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "rossum-b2brouter-recon" / "credentials.json"

# Any field still containing this substring counts as NOT SUPPLIED (see
# `_is_placeholder`). Every placeholder in TEMPLATE below is built with it so
# the two checks can never drift apart.
PLACEHOLDER_MARK = "--PASTE"

# JSON has no comments, so guidance lives in keys beginning with "_" --
# `_strip_underscore_keys` deletes every one of them, recursively, before
# this module reads anything else out of the file. Keep this literally in
# sync with the shape `load_credentials_file` reads below.
TEMPLATE = {
    "_readme": (
        "Fill in the values marked --PASTE...HERE--, then run the "
        "reconciliation. This file holds credentials: keep it outside any "
        "git repository and do not share it. Keys beginning with an "
        "underscore are comments and are ignored."
    ),
    "rossum": {
        "_comment": (
            "An API token for the Rossum organization you are reconciling. "
            "ui_host is the domain your team opens Rossum in; it is only "
            "used to build clickable links and cannot be discovered from "
            "the API."
        ),
        "token": "--PASTE-ROSSUM-API-TOKEN-HERE--",
        "base_url": "https://elis.rossum.ai",
        "ui_host": "--PASTE-ROSSUM-UI-HOST-HERE--",
    },
    "b2brouter": {
        "_comment": (
            "One entry per account group. B2Brouter keys are scoped per "
            "account group, so one key cannot see another group's accounts "
            "-- add as many entries as you have groups. Each key needs "
            "accounts-read and invoices-read ONLY, nothing else. The label "
            "is yours and appears in messages; the key value never does."
        ),
        "keys": {
            "GROUP-1": "--PASTE-B2BROUTER-KEY-FOR-THIS-GROUP-HERE--",
            "GROUP-2": "--PASTE-B2BROUTER-KEY-FOR-THIS-GROUP-HERE--",
        },
    },
}


class CredentialsError(Exception):
    """Any credentials-file problem: missing, malformed, unreadable, or a
    required field left as its --PASTE placeholder.

    Every raise site names the FILE PATH and, where applicable, the FIELD --
    never a value. Callers must map this to the tool's existing
    missing-credentials exit code and print `str(exc)` as-is; they must
    never fall back to environment variables once a file was selected as the
    source (see this module's docstring) -- a half-loaded credentials file
    must never produce a half-run.
    """


@dataclass(frozen=True)
class Credentials:
    """What a credentials file actually supplied, already filtered down to
    real values -- no placeholders, no underscore comment keys.

    `base_url` and `ui_host` are None when the file left them as a
    placeholder (or omitted them): recon.py treats a None here as "no
    opinion from the file", letting its own CLI-flag-then-default cascade
    take over unchanged. `token` is never None -- an unfilled token is a
    CredentialsError, not a value on this object (see `load_credentials_file`).
    """
    token: str
    base_url: str | None
    ui_host: str | None
    keys: dict[str, str]


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and PLACEHOLDER_MARK in value


def _strip_underscore_keys(obj):
    """Drop every dict key beginning with '_', at any depth.

    Recurses into dicts and lists so a comment key nested anywhere in the
    document is ignored, not just at the top level -- "at any level" is the
    explicit contract, and a shallow, top-level-only strip would silently
    let a stray "_note" inside `b2brouter.keys` survive as if it were a real
    account-group label.
    """
    if isinstance(obj, dict):
        return {
            key: _strip_underscore_keys(value)
            for key, value in obj.items()
            if not key.startswith("_")
        }
    if isinstance(obj, list):
        return [_strip_underscore_keys(item) for item in obj]
    return obj


def init_credentials(path: Path) -> None:
    """Write the credentials TEMPLATE to `path` with owner-only (0600)
    permissions, creating parent directories as needed.

    Refuses outright if `path` already exists -- raises CredentialsError
    without touching the file -- so a filled-in file can never be clobbered
    by a stray re-run. Uses O_CREAT|O_EXCL (atomic create-or-fail) rather
    than a separate exists()-then-open check, closing the TOCTOU window
    where a concurrent run could otherwise still overwrite a real file. The
    mode is passed to `os.open` itself (never opened world-readable and
    chmod'd after the fact) and reasserted with `os.chmod` once written, so
    the file is never briefly readable by anyone but its owner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CredentialsError(f"refusing to overwrite existing credentials file: {path}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(TEMPLATE, handle, indent=2)
            handle.write("\n")
        os.chmod(path, 0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def load_credentials_file(path: Path) -> Credentials:
    """Read, parse, and validate a credentials file.

    Missing, unreadable, or invalid-JSON all raise CredentialsError naming
    the path -- never a partial read. `rossum.token` is the only field whose
    absence or placeholder value is a hard failure (named by path AND
    field): `base_url` and `ui_host` are optional overrides recon.py's own
    cascade already has real defaults/CLI flags for. An entry under
    `b2brouter.keys` whose VALUE still contains the placeholder marker is
    silently skipped, not treated as a real key -- so the template's two
    example groups don't become two bogus keys when only one is filled in;
    if every entry is skipped this way, `keys` comes back empty and the
    caller applies the same "no usable key" handling it already has for the
    environment-variable path.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CredentialsError(f"credentials file not found: {path}") from None
    except OSError as exc:
        raise CredentialsError(f"credentials file at {path} could not be read: {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"credentials file at {path} is not valid JSON: {exc}") from exc

    data = _strip_underscore_keys(data)
    if not isinstance(data, dict):
        raise CredentialsError(f"credentials file at {path} must contain a JSON object")

    rossum = data.get("rossum")
    rossum = rossum if isinstance(rossum, dict) else {}
    token = rossum.get("token")
    if not isinstance(token, str) or not token or _is_placeholder(token):
        raise CredentialsError(
            f"credentials file at {path}: field 'rossum.token' is not filled in"
        )

    base_url = rossum.get("base_url")
    if not isinstance(base_url, str) or not base_url or _is_placeholder(base_url):
        base_url = None

    ui_host = rossum.get("ui_host")
    if not isinstance(ui_host, str) or not ui_host or _is_placeholder(ui_host):
        ui_host = None

    b2brouter = data.get("b2brouter")
    b2brouter = b2brouter if isinstance(b2brouter, dict) else {}
    raw_keys = b2brouter.get("keys")
    raw_keys = raw_keys if isinstance(raw_keys, dict) else {}
    keys = {
        label: value
        for label, value in raw_keys.items()
        if isinstance(value, str) and value and not _is_placeholder(value)
    }

    return Credentials(token=token, base_url=base_url, ui_host=ui_host, keys=keys)
