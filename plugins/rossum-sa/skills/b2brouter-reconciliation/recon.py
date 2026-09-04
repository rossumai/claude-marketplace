"""Reconcile B2Brouter received invoices against Rossum annotations.

Read-only. Channels, queues and accounts are discovered from the organization's
own importer hooks, so no per-customer configuration is needed.

    export ROSSUM_TOKEN=... B2B_API_KEY=...
    python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
        --ui-host <org>.rossum.app --from 2026-01-01

B2Brouter API keys are scoped per ACCOUNT GROUP, so a real organization
routinely supplies SEVERAL keys, one per group -- each in its own
B2B_API_KEY_<LABEL> variable. If one key fails its visibility probe (revoked,
stale, typo'd, wrong group), it is reported and skipped rather than aborting
the whole run; the remaining keys still cover whatever they can, and accounts
no working key can see fall through to the existing UNVERIFIED_SOURCE path.

A real organization can carry channels on different B2Brouter hosts (e.g. a
production channel and a channel on a staging host). One client per (key
label, base URL) pair is built lazily and cached; a key's visible accounts
are probed against the SAME host the channel will be queried on, so an
account is never read through the wrong host's key mapping.
"""

import argparse
import collections
import csv
import dataclasses
import functools
import os
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import sys as _sys
from pathlib import Path as _Path

# Flat sibling modules, not a package (this script is invoked directly, e.g.
# `python3 recon.py` or `python3 .../recon.py` from the skill directory) --
# Python already puts the executed script's own directory on sys.path[0], so
# this only matters when recon.py is imported rather than run directly (e.g.
# from the test suite via a different cwd).
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from b2brouter import (
    LEGACY_API_VERSION,
    NEW_API_VERSION,
    B2bError,
    B2brouterClient,
    other_api_version,
)
from credentials import (
    DEFAULT_CREDENTIALS_PATH,
    CredentialsError,
    init_credentials,
    load_credentials_file,
)
from discovery import Channel, discover_channels, map_accounts_to_keys, select_channels
from match import (
    CSV_COLUMNS,
    DELETED,
    DELETED_AS_DUPLICATE,
    LOOKUP_FAILED,
    NOT_CHECKED,
    Row,
    build_row,
    enumeration_contradiction,
    unverified_row,
)
from rossum import RossumClient, RossumError

# I5: the notes that mean "nothing to do". EVERY other note -- including
# STRANDED_CREATED, FAILED_IMPORT, SPLIT_CONTAINER, DUPLICATE and
# UNKNOWN_STATUS:* -- exits 1. The previous exception set was
# MISSING_IN_ROSSUM + UNVERIFIED_SOURCE only, so a run reporting 72
# STRANDED_CREATED / FAILED_IMPORT rows -- the exact signature of the
# incident this tool was built for -- exited 0, which the README calls a
# clean run. A clean run means nothing missing, nothing unverified, and
# nothing needing action. PENDING is clean because its verdict is simply
# not due yet.
#
# CLEAN_NOTES itself is used for TWO things, deliberately kept as one set:
# it is the base of ACTIONABLE_EXEMPT_NOTES below (the exit-code/actionable
# definition), and it is also what `_backfill_acked_at` treats as "clean" for
# deciding which rows are worth an acked_at detail lookup. DELETED and
# DELETED_AS_DUPLICATE do NOT belong in CLEAN_NOTES itself for that second
# reason: they still get an acked_at lookup like any other exception row
# (see ACTIONABLE_EXEMPT_NOTES for the separate, exit-code-only exemption).
CLEAN_NOTES = ("ok", "ok +xml_twin", "PENDING")

# The exit-code / "rows need action" definition. By the user's explicit
# decision, this tool does not reason about WHY an annotation was deleted --
# DELETED and DELETED_AS_DUPLICATE are two flat, neutral labels
# distinguished purely by whether a Rossum search confirmed a surviving
# annotation for the same invoice number (see match.py's DELETED /
# DELETED_AS_DUPLICATE and recon.py's `_verify_deleted_rows`), and NEITHER
# counts as needing action or drives a non-zero exit -- both are still
# reported, as rows and in the per-channel note summary (DELETED always gets
# its own line there, never folded into the DELETED_AS_DUPLICATE count),
# because they are real information about channel overlap.
#
# Measured live: of 246 rows previously counted as needing action, 222 were
# in this bucket (then labelled DELETED_AS_DUPLICATE) -- ten times the 24
# genuine items (12 DUPLICATE, 9 FAILED_IMPORT, 3 STRANDED_CREATED) buried
# under them. A reader who discovers the headline count is inflated by an
# order of magnitude stops trusting the report -- the same failure mode
# already fixed twice, in the unmatched-id count and the per-account
# UNVERIFIED_SOURCE flags.
ACTIONABLE_EXEMPT_NOTES = CLEAN_NOTES + (DELETED, DELETED_AS_DUPLICATE)
KEY_ENV_PREFIX = "B2B_API_KEY"
DEFAULT_BASE_URL = "https://elis.rossum.ai"

# I2(b): the prefix that marks an account's UNVERIFIED_SOURCE reason as a
# direct per-id attribution -- a Rossum-side id inside the window, matched by
# no listed invoice, that a B2Brouter lookup BY ID actually traced to THIS
# account -- rather than an outright enumeration failure (uncovered, or a
# B2bError). Shared between reconcile_channel, which writes the reason, and
# main()'s summary, which must not call this account's listing "failed": it
# may have enumerated fine, even listed plenty; it is simply missing this one
# id. Reporting it as a failure to enumerate would be simply wrong -- and so
# would flagging every quiet sibling the way the old channel-aggregate check
# used to (see the false positives that replaced it): only an account an
# unmatched id was actually traced to gets this reason.
CONTRADICTED_REASON_PREFIX = "attributed to this account by a direct B2Brouter lookup"

# I6: hard cap on per-id fallback lookups per channel. Every invoice absent
# from the search index costs 2+ sequential GETs, each with retries at a 90s
# timeout. In a mass incident -- or whenever the index comes back empty, which
# is precisely when the fallback would be asked for EVERY invoice -- an
# unbounded fallback makes the run unfinishable exactly when it matters, and a
# killed run writes no CSV at all. Past the cap, the remaining un-indexed
# invoices are disclosed as UNVERIFIED_SOURCE rather than checked; they are
# NEVER called MISSING_IN_ROSSUM, which would over-report.
FALLBACK_LOOKUP_CAP = 200

# Hard cap on per-row DELETED verification searches per channel, same
# spirit as the caps above but set much higher: a DELETED row only ever
# earns the DELETED_AS_DUPLICATE label once this search actually confirms a
# healthy annotation for that invoice number exists somewhere, and the user
# wants that check to normally run for EVERY such row, not a sampled
# fraction -- measured live volume is ~222 a month, one search each, which
# is an acceptable cost. Set well above that (with headroom for growth)
# so the cap is a backstop against a genuine mass incident, not a routine
# limiter. Past the cap, the remaining eligible rows are left exactly as
# they were -- DELETED, unchanged -- never silently promoted OR silently
# treated as a confirmed absence on the strength of a search that was never
# made; the channel summary reports how many were verified against the cap,
# and says plainly when rows were left unverified.
DUPLICATE_VERIFY_CAP = 1000
FALLBACK_CAP_REASON = (
    "per-channel Rossum fallback lookup cap ({cap}) reached; this invoice was NOT "
    "checked against Rossum"
)

