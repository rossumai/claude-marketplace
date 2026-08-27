from discovery import Channel, discover_channels

# A channel is keyed on the PRESENCE of B2Brouter accounts in a hook's
# settings, never on the hook's extension URL -- that URL is an
# implementation detail that has already changed once in production
# (importer -> dispatcher) and matching it fails silently, discovering zero
# channels and reporting "nothing to reconcile". Hooks 15 and 16 below pin
# the regression: different (or absent) config.url, still discovered.
HOOKS = [
    {
        "id": 11,
        "name": "E-invoicing B2Brouter - Region A",
        "active": True,
        "queues": ["https://rossum.invalid/api/v1/queues/111"],
        "config": {"url": "https://x.einvoice-importer.rossum-ext.app/"},
        "settings": {
            "b2b_router_account_id": ["900001", "900002"],
            "b2b_router_base_url": "https://app.example-router.net",
        },
    },
    {   # not an importer: must be ignored
        "id": 13,
        "name": "Business Rules",
        "active": True,
        "queues": ["https://rossum.invalid/api/v1/queues/333"],
        "config": {"url": "https://x.some-other-extension.rossum-ext.app/"},
        "settings": {"checks": []},
    },
    {   # importer URL but no account list: nothing to poll, must be ignored
        "id": 14,
        "name": "E-invoicing half-configured",
        "active": True,
        "queues": [],
        "config": {"url": "https://x.einvoice-importer.rossum-ext.app/"},
        "settings": {},
    },
    {   # dispatcher URL (production case): must be discovered via accounts alone
        "id": 15,
        "name": "E-invoicing B2Brouter - Dispatcher",
        "active": True,
        "queues": ["https://rossum.invalid/api/v1/queues/444"],
        "config": {"url": "https://x.einvoice-dispatcher.rossum-ext.app/api/v1/dispatch"},
        "settings": {"b2b_router_account_id": ["900004"]},
    },
    {   # no config key at all: must still be discovered via accounts
        "id": 16,
        "name": "E-invoicing B2Brouter - No Config",
        "active": True,
        "queues": ["https://rossum.invalid/api/v1/queues/555"],
        "settings": {"b2b_router_account_id": ["900005"]},
    },
]


def test_discovers_by_account_presence_not_by_url_and_parses_the_fields():
    channels = discover_channels(HOOKS)
    # Hooks 13 (no accounts at all) and 14 (importer URL, empty settings) are
    # excluded; 11, 15, 16 are kept despite having three different config.url
    # shapes (a matched importer URL, a dispatcher URL, and no config key).
    assert [c.hook_id for c in channels] == [11, 15, 16]
    assert channels[0] == Channel(
        hook_id=11,
        name="E-invoicing B2Brouter - Region A",
        queue_ids=(111,),
        account_ids=("900001", "900002"),
        b2b_base_url="https://app.example-router.net",
        active=True,
    )


def test_discovers_dispatcher_url_hooks():
    """Regression: the production hostname change (importer -> dispatcher)
    must not blind discovery -- accounts alone identify the channel."""
    channels = discover_channels(HOOKS)
    dispatcher = next(c for c in channels if c.hook_id == 15)
    assert dispatcher.account_ids == ("900004",)


def test_discovers_hooks_with_no_config_key():
    """Regression: a hook with no config.url at all is still discovered."""
    channels = discover_channels(HOOKS)
    no_config = next(c for c in channels if c.hook_id == 16)
    assert no_config.account_ids == ("900005",)
