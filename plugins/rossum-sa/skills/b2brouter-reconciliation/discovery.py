"""Discover reconciliation scope from a Rossum organization's own hooks.

Nothing here is hardcoded per customer. A channel is any hook that has
B2Brouter accounts configured in settings.b2b_router_account_id; the hook
itself carries the queues and the account ids, so the scope cannot go stale the
way a prepared configuration file does.

The extension URL is NOT used to identify channels. The dispatcher hostname
has already changed once (importer → dispatcher) and matching it fails
silently when production changes again, making the tool blind to the very
invoices it exists to catch. Account presence alone identifies a channel.
"""

from dataclasses import dataclass

DEFAULT_B2B_BASE = "https://app.b2brouter.net"


@dataclass(frozen=True)
class Channel:
    hook_id: int
    name: str
    queue_ids: tuple[int, ...]
    account_ids: tuple[str, ...]
    b2b_base_url: str
    active: bool


def _queue_id(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def discover_channels(hooks: list[dict]) -> list[Channel]:
    """Every hook that has B2Brouter accounts configured, in hook order."""
    channels: list[Channel] = []
    for hook in hooks:
        settings = hook.get("settings") or {}
        accounts = settings.get("b2b_router_account_id") or []
        if not accounts:
            continue
        channels.append(
            Channel(
                hook_id=hook["id"],
                name=hook["name"],
                queue_ids=tuple(_queue_id(q) for q in hook.get("queues") or []),
                account_ids=tuple(str(a) for a in accounts),
                b2b_base_url=settings.get("b2b_router_base_url") or DEFAULT_B2B_BASE,
                active=bool(hook.get("active")),
            )
        )
    return channels


def select_channels(channels: list[Channel], selector: str | None) -> list[Channel]:
    """Filter by hook id or case-insensitive name substring. None selects all."""
    if selector is None:
        return list(channels)
    chosen = [
        c for c in channels
        if str(c.hook_id) == selector or selector.lower() in c.name.lower()
    ]
    if not chosen:
        raise KeyError(f"no channel matches {selector!r}")
    return chosen


def map_accounts_to_keys(
    channels: list[Channel], visibility: dict[str, set[str]]
) -> tuple[dict[str, str], list[str]]:
    """Decide which supplied key to use per account.

    `visibility` maps a key label to the account ids that key can see, as
    reported by B2Brouter itself. Accounts no key can see are returned separately
    so the caller can mark them unverified instead of quietly skipping them.
    """
    mapping: dict[str, str] = {}
    uncovered: list[str] = []
    for channel in channels:
        for account_id in channel.account_ids:
            if account_id in mapping:
                continue
            owner = next(
                (label for label, seen in visibility.items() if account_id in seen),
                None,
            )
            if owner is None:
                if account_id not in uncovered:
                    uncovered.append(account_id)
            else:
                mapping[account_id] = owner
    return mapping, uncovered