# I2(b) again: hard cap on per-id ATTRIBUTION lookups per channel -- one GET
# each (B2brouterClient.get_invoice), in the same spirit as
# FALLBACK_LOOKUP_CAP above. Only ids that are BOTH window-scoped (see
# _window_scoped_ids) AND unmatched by any listed invoice are ever looked up,
# so this tier is normally tiny; the cap exists for the same reason the
# fallback one does -- a mass incident, or a broken search index, must not
# make the run unfinishable. Past the cap, the remaining unmatched ids are
# simply NOT attributed to any account: this makes the check UNDER-report (an
# account with a real unmatched id might go unflagged) rather than guess,
# which is the safe direction for a check whose whole purpose is not crying
# wolf.
ATTRIBUTION_LOOKUP_CAP = 200

# Hard cap on per-id ACKNOWLEDGEMENT lookups per channel, same spirit as the
# two caps above. `acked_at` is only ever backfilled for EXCEPTION rows (see
# match.CLEAN_NOTES via reconcile_channel's own filter below) -- the notes
# that mean "nothing to do" never need the distinction this backfill exists
# for -- so in practice this tier is small: a handful of exceptions, not one
# call per invoice. The cap exists anyway for the same mass-incident reason:
# an unbounded tier here would make the run unfinishable exactly when the
# exception count is largest. Past the cap, the remaining exception rows'
# acked_at stays NOT_CHECKED rather than being silently left blank -- see
# match.NOT_CHECKED.
ACK_LOOKUP_CAP = 200


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B2Brouter ↔ Rossum e-invoice reconciliation")
    parser.add_argument("--ui-host", default=None,
                        help="host used to build clickable links, e.g. acme.rossum.app. "
                             "Required unless a credentials file supplies rossum.ui_host "
                             "(see --credentials)")
    parser.add_argument("--base-url", default=None,
                        help=f"Rossum API base URL (default: {DEFAULT_BASE_URL}, or a "
                             "credentials file's rossum.base_url)")
    parser.add_argument("--init-credentials", nargs="?", const=str(DEFAULT_CREDENTIALS_PATH),
                        default=None, metavar="PATH",
                        help="write a credentials template to PATH (default: "
                             f"{DEFAULT_CREDENTIALS_PATH}) and exit. Refuses to overwrite an "
                             "existing file. Fill in the printed path yourself -- never paste "
                             "keys into a chat with an agent")
    parser.add_argument("--credentials", default=None, metavar="PATH",
                        help="read ROSSUM_TOKEN/base_url/ui_host and B2Brouter keys from this "
                             "JSON file instead of the environment. Resolution order: this "
                             f"flag, if given; else {DEFAULT_CREDENTIALS_PATH} if it exists; "
                             "else environment variables (ROSSUM_TOKEN, B2B_API_KEY*), exactly "
                             "as before this flag existed")
    parser.add_argument("--from", dest="date_from", help="window start, ISO date or datetime")
    parser.add_argument("--to", dest="date_to", help="window end, ISO date or datetime")
    parser.add_argument("--channel", default=None, help="hook id or name substring; default all")
    parser.add_argument("--out", default="b2brouter_reconciliation.csv")
    parser.add_argument("--only-exceptions", action="store_true")
    parser.add_argument("--grace-minutes", type=int, default=30)
    parser.add_argument("--show-discovery", action="store_true",
                        help="print the discovered channels and exit")
    parser.add_argument("--check-coverage", action="store_true",
                        help="probe which discovered accounts the supplied keys can "
                             "see, print the per-channel coverage, and exit -- fetches "
                             "no invoices")
    parser.add_argument("--relax-x509-strict", action="store_true",
                        help="opt-in: clear ssl.VERIFY_X509_STRICT (the Python "
                             "<=3.12 default) for both Rossum and B2Brouter "
                             "connections -- for a trusted TLS-inspecting proxy "
                             "CA that omits the Key Usage extension. Chain and "
                             "hostname verification stay fully enforced; see "
                             "README's TLS interception section. NOT a "
                             "verification bypass -- default off")
    parser.add_argument("--b2b-api-version", default=None,
                        choices=(LEGACY_API_VERSION, NEW_API_VERSION),
                        help="pin the B2Brouter X-B2B-API-Version header to this exact "
                             "generation and skip auto-detection entirely. By DEFAULT "
                             "this tool auto-detects: it probes each host at "
                             f"{LEGACY_API_VERSION} and, if the host rejects that with "
                             "api_version_subdomain_mismatch, retries once at "
                             f"{NEW_API_VERSION} and uses whichever one worked for every "
                             "later call on that host. This flag, or a credentials "
                             "file's b2brouter.api_version, overrides that -- useful "
                             "when you already know a group's generation and want to "
                             "skip the probe")
    return parser.parse_args(argv)


def build_relaxed_x509_ssl_context() -> ssl.SSLContext:
    """A default TLS context with ONLY VERIFY_X509_STRICT cleared.

    RFC 5280 permits a CA certificate with Basic Constraints CA:TRUE and no
    Key Usage extension at all (absence means unrestricted). Python 3.13+
    enables OpenSSL's VERIFY_X509_STRICT by default, and OpenSSL 3.6 in
    strict mode rejects exactly that RFC-legal CA with "certificate verify
    failed: CA cert does not include key usage extension". Some corporate
    TLS-inspecting proxies re-sign with a CA certificate built that way --
    the same host and bundle succeed against a LibreSSL-linked Python and
    against curl, neither of which enforces this check.

    This clears that one strictness flag and NOTHING else: verify_mode stays
    CERT_REQUIRED and check_hostname stays True -- both asserted by the
    caller (--relax-x509-strict wiring in main()) and pinned in tests,
    because they are the entire reason this is an acceptable, narrow opt-in
    rather than a "skip verification" switch. A bundle that does not include
    the intercepting CA still correctly refuses the connection through a
    context built here; this reverts exactly one RFC-strictness check to the
    pre-3.13 default, not trust itself.
    """
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def collect_keys(env: dict[str, str]) -> dict[str, str]:
    """Every B2Brouter key in the environment, by its variable name."""
    return {name: value for name, value in env.items() if name.startswith(KEY_ENV_PREFIX)}


def _credentials_source_path(args: argparse.Namespace) -> Path | None:
    """Which credentials FILE (if any) this run should use, per the
    documented resolution order: an explicit `--credentials PATH` wins
    outright; otherwise the default path is used only if it already
    exists (so an operator who has never run --init-credentials keeps
    today's environment-variable behaviour, unchanged); otherwise None,
    meaning "use the environment".

    Reads the module-level `DEFAULT_CREDENTIALS_PATH` by bare name (not a
    qualified `credentials.DEFAULT_CREDENTIALS_PATH`) so a test can
    monkeypatch `recon.DEFAULT_CREDENTIALS_PATH` to a tmp_path location --
    the same pattern this module already uses for RossumClient/
    B2brouterClient -- without ever touching the real home directory.
    """
    if args.credentials:
        return Path(args.credentials).expanduser()
    if DEFAULT_CREDENTIALS_PATH.exists():
        return DEFAULT_CREDENTIALS_PATH
    return None


def _window(args: argparse.Namespace, now: datetime) -> tuple[datetime, datetime]:
    """The reporting window, as an inclusive [since, until] pair in UTC.

    Raises ValueError on an unparseable or INVERTED window. An inverted window
    is not a harmless no-op: every invoice is filtered out client-side, so the
    run reports zero invoices and (before the per-account contradiction check)
    could read as a clean, empty reconciliation of a busy month.
    """
    def parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )

    until = parse(args.date_to) if args.date_to else now
    since = parse(args.date_from) if args.date_from else until - timedelta(days=30)
    if since > until:
        raise ValueError(
            f"--from ({since:%Y-%m-%dT%H:%M:%SZ}) is after --to "
            f"({until:%Y-%m-%dT%H:%M:%SZ}); that window matches no invoice at all"
        )
    return since, until


