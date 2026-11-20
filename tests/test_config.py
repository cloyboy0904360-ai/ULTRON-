from __future__ import annotations

from ultron.auth import Authenticator
from ultron.config import Settings


def test_api_key_entries_split_on_semicolon_and_preserve_comma_scopes():
    settings = Settings.load({
        "ULTRON_ENV": "production",
        "ULTRON_API_KEYS": "read-key:operations:read;writer:operations:write,revenue:write",
    })

    assert settings.api_keys == (
        "read-key:operations:read",
        "writer:operations:write,revenue:write",
    )
    auth = Authenticator(settings.api_keys)
    reader = auth.authenticate("read-key")
    writer = auth.authenticate("writer")

    assert reader.can("operations:read")
    assert not reader.can("operations:write")
    assert writer.can("operations:write")
    assert writer.can("revenue:write")
    assert not writer.can("operations:read")


def test_production_configuration_requires_at_least_one_api_key():
    try:
        Settings.load({"ULTRON_ENV": "production"})
    except Exception as exc:
        assert "ULTRON_API_KEYS is required" in str(exc)
    else:
        raise AssertionError("production without API keys must fail")
