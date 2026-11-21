"""Application health and capability checks."""

from __future__ import annotations

import os
from typing import Any

from .adapters.base import BaseAdapter
from .db import Database
from .integrations import CapabilityRegistry


def check_health(db: Database, adapters: list[BaseAdapter] | None = None) -> dict[str, Any]:
    """Liveness: process is up and the database answers."""
    try:
        db.query_one("SELECT 1 AS ok")
        db_ok = True
    except Exception:  # noqa: BLE001 - health check must never raise
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
    }


def check_readiness(
    db: Database, adapters: list[BaseAdapter] | None = None
) -> dict[str, Any]:
    """Readiness: database, configured adapters and secret-safe capabilities."""
    health = check_health(db, adapters)
    integrations = [adapter.status() for adapter in adapters or []]
    blocked = [i for i in integrations if i["status"] != "CONNECTED"]
    capabilities = CapabilityRegistry.from_environment(os.environ).snapshot()
    return {
        **health,
        "ready": health["database"] == "ok",
        "integrations": integrations,
        "integrations_blocked": [i["integration"] for i in blocked],
        "capabilities": capabilities,
    }