def build_client_resolver(
    channels: list[Channel],
    keys: dict[str, str],
    client_factory: Callable[..., object] = B2brouterClient,
    pinned_api_version: str | None = None,
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, list[str]],
    Callable[[Channel], Callable[[str], object | None]],
    set[str],
]:
    """Build one B2Brouter client per (key label, base URL, api version) pair,
    lazily and cached.

    Visibility is probed PER BASE URL: a key's visible accounts are discovered
    against the same host the channel that owns them will actually be queried
    on, never against some other channel's host. Real organizations mix hosts
    (e.g. a production channel and a channel on a staging host); reusing one
    client across hosts would silently read the wrong environment.

    API VERSION: `pinned_api_version` (from `--b2b-api-version` or a
    credentials file's `b2brouter.api_version`) is used outright for every
    client on every host when given -- no probing of the alternate
    generation at all. Left as None (the default), the API version is
    AUTO-DETECTED, per host: the first key probed against a given host tries
    B2brouterClient's own default (LEGACY_API_VERSION). If that specific
    call fails with `api_version_subdomain_mismatch` -- the code B2Brouter
    returns when a request's version and its host/path don't match, meaning
    this host's account group actually defaults to the OTHER generation --
    it is retried exactly once against `other_api_version(...)`. A
    successful retry locks that version for every LATER call against this
    same host, across every key and both listing and detail lookups, and
    prints one line to stderr naming the host and the version chosen. Any
    OTHER failure code (an outright bad key, for instance) is never retried
    -- it is the same failure at either version, and a retry would just
    waste a second request confirming that. If detection has already locked
    a version for a host, every later probe on it uses that version
    directly and is not itself retried again.

    A real organization routinely supplies SEVERAL keys, one per B2Brouter
    account group, so the visibility probe is run PER KEY rather than as one
    all-or-nothing call: if a single key is revoked, stale, typo'd, or simply
    the wrong group (HTTP 401 from visible_account_ids), that key alone is
    skipped -- with a stderr warning naming its ENVIRONMENT VARIABLE NAME,
    never the key value, which is a credential and must never be printed or
    logged. The remaining keys still cover whatever they can. Accounts left
    uncovered by every surviving key flow into uncovered_by_host exactly as
    before, so a bad key can only ever make the report MORE cautious --
    turning a covered account into an UNVERIFIED_SOURCE row -- and never
    falsely clean. Only when EVERY supplied key fails its probe on a given
    host does this function raise B2bError (naming all the failed variables):
    at that point nothing at all is known about that host's coverage, and
    silently reporting zero coverage as if it were a verified result would be
    pretending.

    Both return values below are HOST-SCOPED, not merged into one flat
    structure keyed by account id alone. The same account id string can
    legitimately appear on two different hosts (e.g. a staging tenant
    mirroring production account numbering) — possibly covered by two
    DIFFERENT keys, or uncovered on one host while covered on the other. A
    flat, account-id-only mapping would let the later-processed host's label
    silently win and get paired with a host that label was never verified
    against; a flat uncovered set would let an id uncovered on one host blind
    a channel on a different host with real coverage. Neither is safe.

    Returns (mapping, uncovered_by_host, get_b2b_for_account, failed_keys):
      - mapping: (base_url, account_id) -> the key label VERIFIED to see that
        account ON THAT HOST. Never look this up by account id alone.
      - uncovered_by_host: b2b_base_url -> account ids no supplied (working)
        key could see ON THAT HOST. Callers must scope this to one channel's
        own b2b_base_url before passing it into reconcile_channel — never use
        it as one shared set across channels on different hosts.
      - get_b2b_for_account: channel -> the `b2b_for_account` callable
        `reconcile_channel` expects, resolving clients against THAT channel's
        own base URL and the label verified for that same base URL.
      - failed_keys: the set of key VARIABLE NAMES (never key values) whose
        visibility probe raised B2bError on at least one host. Callers should
        report this set to the operator so they know which variable to fix.
    """
    cache: dict[tuple[str, str, str], object] = {}
    # host -> the version an earlier successful retry locked in for it. Only
    # ever written when pinned_api_version is None -- see _version_for and
    # _probe_visibility below.
    detected_version_by_host: dict[str, str] = {}

    def _version_for(base_url: str) -> str:
        if pinned_api_version is not None:
            return pinned_api_version
        return detected_version_by_host.get(base_url, LEGACY_API_VERSION)

    def client_for(label: str, base_url: str, version: str) -> object:
        cache_key = (label, base_url, version)
        if cache_key not in cache:
            cache[cache_key] = client_factory(keys[label], base_url, api_version=version)
        return cache[cache_key]

    def _probe_visibility(label: str, base_url: str) -> set[str]:
        """One key's visibility probe against one host -- see this
        function's role in the docstring above ("API VERSION")."""
        version = _version_for(base_url)
        try:
            return client_for(label, base_url, version).visible_account_ids()
        except B2bError as exc:
            if pinned_api_version is not None or exc.code != "api_version_subdomain_mismatch":
                raise
            alternate = other_api_version(version)
            result = client_for(label, base_url, alternate).visible_account_ids()
            if detected_version_by_host.get(base_url) != alternate:
                detected_version_by_host[base_url] = alternate
                print(
                    f"B2Brouter: host {base_url} rejected API version {version} "
                    "(api_version_subdomain_mismatch) -- auto-detected "
                    f"{alternate} instead; using it for every later call on "
                    "this host",
                    file=sys.stderr,
                )
            return result

    mapping: dict[tuple[str, str], str] = {}
    uncovered_by_host: dict[str, list[str]] = {}
    failed_keys: set[str] = set()
    for base_url in sorted({channel.b2b_base_url for channel in channels}):
        channels_here = [c for c in channels if c.b2b_base_url == base_url]
        visibility: dict[str, set[str]] = {}
        host_failed: list[str] = []
        for label in keys:
            try:
                visibility[label] = _probe_visibility(label, base_url)
            except B2bError as exc:
                host_failed.append(label)
                failed_keys.add(label)
                print(
                    f"WARNING: B2Brouter key {label} failed its visibility probe "
                    f"on {base_url}: {exc}. Skipping this key; its accounts are "
                    "only covered if another key can see them.",
                    file=sys.stderr,
                )
        if host_failed and len(host_failed) == len(keys):
            raise B2bError(
                "every supplied B2Brouter key failed its visibility probe on "
                f"{base_url} ({', '.join(sorted(host_failed))}); nothing is known "
                "about this host's account coverage"
            )
        group_mapping, group_uncovered = map_accounts_to_keys(channels_here, visibility)
        for account_id, label in group_mapping.items():
            # KEYED BY (base_url, account_id), never by account_id alone -- an
            # earlier version used a flat dict and let the same account id
            # string on a second host silently overwrite the first host's
            # verified label, pairing that id with a key that was never
            # checked against it. A test used to pin the specific case (the
            # same id covered by two DIFFERENT keys on two different hosts,
            # each verified only for its own host) but was pruned as a
            # duplicate of the general host-scoping coverage below; this
            # comment is what's left to explain why the tuple key exists.
            mapping[(base_url, account_id)] = label
        if group_uncovered:
            uncovered_by_host[base_url] = group_uncovered

    def get_b2b_for_account(channel: Channel) -> Callable[[str], object | None]:
        def b2b_for_account(account_id: str) -> object | None:
            label = mapping.get((channel.b2b_base_url, account_id))
            if label is None:
                return None
            return client_for(label, channel.b2b_base_url, _version_for(channel.b2b_base_url))
        return b2b_for_account

    return mapping, uncovered_by_host, get_b2b_for_account, failed_keys


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _window_scoped_ids(index: dict, since: datetime) -> set[str]:
    """einvoice ids from the index whose NEWEST annotation was created at or
    after `since` -- i.e. ids that actually arrived in the reporting window.

    einvoice_index() is deliberately built WIDER than [since, until]:
    rossum.py's INDEX_LOOKBACK subtracts 24 hours from `since` before
    querying, so an invoice arriving right at the window's edge still finds
    an annotation created slightly later. That is correct and must stay --
    but it means the index also holds ids whose ONLY annotation predates the
    window: pure lookback-tail context, not something that arrived now.
    Treating every id in the index as if it belonged to the window is the
    exact bug this function exists to close: on a live run it substantially
    inflated the "unmatched" count and, through the per-account attribution
    check below, produced false UNVERIFIED_SOURCE flags on accounts that had
    simply been quiet.

    A re-import or a later sibling annotation created after `since` puts the
    id back in scope even if an older annotation for the same id predates the
    window -- hence "newest", not "any" or "all".
    """
    scoped: set[str] = set()
    for einvoice_id, anns in index.items():
        newest = max((_parse_created_at(a.created_at) for a in anns), default=None)
        if newest is not None and newest >= since:
            scoped.add(einvoice_id)
    return scoped


