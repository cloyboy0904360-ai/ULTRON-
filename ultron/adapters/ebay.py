"""eBay integration adapter (Sell Inventory API, OAuth client credentials)."""

from __future__ import annotations

import base64
from typing import Any

from ..errors import IntegrationUnavailableError, NotFoundError, ValidationError
from .base import BaseAdapter


class EbayAdapter(BaseAdapter):
    name = "ebay"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        sandbox: bool = False,
        **kwargs: Any,
    ):
        super().__init__(token=None, **kwargs)
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
        )
        self._access_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _require_configured(self) -> str:  # type: ignore[override]
        if not self.configured:
            raise IntegrationUnavailableError(
                "eBay credentials are not configured; integration is BLOCKED",
                integration=self.name,
            )
        return self.client_id or ""

    def _obtain_token(self) -> str:
        if self._access_token:
            return self._access_token
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        payload = self._request(
            "POST",
            f"{self.base_url}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            raw_body=(
                b"grant_type=client_credentials"
                b"&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope"
            ),
            action="oauth_token",
        )
        token = payload.get("access_token")
        if not token:
            raise ValidationError("eBay token response contained no access_token")
        self._access_token = token
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._obtain_token()}"}

    def health_check(self) -> dict[str, Any]:
        self._obtain_token()
        return {"integration": self.name, "token_acquired": True}

    def get_inventory_item(self, sku: str) -> dict[str, Any]:
        if not sku.strip():
            raise ValidationError("SKU must not be empty")
        payload = self._request(
            "GET",
            f"{self.base_url}/sell/inventory/v1/inventory_item/{sku}",
            headers=self._headers(), action="get_inventory_item",
        )
        if not payload:
            raise NotFoundError(f"eBay inventory item for SKU '{sku}' not found")
        return payload

    def validate_sku(self, sku: str) -> bool:
        try:
            self.get_inventory_item(sku)
        except NotFoundError:
            return False
        return True
