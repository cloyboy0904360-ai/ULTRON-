"""Environment configuration with validation.

All settings come from environment variables. ``Settings.load()`` raises
``ConfigError`` listing every problem found, so misconfiguration fails
fast at startup instead of surfacing mid-operation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import ConfigError

# Integration credentials are optional: when absent the corresponding
# adapter reports BLOCKED instead of crashing the whole system.
_INTEGRATION_VARS = (
    "GITHUB_TOKEN",
    "ASANA_ACCESS_TOKEN",
    "ASANA_WORKSPACE_GID",
    "MAKE_API_TOKEN",
    "SHOPIFY_SHOP",
    "SHOPIFY_ACCESS_TOKEN",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
)


@dataclass(frozen=True)
class Settings:
    database_path: str = "ultron.db"
    api_keys: tuple[str, ...] = ()
    log_level: str = "INFO"
    environment: str = "development"
    http_timeout_seconds: float = 15.0
    max_retries: int = 3
    integrations: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        problems: list[str] = []

        database_path = env.get("ULTRON_DATABASE_PATH", "ultron.db").strip()
        if not database_path:
            problems.append("ULTRON_DATABASE_PATH must not be empty")

        api_keys = tuple(
            key.strip()
            for key in env.get("ULTRON_API_KEYS", "").split(",")
            if key.strip()
        )
        environment = env.get("ULTRON_ENV", "development").strip().lower()
        if environment not in {"development", "test", "staging", "production"}:
            problems.append(
                "ULTRON_ENV must be one of development|test|staging|production"
            )
        if environment == "production" and not api_keys:
            problems.append(
                "ULTRON_API_KEYS is required in production (comma-separated API keys)"
            )

        log_level = env.get("ULTRON_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            problems.append("ULTRON_LOG_LEVEL is not a valid logging level")

        try:
            timeout = float(env.get("ULTRON_HTTP_TIMEOUT_SECONDS", "15"))
            if timeout <= 0:
                raise ValueError
        except ValueError:
            problems.append("ULTRON_HTTP_TIMEOUT_SECONDS must be a positive number")
            timeout = 15.0

        try:
            max_retries = int(env.get("ULTRON_MAX_RETRIES", "3"))
            if max_retries < 0:
                raise ValueError
        except ValueError:
            problems.append("ULTRON_MAX_RETRIES must be a non-negative integer")
            max_retries = 3

        if problems:
            raise ConfigError(
                "Invalid ULTRON configuration", details={"problems": problems}
            )

        integrations = {name: env[name] for name in _INTEGRATION_VARS if env.get(name)}
        return cls(
            database_path=database_path,
            api_keys=api_keys,
            log_level=log_level,
            environment=environment,
            http_timeout_seconds=timeout,
            max_retries=max_retries,
            integrations=integrations,
        )