def _pick_attribution_client(channel, b2b_for_account, uncovered: set[str]):
    """The one client used for all of this channel's per-id attribution
    lookups (see `_attribute_unmatched_ids`).

    Deliberately ONE client, not one per account: attribution has to ask
    "which account owns this id" without already knowing the answer, so
    there is no account to resolve a client FOR yet. A B2Brouter API key is
    scoped to an entire ACCOUNT GROUP, not a single account, so the first
    covered account's client can see this channel's invoices exactly as well
    as any other covered account's client would. An account that merely
    failed its own LISTING call earlier is still tried here: a listing
    failure on one account's endpoint does not mean the same key's
    single-invoice-by-id lookup is broken too.
    """
    for account_id in channel.account_ids:
        if account_id in uncovered:
            continue
        client = b2b_for_account(account_id)
        if client is not None:
            return client
    return None


def _attribute_unmatched_ids(
    unmatched_ids: list[str], client, *, cap: int, since: datetime
) -> tuple[dict[str, int], int, int]:
    """Trace each window-scoped, unmatched id to the account that owns it --
    unless the invoice itself turns out to have arrived before the window,
    in which case it is a re-import artefact, not a listing gap, and no
    account is flagged for it.

    One GET per id via `client.get_invoice`, bounded by `cap` -- in the same
    spirit as FALLBACK_LOOKUP_CAP: an unbounded tier here would make the run
    unfinishable exactly when a mass incident makes attribution matter most.
    A B2bError from a single lookup is swallowed -- the id is simply left
    unattributed, never treated as proof it belongs to no account -- so one
    bad lookup cannot derail the rest. A 404 (the id genuinely does not
    resolve for this key) is likewise left unattributed AND is not counted
    as a pre-window artefact either: with no invoice object to read a date
    from, there is no basis for calling it benign, so it stays exactly what
    it already was -- an unresolved entry in the window-scoped unmatched
    count -- rather than being quietly exonerated.

    An invoice that resolves and carries a `created_at` strictly before
    `since` is the mirror image of the index's own lookback tail: the
    Rossum annotation was created inside the window (that is how the id got
    this far), but the invoice itself legitimately arrived long before it --
    typically a re-import of an old document. No account's LISTING for this
    window could ever have returned it, however complete that listing is,
    so treating it as a gap would flag an innocent account every time. An
    invoice with no usable `created_at` (an account id was found, but the
    date could not be) is NOT given this exemption -- unknown arrival is
    conservatively treated the same as "inside the window": flag it rather
    than guess it is benign.

    Returns (counts, used, pre_window_artefacts): `counts` maps account id
    -> how many unmatched ids were traced to it as a genuine gap; `used` is
    how many GETs were actually spent (the caller compares this against
    `len(unmatched_ids)` to tell whether the cap was reached, rather than
    this function claiming completeness itself); `pre_window_artefacts` is
    how many resolved invoices were excluded as pre-window re-imports.
    """
    counts: dict[str, int] = collections.Counter()
    used = 0
    pre_window_artefacts = 0
    for einvoice_id in unmatched_ids:
        if used >= cap:
            break
        used += 1
        try:
            ref = client.get_invoice(einvoice_id)
        except B2bError:
            ref = None
        if ref is None:
            continue
        arrived = None
        if ref.created_at:
            try:
                arrived = _parse_created_at(ref.created_at)
            except ValueError:
                arrived = None
        if arrived is not None and arrived < since:
            pre_window_artefacts += 1
            continue
        counts[ref.account_id] += 1
    return dict(counts), used, pre_window_artefacts


def _backfill_acked_at(
    rows: list[Row], b2b_for_account, *, cap: int
) -> tuple[list[Row], int, int]:
    """Fill `acked_at` for EXCEPTION rows only, via one detail lookup each.

    Why exception rows only: `acked_at` is the column that tells apart the
    two very different faults behind a missing invoice -- a timestamp means
    the importer collected the invoice and then lost it (our fault, and the
    source will never re-deliver it because it considers the job done),
    while a genuinely empty value means the source never handed it over
    (their fault, and it may still be pending). That distinction is only
    ACTIONABLE on a row that is already flagged as something needing
    attention -- CLEAN_NOTES rows have nothing to act on either way -- so
    this is scoped to `note not in CLEAN_NOTES`, which keeps the cost to a
    handful of calls per run instead of one per invoice.

    Reuses `B2brouterClient.get_invoice` -- the SAME by-id call attribution
    already uses, never a second endpoint -- via `b2b_for_account`, exactly
    the resolver `reconcile_channel` already has for this channel.

    Every row this function does not (or cannot) actually look up keeps
    `NOT_CHECKED`, the value `build_row`/`unverified_row` already put there:
    a clean row's own `acked_at` is left completely untouched by this
    function -- it is filtered out below, by note, before any lookup is
    attempted, and never mutated even when the cap is spent. A row that WAS
    attempted but the lookup did not resolve (network failure, 404, an
    unrecognised shape) gets LOOKUP_FAILED, never a silent blank -- the
    whole point of this backfill is that a blank `acked_at` must never again
    be readable as "not acknowledged".

    Returns (rows, used, capped): `rows` is a NEW list (Row is frozen, so
    each changed row is `dataclasses.replace`d); `used` is how many lookups
    were actually spent; `capped` is how many exception rows were left
    NOT_CHECKED purely because the cap was already spent before their turn
    (as opposed to no client being available for their account, which is
    reported the same way but is not a cap exhaustion).
    """
    used = 0
    capped = 0
    filled: list[Row] = []
    for row in rows:
        if not row.einvoice_id or row.note in CLEAN_NOTES:
            filled.append(row)
            continue
        if used >= cap:
            capped += 1
            filled.append(row)
            continue
        client = b2b_for_account(row.account)
        if client is None:
            # No client is available for this account at all (uncovered, or
            # every key failed) -- no lookup was attempted, so this is the
            # same "never checked" outcome as a clean row, not a failure.
            filled.append(row)
            continue
        used += 1
        try:
            ref = client.get_invoice(row.einvoice_id)
        except B2bError:
            filled.append(dataclasses.replace(row, acked_at=LOOKUP_FAILED))
            continue
        if ref is None:
            # A 404: this key/account group cannot see this invoice id by
            # direct lookup either -- a failed check, not evidence of
            # anything about acknowledgement.
            filled.append(dataclasses.replace(row, acked_at=LOOKUP_FAILED))
            continue
        # ref.ack_at is None both when the field was absent/non-string AND
        # when the invoice genuinely has no acknowledgement yet -- but this
        # lookup succeeded (a real invoice object came back), so here None
        # means the latter: write a genuinely empty cell, distinguishable
        # from NOT_CHECKED and from LOOKUP_FAILED.
        filled.append(dataclasses.replace(row, acked_at=ref.ack_at or ""))
    return filled, used, capped


