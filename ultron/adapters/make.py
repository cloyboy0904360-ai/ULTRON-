"""Make (Integromat) integration adapter (API v2).

The Make API is region-scoped; the base URL is configurable via
``base_url`` (default: the EU1 zone).
"""

from __future__ import annotations

from typing import Any

from .base import BaseAdapter


class MakeAdapter(BaseAdapter):
    name = "make"

    def __init__(self, *, base_url: str = "https://eu1.make.com/api/v2", **kwargs: Any):
        super().__init__(**kwargs)
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._require_configured()}"}

    def health_check(self) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/users/me", headers=self._headers(),
            action="health_check",
        )

    def check_permissions(self) -> dict[str, Any]:
        me = self.health_check()
        return {
            "integration": self.name,
            "user": (me.get("user") or {}).get("email"),
        }

    def list_scenarios(self, team_id: int) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/scenarios?teamId={team_id}",
            headers=self._headers(), action="list_scenarios",
        )
