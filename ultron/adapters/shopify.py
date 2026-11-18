"""Shopify Admin API adapter.

The store identity (shop domain) is verified before any catalogue
operation. SKU validation looks up variants by SKU and returns the real
variant records — it never assumes a SKU exists.
"""

from __future__ import annotations

from typing import Any

from ..errors import NotFoundError, ValidationError
from .base import BaseAdapter

API_VERSION = "2024-10"


class ShopifyAdapter(BaseAdapter):
    name = "shopify"

    def __init__(self, *, shop: str | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.shop = shop

    def _headers(self) -> dict[str, str]:
        return {"X-Shopify-Access-Token": self._require_configured()}

    def _base(self) -> str:
        if not self.shop:
            raise ValidationError("No Shopify shop domain configured")
        shop = self.shop.strip()
        if not shop.endswith(".myshopify.com"):
            shop = f"{shop}.myshopify.com"
        return f"https://{shop}/admin/api/{API_VERSION}"

    def verify_store(self) -> dict[str, Any]:
        """Return the actual store identity; raises if unreachable."""
        payload = self._request(
            "GET", f"{self._base()}/shop.json", headers=self._headers(),
            action="verify_store",
        )
        return payload.get("shop", {})

    def health_check(self) -> dict[str, Any]:
        return self.verify_store()

    def check_permissions(self) -> dict[str, Any]:
        shop = self.verify_store()
        return {
            "integration": self.name,
            "shop": shop.get("myshopify_domain"),
            "plan": shop.get("plan_name"),
        }

    def list_products(self, *, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", f"{self._base()}/products.json?limit={min(limit, 250)}",
            headers=self._headers(), action="list_products",
        )
        return payload.get("products", [])

    def get_product(self, product_id: int) -> dict[str, Any]:
        payload = self._request(
            "GET", f"{self._base()}/products/{product_id}.json",
            headers=self._headers(), action="get_product",
        )
        product = payload.get("product")
        if not product:
            raise NotFoundError(f"Shopify product {product_id} not found")
        return product

    def find_variant_by_sku(self, sku: str) -> dict[str, Any] | None:
        """Validate a SKU against the live catalogue.

        Returns the matching variant, or None when no variant carries
        that SKU. Never fabricates a result.
        """
        if not sku.strip():
            raise ValidationError("SKU must not be empty")
        for product in self.list_products(limit=250):
            for variant in product.get("variants", []):
                if variant.get("sku") == sku:
                    return variant
        return None