def _verify_deleted_rows(
    rows: list[Row], rossum, *, cap: int
) -> tuple[list[Row], int, int]:
    """Try to upgrade DELETED rows to DELETED_AS_DUPLICATE -- the ONLY way
    that label is ever earned -- by searching Rossum for a healthy
    annotation elsewhere carrying the same invoice NUMBER.

    classify() only ever returns the neutral DELETED for a row whose every
    annotation is deleted/purged: it has no way to know whether a
    *different* e-invoice id elsewhere in the organization holds a
    surviving copy, and by the user's explicit decision this tool does not
    speculate about why the annotations were deleted. This function is what
    actually looks -- one search per eligible row, capped -- and the two
    labels afterward are distinguished purely by what that search found,
    nothing else.

    Only rows whose note is exactly DELETED AND that carry a non-empty
    `invoice_number` are eligible -- there is nothing to search by
    otherwise, and such a row is left exactly as it was, DELETED, not
    silently treated as checked. Bounded by `cap`, in the same spirit as
    every other per-run lookup tier, though set high enough (see
    DUPLICATE_VERIFY_CAP) that the whole month's volume is normally covered.

    A row that is NOT attempted -- no invoice_number, the cap already
    spent, or the search itself failed (RossumError, swallowed here exactly
    like a single failed attribution lookup is swallowed: a failed check
    proves nothing, so it must never be read as either confirmation or
    refutation) -- keeps DELETED, unchanged. This is the crux of the
    label's meaning: DELETED on an unverified row means "we did not look",
    never "nothing exists" -- only a search that actually completed and
    found nothing gets to mean the latter.

    A row that IS attempted and finds a healthy annotation somewhere for
    that invoice number is promoted to DELETED_AS_DUPLICATE -- earned, not
    assumed. A row that IS attempted and finds nothing stays DELETED, now a
    CONFIRMED absence rather than an unexamined one, though the note string
    itself does not change to say so (the verified/not_verified counts
    returned here, and the per-note summary elsewhere, are what makes that
    visible).

    Returns (rows, verified, not_verified): `verified` is how many searches
    actually completed (successfully, regardless of outcome -- promoted or
    left DELETED both count); `not_verified` is how many eligible rows
    (DELETED, non-empty invoice_number) were never resolved -- the cap was
    reached before their turn, or the search itself failed.
    """
    used = 0
    verified = 0
    not_verified = 0
    filled: list[Row] = []
    for row in rows:
        if row.note != DELETED or not row.invoice_number:
            filled.append(row)
            continue
        if used >= cap:
            not_verified += 1
            filled.append(row)
            continue
        used += 1
        try:
            found = rossum.has_surviving_original(row.invoice_number)
        except RossumError:
            # The check itself did not complete -- leave the row exactly as
            # DELETED, never promote (or treat as a confirmed absence) on
            # the strength of a search that never actually resolved.
            not_verified += 1
            filled.append(row)
            continue
        verified += 1
        if found:
            filled.append(dataclasses.replace(row, note=DELETED_AS_DUPLICATE))
        else:
            filled.append(row)
    return filled, verified, not_verified


