"""Stripe integration adapter.

Payment state is always read from Stripe before it is treated as real:
an authorised PaymentIntent is never reported as settled funds.
"""

from __future__ import annotations

from typing import Any

from .base import BaseAdapter


class StripeAdapter(BaseAdapter):
    name = "stripe"
    base_url = "https://api.stripe.com/v1"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._require_configured()}"}

    def health_check(self) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/balance", headers=self._headers(),
            action="health_check",
        )

    def check_permissions(self) -> dict[str, Any]:
        balance = self.health_check()
        return {
            "integration": self.name,
            "livemode": balance.get("livemode"),
        }

    def get_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/payment_intents/{payment_intent_id}",
            headers=self._headers(), action="get_payment_intent",
        )

    def get_charge(self, charge_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/charges/{charge_id}",
            headers=self._headers(), action="get_charge",
        )

    def is_charge_settled(self, charge_id: str) -> bool:
        """A charge counts as received funds only when Stripe reports it
        succeeded, captured and paid out-eligible (status 'succeeded')."""
        charge = self.get_charge(charge_id)
        return bool(
            charge.get("status") == "succeeded"
            and charge.get("paid") is True
            and charge.get("captured") is True
        )
