"""ULTRON revenue engine.

Implements the pipeline:

    IDENTIFY -> QUALIFY -> CONTACT -> CONVERT -> PAYMENT -> VERIFY
    -> DELIVER -> COMPLETE -> GET_PAID -> STABILISE -> AUTOMATE
    -> NEXT_OPPORTUNITY

Financial invariants enforced here:
- Potential revenue (estimates, proposals, issued invoices) is kept
  strictly separate from received revenue (settled payments).
- An unpaid invoice is never a sale received.
- A payment authorisation is never settled bank funds; settlement
  requires verification evidence.
- An order is never delivered without delivery evidence.
- Refunds can never exceed the settled amount of a payment.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from ..errors import (
    DuplicateError,
    EvidenceRequiredError,
    NotFoundError,
    StateTransitionError,
    ValidationError,
)
from ..logging_config import get_logger
from .ledger import Ledger
from .models import (
    FulfilmentStatus,
    InvoiceStatus,
    OrderStatus,
    PaymentStatus,
    PipelineStage,
    ProposalStatus,
)

log = get_logger("ultron.revenue.engine")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _stage_index(stage: PipelineStage) -> int:
    return list(PipelineStage).index(stage)


class RevenueEngine:
    def __init__(self, db: Database):
        self.db = db
        self.ledger = Ledger(db)

    # --------------------------------------------------------- prospects
    def create_prospect(
        self,
        *,
        name: str,
        contact: str | None = None,
        source: str | None = None,
        estimated_value_pence: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationError("Prospect name is required")
        if estimated_value_pence is not None and estimated_value_pence < 0:
            raise ValidationError("Estimated value cannot be negative")
        prospect_id = _new_id()
        now = _now()
        self.db.execute(
            """INSERT INTO prospects
               (id, name, contact, source, stage, estimated_value_pence,
                created_at, updated_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                prospect_id, name.strip(), contact, source,
                PipelineStage.IDENTIFY.value, estimated_value_pence, now, now, notes,
            ),
        )
        return self.get_prospect(prospect_id)

    def get_prospect(self, prospect_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM prospects WHERE id = ?", (prospect_id,))
        if not row:
            raise NotFoundError(f"Prospect {prospect_id} not found")
        return row

    def advance_prospect(
        self, prospect_id: str, target: PipelineStage
    ) -> dict[str, Any]:
        """Move a prospect forward through the pipeline (never backwards).

        Stage-specific gates are enforced; see module docstring.
        """
        prospect = self.get_prospect(prospect_id)
        current = PipelineStage(prospect["stage"])
        if _stage_index(target) <= _stage_index(current):
            raise StateTransitionError(
                f"Pipeline cannot move from {current.value} back to {target.value}"
            )
        self._check_pipeline_gate(prospect, target)
        self.db.execute(
            "UPDATE prospects SET stage = ?, updated_at = ? WHERE id = ?",
            (target.value, _now(), prospect_id),
        )
        log.info(
            "prospect advanced",
            extra={"context": {"prospect_id": prospect_id, "stage": target.value}},
        )
        return self.get_prospect(prospect_id)

    def _check_pipeline_gate(
        self, prospect: dict[str, Any], target: PipelineStage
    ) -> None:
        pid = prospect["id"]
        if _stage_index(target) >= _stage_index(PipelineStage.CONVERT):
            if _stage_index(PipelineStage(prospect["stage"])) < _stage_index(PipelineStage.CONVERT):
                customer = self.db.query_one(
                    "SELECT id FROM customers WHERE prospect_id = ?", (pid,)
                )
                if not customer:
                    raise StateTransitionError(
                        "Prospect must be converted to a customer first "
                        "(use convert_prospect)"
                    )
        if _stage_index(target) >= _stage_index(PipelineStage.PAYMENT):
            if not self._orders_for_prospect(pid):
                raise StateTransitionError(
                    "Cannot enter PAYMENT: no order exists for this prospect"
                )
        if _stage_index(target) >= _stage_index(PipelineStage.VERIFY):
            payment = self._latest_payment_for_prospect(pid)
            if not payment or payment["status"] not in {
                PaymentStatus.AUTHORISED.value,
                PaymentStatus.SETTLED.value,
            }:
                raise StateTransitionError(
                    "Cannot enter VERIFY: no authorised payment exists"
                )
        if _stage_index(target) >= _stage_index(PipelineStage.DELIVER):
            payment = self._latest_payment_for_prospect(pid)
            if not payment or payment["status"] != PaymentStatus.SETTLED.value:
                raise StateTransitionError(
                    "Cannot DELIVER before payment is SETTLED (verified funds)"
                )
        if _stage_index(target) >= _stage_index(PipelineStage.COMPLETE):
            for order in self._orders_for_prospect(pid):
                if order["status"] not in {
                    OrderStatus.DELIVERED.value,
                    OrderStatus.COMPLETED.value,
                }:
                    raise StateTransitionError(
                        "Cannot COMPLETE: an order is not yet delivered with evidence"
                    )
        if _stage_index(target) >= _stage_index(PipelineStage.GET_PAID):
            for invoice in self._invoices_for_prospect(pid):
                if invoice["status"] != InvoiceStatus.PAID.value:
                    raise StateTransitionError(
                        "Cannot GET_PAID: an invoice is not fully settled"
                    )

    def convert_prospect(self, prospect_id: str) -> dict[str, Any]:
        """Convert a prospect into a customer and move to CONVERT."""
        prospect = self.get_prospect(prospect_id)
        existing = self.db.query_one(
            "SELECT * FROM customers WHERE prospect_id = ?", (prospect_id,)
        )
        if existing:
            raise DuplicateError(
                "Prospect is already converted",
                details={"customer_id": existing["id"]},
            )
        customer_id = _new_id()
        self.db.execute(
            "INSERT INTO customers (id, prospect_id, name, contact, created_at) "
            "VALUES (?,?,?,?,?)",
            (customer_id, prospect_id, prospect["name"], prospect["contact"], _now()),
        )
        self.advance_prospect(prospect_id, PipelineStage.CONVERT)
        return self.db.query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))

    # --------------------------------------------------- products & SKUs
    def create_product(
        self, *, name: str, description: str | None = None
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationError("Product name is required")
        product_id = _new_id()
        self.db.execute(
            "INSERT INTO products (id, name, description, created_at) VALUES (?,?,?,?)",
            (product_id, name.strip(), description, _now()),
        )
        return self.db.query_one("SELECT * FROM products WHERE id = ?", (product_id,))

    def create_sku(
        self,
        *,
        product_id: str,
        sku: str,
        unit_price_pence: int,
        unit_cost_pence: int = 0,
    ) -> dict[str, Any]:
        if not self.db.query_one("SELECT id FROM products WHERE id = ?", (product_id,)):
            raise NotFoundError(f"Product {product_id} not found")
        if not sku.strip():
            raise ValidationError("SKU code is required")
        if unit_price_pence < 0 or unit_cost_pence < 0:
            raise ValidationError("Prices and costs cannot be negative")
        if self.db.query_one("SELECT id FROM skus WHERE sku = ?", (sku.strip(),)):
            raise DuplicateError(f"SKU '{sku}' already exists")
        sku_id = _new_id()
        self.db.execute(
            """INSERT INTO skus
               (id, product_id, sku, unit_price_pence, unit_cost_pence, active)
               VALUES (?,?,?,?,?,1)""",
            (sku_id, product_id, sku.strip(), unit_price_pence, unit_cost_pence),
        )
        return self.db.query_one("SELECT * FROM skus WHERE id = ?", (sku_id,))

    # ---------------------------------------------------------- proposals
    def create_proposal(
        self, *, prospect_id: str, amount_pence: int
    ) -> dict[str, Any]:
        self.get_prospect(prospect_id)
        if amount_pence <= 0:
            raise ValidationError("Proposal amount must be positive")
        proposal_id = _new_id()
        now = _now()
        self.db.execute(
            """INSERT INTO proposals
               (id, prospect_id, status, amount_pence, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (proposal_id, prospect_id, ProposalStatus.DRAFT.value, amount_pence, now, now),
        )
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if not row:
            raise NotFoundError(f"Proposal {proposal_id} not found")
        return row

    def set_proposal_status(
        self, proposal_id: str, status: ProposalStatus
    ) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        current = ProposalStatus(proposal["status"])
        allowed = {
            ProposalStatus.DRAFT: {ProposalStatus.SENT, ProposalStatus.EXPIRED},
            ProposalStatus.SENT: {
                ProposalStatus.ACCEPTED,
                ProposalStatus.REJECTED,
                ProposalStatus.EXPIRED,
            },
            ProposalStatus.ACCEPTED: set(),
            ProposalStatus.REJECTED: set(),
            ProposalStatus.EXPIRED: set(),
        }
        if status not in allowed[current]:
            raise StateTransitionError(
                f"Proposal cannot move from {current.value} to {status.value}"
            )
        self.db.execute(
            "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _now(), proposal_id),
        )
        return self.get_proposal(proposal_id)

    # ------------------------------------------------------------- orders
    def create_order(
        self, *, customer_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if not self.db.query_one("SELECT id FROM customers WHERE id = ?", (customer_id,)):
            raise NotFoundError(f"Customer {customer_id} not found")
        if idempotency_key:
            existing = self.db.query_one(
                "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
            )
            if existing:
                raise DuplicateError(
                    "Order with this idempotency key already exists",
                    details={"order_id": existing["id"]},
                )
        order_id = _new_id()
        now = _now()
        self.db.execute(
            """INSERT INTO orders
               (id, idempotency_key, customer_id, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (order_id, idempotency_key, customer_id, OrderStatus.DRAFT.value, now, now),
        )
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not row:
            raise NotFoundError(f"Order {order_id} not found")
        row["items"] = self.db.query_all(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        )
        return row

    def add_order_item(self, *, order_id: str, sku_id: str, quantity: int) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] != OrderStatus.DRAFT.value:
            raise StateTransitionError("Items can only be added to a DRAFT order")
        sku = self.db.query_one("SELECT * FROM skus WHERE id = ?", (sku_id,))
        if not sku:
            raise NotFoundError(f"SKU {sku_id} not found")
        if not sku["active"]:
            raise ValidationError(f"SKU '{sku['sku']}' is inactive")
        if quantity <= 0:
            raise ValidationError("Quantity must be positive")
        item_id = _new_id()
        self.db.execute(
            """INSERT INTO order_items
               (id, order_id, sku_id, quantity, unit_price_pence, unit_cost_pence)
               VALUES (?,?,?,?,?,?)""",
            (item_id, order_id, sku_id, quantity,
             sku["unit_price_pence"], sku["unit_cost_pence"]),
        )
        return self.db.query_one("SELECT * FROM order_items WHERE id = ?", (item_id,))

    def confirm_order(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] != OrderStatus.DRAFT.value:
            raise StateTransitionError("Only DRAFT orders can be confirmed")
        if not order["items"]:
            raise ValidationError("Cannot confirm an order with no items")
        self._set_order_status(order_id, OrderStatus.CONFIRMED)
        for item in order["items"]:
            cost = item["unit_cost_pence"] * item["quantity"]
            if cost:
                self.ledger.record(
                    kind="COST", source_type="sku_cost", source_id=item["id"],
                    amount_pence=cost,
                    note=f"Cost of goods for order {order_id}",
                )
        return self.get_order(order_id)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] not in {OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value}:
            raise StateTransitionError(
                f"Order in status {order['status']} cannot be cancelled"
            )
        if order["status"] == OrderStatus.CONFIRMED.value:
            for item in order["items"]:
                cost = item["unit_cost_pence"] * item["quantity"]
                if cost:
                    self.ledger.record(
                        kind="COST", source_type="sku_cost_reversal",
                        source_id=item["id"], amount_pence=-cost,
                        note=f"Reversal: order {order_id} cancelled",
                    )
        self._set_order_status(order_id, OrderStatus.CANCELLED)
        return self.get_order(order_id)

    # ------------------------------------------------------------ invoices
    def create_invoice(
        self, *, order_id: str, due_at: str | None = None
    ) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] != OrderStatus.CONFIRMED.value:
            raise StateTransitionError(
                "An invoice can only be issued for a CONFIRMED order"
            )
        if self.db.query_one(
            "SELECT id FROM invoices WHERE order_id = ? AND status != 'VOID'",
            (order_id,),
        ):
            raise DuplicateError("Order already has an active invoice")
        amount = sum(i["unit_price_pence"] * i["quantity"] for i in order["items"])
        invoice_id = _new_id()
        self.db.execute(
            """INSERT INTO invoices
               (id, order_id, status, amount_pence, issued_at, due_at)
               VALUES (?,?,?,?,?,?)""",
            (invoice_id, order_id, InvoiceStatus.ISSUED.value, amount, _now(), due_at),
        )
        return self.get_invoice(invoice_id)

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
        if not row:
            raise NotFoundError(f"Invoice {invoice_id} not found")
        return row

    def void_invoice(self, invoice_id: str) -> dict[str, Any]:
        invoice = self.get_invoice(invoice_id)
        if invoice["status"] != InvoiceStatus.ISSUED.value:
            raise StateTransitionError("Only an unpaid ISSUED invoice can be voided")
        self.db.execute(
            "UPDATE invoices SET status = ? WHERE id = ?",
            (InvoiceStatus.VOID.value, invoice_id),
        )
        return self.get_invoice(invoice_id)

    # ------------------------------------------------------------ payments
    def record_payment_authorised(
        self,
        *,
        invoice_id: str,
        provider: str,
        amount_pence: int,
        provider_reference: str,
    ) -> dict[str, Any]:
        """Record a payment authorisation. This is NOT received revenue."""
        invoice = self.get_invoice(invoice_id)
        if invoice["status"] == InvoiceStatus.VOID.value:
            raise StateTransitionError("Cannot take payment against a VOID invoice")
        if amount_pence <= 0:
            raise ValidationError("Payment amount must be positive")
        if not provider_reference.strip():
            raise ValidationError("Provider reference is required")
        if self.db.query_one(
            "SELECT id FROM payments WHERE provider_reference = ?",
            (provider_reference.strip(),),
        ):
            raise DuplicateError(
                "A payment with this provider reference already exists"
            )
        payment_id = _new_id()
        self.db.execute(
            """INSERT INTO payments
               (id, invoice_id, provider, provider_reference, status,
                amount_pence, authorised_at)
               VALUES (?,?,?,?,?,?,?)""",
            (payment_id, invoice_id, provider, provider_reference.strip(),
             PaymentStatus.AUTHORISED.value, amount_pence, _now()),
        )
        return self.get_payment(payment_id)

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        if not row:
            raise NotFoundError(f"Payment {payment_id} not found")
        return row

    def settle_payment(
        self, *, payment_id: str, verification_evidence: str
    ) -> dict[str, Any]:
        """Mark an authorised payment as settled bank funds.

        Requires verification evidence (e.g. provider settlement report
        reference). Only now does revenue enter the ledger.
        """
        if not verification_evidence.strip():
            raise EvidenceRequiredError(
                "Settling a payment requires verification evidence"
            )
        payment = self.get_payment(payment_id)
        if payment["status"] != PaymentStatus.AUTHORISED.value:
            raise StateTransitionError(
                f"Only AUTHORISED payments can settle (got {payment['status']})"
            )
        self.db.execute(
            """UPDATE payments
               SET status = ?, settled_at = ?, verification_evidence = ?
               WHERE id = ?""",
            (PaymentStatus.SETTLED.value, _now(),
             verification_evidence.strip(), payment_id),
        )
        self.ledger.record(
            kind="REVENUE_RECEIVED", source_type="payment", source_id=payment_id,
            amount_pence=payment["amount_pence"],
            note=f"Settled payment for invoice {payment['invoice_id']}",
        )
        self._refresh_invoice_status(payment["invoice_id"])
        return self.get_payment(payment_id)

    def fail_payment(self, *, payment_id: str, reason: str) -> dict[str, Any]:
        payment = self.get_payment(payment_id)
        if payment["status"] != PaymentStatus.AUTHORISED.value:
            raise StateTransitionError(
                f"Only AUTHORISED payments can fail (got {payment['status']})"
            )
        self.db.execute(
            "UPDATE payments SET status = ?, verification_evidence = ? WHERE id = ?",
            (PaymentStatus.FAILED.value, reason, payment_id),
        )
        return self.get_payment(payment_id)

    def refund_payment(
        self, *, payment_id: str, amount_pence: int, reason: str | None = None
    ) -> dict[str, Any]:
        payment = self.get_payment(payment_id)
        if payment["status"] != PaymentStatus.SETTLED.value:
            raise StateTransitionError("Only SETTLED payments can be refunded")
        if amount_pence <= 0:
            raise ValidationError("Refund amount must be positive")
        refunded = self.db.query_one(
            "SELECT COALESCE(SUM(amount_pence), 0) AS total FROM refunds "
            "WHERE payment_id = ?",
            (payment_id,),
        )["total"]
        if refunded + amount_pence > payment["amount_pence"]:
            raise ValidationError(
                "Refund would exceed the settled payment amount",
                details={"already_refunded_pence": refunded},
            )
        refund_id = _new_id()
        self.db.execute(
            "INSERT INTO refunds (id, payment_id, amount_pence, reason, created_at) "
            "VALUES (?,?,?,?,?)",
            (refund_id, payment_id, amount_pence, reason, _now()),
        )
        self.ledger.record(
            kind="REVENUE_RECEIVED", source_type="refund", source_id=refund_id,
            amount_pence=-amount_pence,
            note=f"Refund against payment {payment_id}",
        )
        if refunded + amount_pence == payment["amount_pence"]:
            self.db.execute(
                "UPDATE payments SET status = ? WHERE id = ?",
                (PaymentStatus.REFUNDED.value, payment_id),
            )
        self._refresh_invoice_status(payment["invoice_id"])
        return self.db.query_one("SELECT * FROM refunds WHERE id = ?", (refund_id,))

    # ---------------------------------------------------------- fulfilment
    def create_fulfilment(self, *, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] != OrderStatus.PAID.value:
            raise StateTransitionError(
                "Fulfilment can only start for a PAID order — never dispatch unpaid goods"
            )
        fulfilment_id = _new_id()
        self.db.execute(
            "INSERT INTO fulfilments (id, order_id, status) VALUES (?,?,?)",
            (fulfilment_id, order_id, FulfilmentStatus.PENDING.value),
        )
        return self.get_fulfilment(fulfilment_id)

    def get_fulfilment(self, fulfilment_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM fulfilments WHERE id = ?", (fulfilment_id,)
        )
        if not row:
            raise NotFoundError(f"Fulfilment {fulfilment_id} not found")
        row["evidence"] = self.db.query_all(
            "SELECT * FROM delivery_evidence WHERE fulfilment_id = ?", (fulfilment_id,)
        )
        return row

    def dispatch_fulfilment(
        self, *, fulfilment_id: str, carrier: str, tracking_reference: str
    ) -> dict[str, Any]:
        fulfilment = self.get_fulfilment(fulfilment_id)
        if fulfilment["status"] != FulfilmentStatus.PENDING.value:
            raise StateTransitionError("Only PENDING fulfilments can be dispatched")
        if not carrier.strip() or not tracking_reference.strip():
            raise ValidationError("Carrier and tracking reference are required")
        self.db.execute(
            """UPDATE fulfilments
               SET status = ?, carrier = ?, tracking_reference = ?, dispatched_at = ?
               WHERE id = ?""",
            (FulfilmentStatus.DISPATCHED.value, carrier.strip(),
             tracking_reference.strip(), _now(), fulfilment_id),
        )
        self._set_order_status(fulfilment["order_id"], OrderStatus.DISPATCHED)
        return self.get_fulfilment(fulfilment_id)

    def add_delivery_evidence(
        self, *, fulfilment_id: str, kind: str, reference: str
    ) -> dict[str, Any]:
        fulfilment = self.get_fulfilment(fulfilment_id)
        if fulfilment["status"] != FulfilmentStatus.DISPATCHED.value:
            raise StateTransitionError(
                "Delivery evidence can only be attached to a DISPATCHED fulfilment"
            )
        if not kind.strip() or not reference.strip():
            raise EvidenceRequiredError("Evidence kind and reference are required")
        evidence_id = _new_id()
        self.db.execute(
            """INSERT INTO delivery_evidence
               (id, fulfilment_id, kind, reference, captured_at)
               VALUES (?,?,?,?,?)""",
            (evidence_id, fulfilment_id, kind.strip(), reference.strip(), _now()),
        )
        return self.db.query_one(
            "SELECT * FROM delivery_evidence WHERE id = ?", (evidence_id,)
        )

    def mark_delivered(self, *, fulfilment_id: str) -> dict[str, Any]:
        fulfilment = self.get_fulfilment(fulfilment_id)
        if fulfilment["status"] != FulfilmentStatus.DISPATCHED.value:
            raise StateTransitionError("Only DISPATCHED fulfilments can be delivered")
        if not fulfilment["evidence"]:
            raise EvidenceRequiredError(
                "An order can never be marked delivered without delivery evidence"
            )
        self.db.execute(
            "UPDATE fulfilments SET status = ?, delivered_at = ? WHERE id = ?",
            (FulfilmentStatus.DELIVERED.value, _now(), fulfilment_id),
        )
        self._set_order_status(fulfilment["order_id"], OrderStatus.DELIVERED)
        return self.get_fulfilment(fulfilment_id)

    # ----------------------------------------------------------- reporting
    def pipeline_report(self) -> dict[str, Any]:
        """Potential vs actual revenue — kept strictly separate."""
        by_stage: dict[str, int] = {}
        potential = 0
        for row in self.db.query_all("SELECT stage, estimated_value_pence FROM prospects"):
            by_stage[row["stage"]] = by_stage.get(row["stage"], 0) + 1
            potential += row["estimated_value_pence"] or 0
        outstanding = self.db.query_one(
            "SELECT COALESCE(SUM(amount_pence), 0) AS total FROM invoices "
            "WHERE status IN ('ISSUED','PARTIALLY_PAID')"
        )["total"]
        totals = self.ledger.totals()
        return {
            "prospects_by_stage": by_stage,
            "potential_revenue_pence": potential,
            "outstanding_invoices_pence": outstanding,
            **totals,
        }

    # ------------------------------------------------------------ internals
    def _refresh_invoice_status(self, invoice_id: str) -> None:
        invoice = self.get_invoice(invoice_id)
        settled = self.db.query_one(
            "SELECT COALESCE(SUM(p.amount_pence), 0) AS total "
            "FROM payments p WHERE p.invoice_id = ? AND p.status = 'SETTLED'",
            (invoice_id,),
        )["total"]
        refunded = self.db.query_one(
            "SELECT COALESCE(SUM(r.amount_pence), 0) AS total "
            "FROM refunds r JOIN payments p ON p.id = r.payment_id "
            "WHERE p.invoice_id = ?",
            (invoice_id,),
        )["total"]
        net = settled - refunded
        if net >= invoice["amount_pence"]:
            status = InvoiceStatus.PAID
            self.db.execute(
                "UPDATE invoices SET status = ?, paid_at = ? WHERE id = ?",
                (status.value, _now(), invoice_id),
            )
            order_id = invoice["order_id"]
            order = self.get_order(order_id)
            if order["status"] == OrderStatus.CONFIRMED.value:
                self._set_order_status(order_id, OrderStatus.PAID)
        elif settled > 0:
            self.db.execute(
                "UPDATE invoices SET status = ? WHERE id = ?",
                (InvoiceStatus.PARTIALLY_PAID.value, invoice_id),
            )

    def _set_order_status(self, order_id: str, status: OrderStatus) -> None:
        self.db.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _now(), order_id),
        )

    def _orders_for_prospect(self, prospect_id: str) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id "
            "WHERE c.prospect_id = ?",
            (prospect_id,),
        )

    def _invoices_for_prospect(self, prospect_id: str) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT i.* FROM invoices i "
            "JOIN orders o ON o.id = i.order_id "
            "JOIN customers c ON c.id = o.customer_id "
            "WHERE c.prospect_id = ?",
            (prospect_id,),
        )

    def _latest_payment_for_prospect(self, prospect_id: str) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT p.* FROM payments p "
            "JOIN invoices i ON i.id = p.invoice_id "
            "JOIN orders o ON o.id = i.order_id "
            "JOIN customers c ON c.id = o.customer_id "
            "WHERE c.prospect_id = ? ORDER BY p.authorised_at DESC LIMIT 1",
            (prospect_id,),
        )