def reconcile_channel(
    channel,
    b2b_for_account,
    rossum,
    *,
    since: datetime,
    until: datetime,
    now: datetime,
    grace_minutes: int,
    uncovered: set[str],
    ui_host: str,
    fallback_cap: int = FALLBACK_LOOKUP_CAP,
    attribution_cap: int = ATTRIBUTION_LOOKUP_CAP,
    ack_cap: int = ACK_LOOKUP_CAP,
    duplicate_verify_cap: int = DUPLICATE_VERIFY_CAP,
) -> tuple[list[Row], list[str]]:
    """Join one channel's invoices to its Rossum annotations.

    Every account in `failed` -- uncovered, or an outright B2bError -- gets a
    synthetic UNVERIFIED_SOURCE row via `unverified_row`, in addition to the
    stderr warning and its place in the returned `failed` list. Without that
    row, an un-enumerable account contributes ZERO invoices, so it silently
    disappears from the CSV rather than showing up as an exception in it --
    and the CSV is the artefact that actually travels; nobody reading it sees
    stderr or the exit code.

    I2: the enumeration-contradiction check is applied PER ACCOUNT, not only
    channel-wide. Channel-wide alone, a single listed invoice anywhere in the
    channel disarmed it entirely: 19 accounts listing nothing and one listing
    one invoice, against 900 e-invoices in the channel's queues, produced one
    `ok` row and exit 0.

    The per-account version is PRECISE, not sibling-based. einvoice_index()
    is deliberately wider than [since, until] (see rossum.py's
    INDEX_LOOKBACK), so an id in the index is not necessarily an id that
    arrived IN the window; `_window_scoped_ids` keeps only ids whose newest
    annotation was created at or after `since`, and ids that exist solely in
    the lookback tail are reported as their own, deliberate count -- never
    folded into "unmatched". Of the window-scoped ids, the ones no listed
    invoice matched are then looked up in B2Brouter BY ID
    (`_attribute_unmatched_ids`) to learn which account actually owns each
    one, and ONLY that account is flagged UNVERIFIED_SOURCE. An account that
    simply listed nothing during the window, with no unmatched id of its own,
    is never flagged: some accounts legitimately receive only a handful of
    invoices a YEAR, and flagging them for a sibling's shortfall (the old
    channel-aggregate behaviour) is exactly the false positive this
    replaced -- measured on a live run, it flagged multiple quiet accounts
    across several channels for no real reason.

    The unmatched-id count and the lookback-tail count are both reported but
    deliberately do NOT produce a row per id, and get no note type of their
    own: a handful of unmatched ids at window edges is normal, and
    false-positive rows would erode trust in the report faster than a
    missing check.

    I6: the per-id fallback lookup tier is capped at `fallback_cap`, and the
    per-id ATTRIBUTION tier above is separately capped at `attribution_cap` --
    same reasoning, same shape: past either cap, the run says so explicitly
    rather than silently claiming completeness. Past the fallback cap, the
    remaining un-indexed invoices get an UNVERIFIED_SOURCE row whose reason
    names the cap; past the attribution cap, the remaining unmatched ids are
    simply left unattributed. Neither invoice nor account is ever
    classified/flagged on the strength of a lookup that was never made --
    over-reporting is its own kind of wrong.
    """
    index = rossum.einvoice_index(channel.queue_ids, since=since)

    invoices, failed = [], []
    unverified_rows: list[Row] = []
    for account_id in channel.account_ids:
        if account_id in uncovered:
            failed.append(account_id)
            reason = "no API key can see it"
            print(f"  ! account {account_id}: {reason}", file=sys.stderr)
            unverified_rows.append(unverified_row(channel.name, account_id, reason))
            continue
        client = b2b_for_account(account_id)
        try:
            listed = client.received_invoices(account_id, since=since, until=until)
            invoices.extend(listed)
            # I4: rows the source listed but that carried no id or no
            # created_at were skipped. They are invoices that exist on the
            # network and are absent from this report, so they are named here
            # rather than left to be inferred from a row count.
            skipped = client.skipped_rows.get(account_id, 0)
            if skipped:
                print(
                    f"  ! account {account_id}: {skipped} source row(s) skipped as "
                    "unidentifiable (no id or no created_at) and are NOT in this "
                    "report",
                    file=sys.stderr,
                )
        except B2bError as exc:
            failed.append(account_id)
            print(f"  ! account {account_id} failed: {exc}", file=sys.stderr)
            unverified_rows.append(unverified_row(channel.name, account_id, str(exc)))

    # Window-scoped once, up front, and reused by BOTH contradiction checks
    # below (channel-wide and per-account) plus the unmatched-id count: the
    # index itself is deliberately wider than the window (see
    # _window_scoped_ids and rossum.py's INDEX_LOOKBACK), and every consumer
    # of "how many e-invoice ids does Rossum hold for this channel" must
    # agree on what "in the window" means, or a channel that legitimately
    # received nothing this window -- but whose queues hold e-invoices from
    # BEFORE it -- would be declared unverified on the strength of the
    # lookback tail alone.
    window_scoped_ids = _window_scoped_ids(index, since)
    tail_only_ids = set(index) - window_scoped_ids

    if enumeration_contradiction(len(invoices), len(window_scoped_ids)):
        reason = (
            f"source enumerated 0 invoices while Rossum holds "
            f"{len(window_scoped_ids)} e-invoices from this channel's queues, inside "
            "the window; the invoice index is not usable with these credentials"
        )
        print(
            f"  ! {channel.name}: the source enumerated 0 invoices while Rossum holds "
            f"{len(window_scoped_ids)} e-invoices from this channel's queues, inside "
            "the window. The invoice index is not usable with these credentials; "
            "treating the channel as unverified.",
            file=sys.stderr,
        )
        return (
            [unverified_row(channel.name, account_id, reason) for account_id in channel.account_ids],
            list(channel.account_ids),
        )

    # I2(a): Rossum-side ids that NO listed invoice matched -- but ONLY among
    # ids whose newest annotation is actually inside the window. An id that
    # exists solely in the lookback tail is disclosed separately, right
    # below, so the exclusion is visible rather than silent -- silently
    # dropping ids from this count is how the opposite bug (a real orphan
    # going uncounted) gets introduced later.
    unmatched_index_ids = sorted(
        window_scoped_ids - {invoice.einvoice_id for invoice in invoices}
    )
    print(f"    {len(unmatched_index_ids):6d}  Rossum e-invoice id(s) matched by no "
          f"listed invoice (window-scoped)")
    print(f"    {len(tail_only_ids):6d}  Rossum e-invoice id(s) outside the window "
          f"(lookback-only, skipped from the count above -- deliberate, not a gap)")
    if unmatched_index_ids:
        print(
            f"  ! {channel.name}: {len(unmatched_index_ids)} Rossum e-invoice id(s) in "
            "this channel's queues, created inside the window, were matched by no "
            "listed invoice. A few are normal at window edges; a large count means the "
            "source listing is not returning everything these queues actually received.",
            file=sys.stderr,
        )

    # I2(b): per-account attribution, not a channel-aggregate guess. Each
    # window-scoped unmatched id is looked up in B2Brouter BY ID to learn
    # which account actually owns it AND when the invoice itself arrived --
    # only THAT account is flagged, and only for an id whose invoice did not
    # itself predate the window (see _attribute_unmatched_ids: a re-import
    # of an old invoice is an artefact, not a gap). Never a quiet sibling
    # that simply listed nothing of its own.
    attribution_client = _pick_attribution_client(channel, b2b_for_account, uncovered)
    attributed_counts: dict[str, int] = {}
    attribution_used = 0
    pre_window_artefacts = 0
    if unmatched_index_ids and attribution_client is not None:
        attributed_counts, attribution_used, pre_window_artefacts = _attribute_unmatched_ids(
            unmatched_index_ids, attribution_client, cap=attribution_cap, since=since,
        )
    print(f"    {attribution_used:6d}  per-account attribution lookups used "
          f"(cap {attribution_cap})")
    print(f"    {pre_window_artefacts:6d}  unmatched id(s) whose B2Brouter invoice "
          f"arrived before the window (re-import artefact, not a gap -- excluded from "
          f"attribution)")
    if unmatched_index_ids and attribution_client is None:
        print(
            f"  ! {channel.name}: no covered account had a usable client to attribute "
            f"the {len(unmatched_index_ids)} unmatched id(s) to an account; per-account "
            "attribution was NOT performed for this channel.",
            file=sys.stderr,
        )
    elif attribution_used < len(unmatched_index_ids):
        print(
            f"  ! {channel.name}: the attribution lookup cap ({attribution_cap}) was "
            f"reached; only {attribution_used} of {len(unmatched_index_ids)} unmatched "
            "id(s) were checked. Attribution is INCOMPLETE for the remainder -- an "
            "account with a real unmatched id may not be flagged. Narrow the window or "
            "re-run.",
            file=sys.stderr,
        )

    already_failed = set(failed)
    for account_id, count in sorted(attributed_counts.items()):
        # Defensive: an id this channel's own queues produced should trace to
        # one of this channel's own accounts. Skip anything else rather than
        # inject an out-of-scope account into this channel's report.
        if account_id not in channel.account_ids or account_id in already_failed:
            continue
        failed.append(account_id)
        reason = (
            f"{CONTRADICTED_REASON_PREFIX}: {count} Rossum e-invoice id(s) in this "
            "channel's queues, created inside the window and matched by no listed "
            "invoice, were traced to this account by a direct B2Brouter lookup"
        )
        print(f"  ! account {account_id}: {reason}", file=sys.stderr)
        unverified_rows.append(unverified_row(channel.name, account_id, reason))

    failed_set = set(failed)
    rows: list[Row] = []
    fallback_used = 0
    capped = 0
    for invoice in invoices:
        cap_reached = False
        if invoice.einvoice_id in index:
            anns = index[invoice.einvoice_id]
        elif fallback_used < fallback_cap:
            # Exact-filename fallback: catches purged annotations and documents
            # outside the discovered queues.
            fallback_used += 1
            anns = rossum.lookup_einvoice(invoice.einvoice_id)
        else:
            cap_reached = True
            capped += 1
            anns = []
        row = build_row(
            channel.name,
            invoice,
            anns,
            now=now,
            grace_minutes=grace_minutes,
            # A capped invoice was not checked, so its verdict is unknown --
            # exactly what source_ok=False expresses.
            source_ok=not cap_reached and invoice.account_id not in failed_set,
            ui_host=ui_host,
        )
        if cap_reached:
            row = dataclasses.replace(
                row, b2b_state=FALLBACK_CAP_REASON.format(cap=fallback_cap)
            )
        rows.append(row)

    print(f"    {fallback_used:6d}  per-id Rossum fallback lookups used "
          f"(cap {fallback_cap})")
    if capped:
        print(
            f"  ! {channel.name}: the fallback lookup cap ({fallback_cap}) was reached; "
            f"{capped} invoice(s) were NOT checked against Rossum and are reported "
            "UNVERIFIED_SOURCE, not missing. Narrow the window or fix the search index, "
            "then re-run.",
            file=sys.stderr,
        )

    # Backfill acked_at for exception rows only -- see _backfill_acked_at's
    # own docstring for why. Run AFTER the fallback loop above (so a
    # fallback-capped row's note, UNVERIFIED_SOURCE, is already final) and
    # BEFORE unverified_rows is appended: those synthetic rows carry no
    # einvoice_id at all, so _backfill_acked_at already leaves them alone,
    # but there is no reason to hand them to it in the first place.
    rows, ack_used, ack_capped = _backfill_acked_at(rows, b2b_for_account, cap=ack_cap)
    print(f"    {ack_used:6d}  per-invoice acknowledgement lookups used (cap {ack_cap})")
    if ack_capped:
        print(
            f"  ! {channel.name}: the acknowledgement lookup cap ({ack_cap}) was reached; "
            f"{ack_capped} exception row(s) were NOT checked and are marked "
            f"'{NOT_CHECKED}' in acked_at rather than left blank.",
            file=sys.stderr,
        )

    # Try to upgrade DELETED rows to DELETED_AS_DUPLICATE -- see
    # _verify_deleted_rows' own docstring. Order relative to the acked_at
    # backfill above does not matter: DELETED and DELETED_AS_DUPLICATE are
    # equally eligible for an acked_at lookup either way (neither is in
    # CLEAN_NOTES), so which one runs first changes nothing either function
    # reads. This is also what gives DELETED its own line in the per-channel
    # note summary below (summarize() counts by exact note string, so
    # DELETED and DELETED_AS_DUPLICATE are always reported separately, never
    # folded together).
    rows, duplicate_verified, duplicate_not_verified = _verify_deleted_rows(
        rows, rossum, cap=duplicate_verify_cap
    )
    print(f"    {duplicate_verified:6d}  DELETED row(s) checked for a surviving original "
          f"(cap {duplicate_verify_cap})")
    if duplicate_not_verified:
        print(
            f"  ! {channel.name}: {duplicate_not_verified} DELETED row(s) were NOT "
            "verified (the cap was reached, or the search itself failed) and keep the "
            'DELETED label -- for these rows that means "we did not look", not '
            '"nothing exists".',
            file=sys.stderr,
        )

    rows.extend(unverified_rows)
    rows.sort(key=lambda r: (r.channel, r.arrived_at))
    return rows, failed


