from __future__ import annotations

import pytest

from ultron.auth import Authenticator
from ultron.config import Settings
from ultron.errors import ConfigError


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
    with pytest.raises(ConfigError) as error:
        Settings.load({"ULTRON_ENV": "production"})
    assert any("ULTRON_API_KEYS is required" in item for item in error.value.details["problems"])
