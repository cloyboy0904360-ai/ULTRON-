"""Safe capability registry for external integrations.

The registry reports what is configured without exposing credential values or
pretending that configuration alone proves a live connection. Health probes
are optional and injected by the caller, which keeps this module testable and
prevents accidental network calls during startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


CapabilityProbe = Callable[[], tuple[str, str | None]]


@dataclass(frozen=True)
class Capability:
    name: str
    required_env: tuple[str, ...] = ()
    configured: bool = False
    probe: CapabilityProbe | None = None


class CapabilityRegistry:
    def __init__(self, capabilities: tuple[Capability, ...] = ()) -> None:
        self._capabilities: dict[str, Capability] = {c.name: c for c in capabilities}

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "CapabilityRegistry":
        definitions = {
            "github": ("GITHUB_TOKEN",),
            "google_drive": ("GOOGLE_DRIVE_TOKEN",),
            "google_sheets": ("GOOGLE_SHEETS_TOKEN",),
            "gmail": ("GMAIL_TOKEN",),
            "asana": ("ASANA_ACCESS_TOKEN", "ASANA_WORKSPACE_GID"),
            "hubspot": ("HUBSPOT_ACCESS_TOKEN",),
            "stripe": ("STRIPE_SECRET_KEY",),
            "ebay": ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"),
            "facebook": ("FACEBOOK_ACCESS_TOKEN",),
            "youtube": ("YOUTUBE_ACCESS_TOKEN",),
            "notion": ("NOTION_TOKEN",),
            "browser_use": ("BROWSER_USE_API_KEY",),
            "composio": ("COMPOSIO_API_KEY",),
        }
        caps = tuple(
            Capability(name=name, required_env=vars_, configured=all(bool(env.get(v)) for v in vars_))
            for name, vars_ in definitions.items()
        )
        return cls(caps)

    def register(self, capability: Capability) -> None:
        if not capability.name.strip():
            raise ValueError("capability name is required")
        self._capabilities[capability.name] = capability

    def snapshot(self) -> list[dict[str, object]]:
        result = []
        for capability in self._capabilities.values():
            if not capability.configured:
                result.append({"integration": capability.name, "status": "BLOCKED", "reason": "credentials not configured"})
                continue
            if capability.probe is None:
                result.append({"integration": capability.name, "status": "CONFIGURED", "reason": "live health not verified"})
                continue
            try:
                status, reason = capability.probe()
            except Exception as exc:  # noqa: BLE001 - registry must not crash readiness
                status, reason = "DEGRADED", str(exc)
            result.append({"integration": capability.name, "status": status, "reason": reason})
        return result

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise KeyError(f"Unknown integration capability: {name}") from exc