def write_csv(rows: list[Row], path) -> None:
    # utf-8-sig: sender names carry non-ASCII characters and Excel needs the BOM.
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_dict())


def summarize(rows: list[Row]) -> dict[str, int]:
    return dict(collections.Counter(row.note for row in rows))


def check_coverage(
    channels: list[Channel],
    uncovered_by_host: dict[str, list[str]],
    failed_keys: set[str] = frozenset(),
) -> int:
    """Print per-channel account coverage and return the exit code for it.

    Fetches no invoices -- it only reports what build_client_resolver's
    visibility probe (visible_account_ids per host) already found, so it is
    cheap to run before committing to a full reconciliation. A count alone
    is not actionable; an operator needs the actual uncovered ids to go ask
    for access, so they are always listed explicitly, never just counted.

    `failed_keys` -- key VARIABLE NAMES whose visibility probe raised
    B2bError -- always forces a non-zero exit, even when every account
    happens to be covered by a surviving key. The operator ran this mode to
    ask "are my credentials right?", and a key that failed its probe means
    one of them is not; that must not be masked by the accounts otherwise
    looking fully covered.

    Returns 0 if every account on every channel is covered AND no key
    failed its probe, 1 otherwise.
    """
    any_uncovered = False
    for channel in channels:
        channel_uncovered = uncovered_by_host.get(channel.b2b_base_url, [])
        total = len(channel.account_ids)
        covered = total - len(channel_uncovered)
        print(f"{channel.name}: {covered}/{total} accounts covered")
        if channel_uncovered:
            any_uncovered = True
            print(f"    uncovered: {', '.join(channel_uncovered)}")
    if failed_keys:
        print(
            f"FAILED KEYS: {len(failed_keys)} key(s) failed their visibility probe "
            f"and could not be used: {', '.join(sorted(failed_keys))}"
        )
    return 1 if (any_uncovered or failed_keys) else 0


