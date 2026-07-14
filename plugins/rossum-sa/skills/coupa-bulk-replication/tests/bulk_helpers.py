"""Shared helpers for the coupa-bulk-replication test suite."""
import json


def write_config(tmp_path, **kw):
    """Write a minimal valid config JSON; keyword args override defaults."""
    cfg = {
        "coupa": {
            "base_url": kw.get("base_url", "https://x.coupahost.com"),
            "client_id": kw.get("client_id", "cid"),
            "client_secret": kw.get("client_secret", "sec"),
        },
        "rossum": {
            "api_url": "https://o.rossum.app/api/v1",
            "ds_url": "https://o.rossum.app/svc/data-storage/api/v1",
            "token": kw.get("token", "tok"),
        },
        "ds_batch_size": kw.get("ds_batch_size", 5000),
        "datasets": kw.get("datasets", {
            "users": {"endpoint": "api/users", "collection": "users",
                      "id_key": "id", "scope": "s", "fields": ["id"]},
        }),
    }
    path = tmp_path / "coupa_bulk_import.config.json"
    path.write_text(json.dumps(cfg))
    return path