def _report_abort(system: str, exc: RossumError | B2bError) -> int:
    """Turn an aborting RossumError/B2bError into one clear stderr line
    instead of a raw traceback.

    An expired or invalid token/key is the single most common operational
    failure for this tool, and a 40-line traceback buries the actionable
    part at the bottom. HTTP 401/403 is the same class of failure as
    missing credentials (which already exits 2): the token or key was
    presented but REJECTED, not merely absent. Any other aborting error
    exits 1, with the error text printed as-is on one line -- this also
    keeps a certificate-verification message's own SSL_CERT_FILE guidance
    intact rather than flattening it into something generic.
    """
    text = str(exc)
    if "HTTP 401" in text or "HTTP 403" in text:
        print(
            f"{system}: the token or key appears invalid or expired "
            f"(HTTP 401/403). {text}",
            file=sys.stderr,
        )
        return 2
    print(f"{system} request failed: {text}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.init_credentials is not None:
        # Deliberately the very first thing main() does: no window, no
        # --ui-host, no token is needed to write a template, and this must
        # exit before any of those are even checked.
        path = Path(args.init_credentials).expanduser()
        try:
            init_credentials(path)
        except CredentialsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Wrote a credentials template to {path}")
        print("Fill in the --PASTE...HERE-- placeholders yourself, then run the "
              "reconciliation again -- never paste keys into a chat with an agent.")
        return 0

    now = datetime.now(timezone.utc)
    try:
        since, until = _window(args, now)
    except ValueError as exc:
        print(f"invalid window: {exc}", file=sys.stderr)
        return 2

    # Credentials resolution: an explicit --credentials path wins outright;
    # otherwise the default path is used only if it already exists;
    # otherwise environment variables, exactly as before this flag existed.
    # Whichever source wins is used WHOLESALE -- see credentials.py's
    # docstring for why a file is never partially trusted and never falls
    # through to the environment once selected.
    cred_path = _credentials_source_path(args)
    if cred_path is not None:
        try:
            creds = load_credentials_file(cred_path)
        except CredentialsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        token = creds.token
        keys = creds.keys
        if not keys:
            print(
                f"credentials file at {cred_path}: no usable B2Brouter key -- every "
                "entry under b2brouter.keys is still a --PASTE placeholder, or none "
                "was added",
                file=sys.stderr,
            )
            return 2
        base_url = args.base_url or creds.base_url or DEFAULT_BASE_URL
        ui_host = args.ui_host or creds.ui_host
        pinned_api_version = args.b2b_api_version or creds.api_version
    else:
        token = os.environ.get("ROSSUM_TOKEN")
        if not token:
            print("ROSSUM_TOKEN is not set", file=sys.stderr)
            return 2
        keys = collect_keys(dict(os.environ))
        if not keys:
            print(f"no B2Brouter key found ({KEY_ENV_PREFIX}...)", file=sys.stderr)
            return 2
        base_url = args.base_url or DEFAULT_BASE_URL
        ui_host = args.ui_host
        pinned_api_version = args.b2b_api_version

    # --b2b-api-version is choice-restricted by argparse, so an invalid
    # value can only ever arrive via a credentials file's free-text
    # b2brouter.api_version -- checked here, once, rather than trusting the
    # file the way base_url/ui_host are (those have no "wrong" value; a
    # bogus API version would otherwise surface much later as an opaque
    # invalid_api_version from B2Brouter itself).
    if pinned_api_version is not None and pinned_api_version not in (
        LEGACY_API_VERSION, NEW_API_VERSION,
    ):
        print(
            f"invalid B2Brouter API version {pinned_api_version!r} "
            f"(known: {LEGACY_API_VERSION}, {NEW_API_VERSION})",
            file=sys.stderr,
        )
        return 2

    if not ui_host:
        print(
            "--ui-host is required (pass --ui-host, or supply rossum.ui_host in a "
            "credentials file -- see --init-credentials)",
            file=sys.stderr,
        )
        return 2

    # None unless the operator explicitly opted in with --relax-x509-strict.
    # Passed to BOTH clients below (never just one) -- another organization's
    # Rossum host can sit behind an intercepting proxy just as easily as
    # B2Brouter's. When it is None, neither client is given a context kwarg
    # at all, so the no-flag path is byte-for-byte unchanged.
    ssl_context = build_relaxed_x509_ssl_context() if args.relax_x509_strict else None
    rossum_kwargs = {} if ssl_context is None else {"ssl_context": ssl_context}
    b2b_client_factory = (
        B2brouterClient if ssl_context is None
        else functools.partial(B2brouterClient, ssl_context=ssl_context)
    )

    # Scoped narrowly to discovery and the visibility probe: a RossumError or
    # B2bError escaping THESE calls means the whole run cannot proceed, so it
    # gets the plain-language treatment below. The per-channel reconciliation
    # loop below is deliberately OUTSIDE this try -- a B2bError from a single
    # account's received_invoices() is already caught inside
    # reconcile_channel's own per-account loop (producing an UNVERIFIED_SOURCE
    # row and continuing), and must never be intercepted here as if the whole
    # run had aborted.
    try:
        rossum = RossumClient(token, base_url, **rossum_kwargs)
        channels = select_channels(discover_channels(rossum.list_hooks()), args.channel)
        if not channels:
            print("no e-invoice importer hooks found in this organization", file=sys.stderr)
            return 2

        if args.show_discovery:
            for channel in channels:
                state = "active" if channel.active else "INACTIVE"
                print(f"hook {channel.hook_id} [{state}] {channel.name}: "
                      f"queues={list(channel.queue_ids)} accounts={len(channel.account_ids)} "
                      f"base={channel.b2b_base_url}")
            return 0

        # client_factory is passed explicitly (rather than relying on
        # build_client_resolver's default) so that a caller who patches the
        # B2brouterClient name in THIS module (e.g. tests) is respected -- a
        # default parameter value is bound once at function-definition time
        # and would otherwise keep referencing the original class forever.
        # b2b_client_factory itself was built from the (possibly monkeypatched)
        # B2brouterClient name above, at main()'s own call time, so this still
        # holds.
        _mapping, uncovered_by_host, get_b2b_for_account, failed_keys = build_client_resolver(
            channels, keys, client_factory=b2b_client_factory,
            pinned_api_version=pinned_api_version,
        )
        if failed_keys:
            # build_client_resolver already warned per-key at the moment of
            # failure; this line is the single, easy-to-grep summary so the
            # operator sees which variable(s) to fix even in a long run.
            print(
                f"WARNING: {len(failed_keys)} B2Brouter key(s) failed their "
                f"visibility probe and were skipped: {', '.join(sorted(failed_keys))}",
                file=sys.stderr,
            )

        if args.check_coverage:
            # Reuses build_client_resolver's own visibility probe -- no
            # separate coverage-checking logic, and no invoice listing call
            # is ever made on this path (build_client_resolver only calls
            # visible_account_ids).
            return check_coverage(channels, uncovered_by_host, failed_keys)
    except RossumError as exc:
        return _report_abort("Rossum", exc)
    except B2bError as exc:
        return _report_abort("the invoicing network", exc)

    all_rows: list[Row] = []
    all_failed: dict[str, list[str]] = {}
    for channel in channels:
        if not channel.active:
            print(f"WARNING: hook {channel.hook_id} ({channel.name}) is INACTIVE — "
                  f"its invoices are not being imported at all", file=sys.stderr)
        print(f"{channel.name}: {since:%Y-%m-%d} → {until:%Y-%m-%d}")
        # Host-scoped: an account id uncovered on some OTHER channel's host must
        # never block this channel, even if the id string happens to match.
        channel_uncovered = set(uncovered_by_host.get(channel.b2b_base_url, []))
        rows, failed = reconcile_channel(
            channel,
            get_b2b_for_account(channel),
            rossum,
            since=since,
            until=until,
            now=now,
            grace_minutes=args.grace_minutes,
            uncovered=channel_uncovered,
            ui_host=ui_host,
            fallback_cap=FALLBACK_LOOKUP_CAP,
            attribution_cap=ATTRIBUTION_LOOKUP_CAP,
            ack_cap=ACK_LOOKUP_CAP,
            duplicate_verify_cap=DUPLICATE_VERIFY_CAP,
        )
        all_rows.extend(rows)
        if failed:
            all_failed[channel.name] = failed
        for note, count in sorted(summarize(rows).items()):
            print(f"    {count:6d}  {note}")

    written = [r for r in all_rows
               if not args.only_exceptions or not r.note.startswith("ok")]
    write_csv(written, args.out)
    print(f"\n{len(written)} rows → {args.out}")

    # I2(b): an account in `all_failed` may be there for two different
    # reasons -- it could genuinely not be enumerated (uncovered, or a
    # B2bError), or a Rossum-side id inside the window, matched by no listed
    # invoice, was traced directly to it by a B2Brouter lookup. The second is
    # not an enumeration failure -- the account may have listed plenty -- and
    # telling an operator "could not enumerate" for it is simply wrong. Each
    # UNVERIFIED_SOURCE row's own reason (visible per-account above and in
    # the CSV) is the ground truth for which one applies.
    unverified_reason = {
        (row.channel, row.account): row.b2b_state
        for row in all_rows if row.note == "UNVERIFIED_SOURCE"
    }
    for name, accounts in all_failed.items():
        contradicted = [
            a for a in accounts
            if unverified_reason.get((name, a), "").startswith(CONTRADICTED_REASON_PREFIX)
        ]
        could_not_enumerate = [a for a in accounts if a not in contradicted]
        if could_not_enumerate:
            print(f"INCOMPLETE: {name} could not enumerate {len(could_not_enumerate)} "
                  f"account(s): {', '.join(could_not_enumerate)}", file=sys.stderr)
        if contradicted:
            print(
                f"INCOMPLETE: {name} {len(contradicted)} account(s) enumerated fine but "
                "had at least one Rossum e-invoice id inside the window traced directly "
                f"to them by a B2Brouter lookup, so they are unverified, not failed: "
                f"{', '.join(contradicted)}",
                file=sys.stderr,
            )
    # Counted over ALL rows, not only the ones written: --only-exceptions
    # changes what the CSV holds, never what the exit code means.
    # ACTIONABLE_EXEMPT_NOTES, not CLEAN_NOTES: DELETED and DELETED_AS_DUPLICATE
    # are exempt from THIS count even though they are still "exception" rows
    # for acked_at-backfill purposes -- see ACTIONABLE_EXEMPT_NOTES' own
    # comment above. By the user's explicit decision neither label implies
    # an action is needed, whether or not the row's DELETED->DELETED_AS_DUPLICATE
    # upgrade was ever actually verified.
    actionable = sum(1 for r in all_rows if r.note not in ACTIONABLE_EXEMPT_NOTES)
    if actionable or all_failed:
        print(f"EXIT 1: {actionable} row(s) need action, "
              f"{len(all_failed)} incomplete channel(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
