"""Enums for the revenue engine.

Money is always stored as integer pence. "Potential" revenue (pipeline
estimates, issued invoices) is never mixed with "received" revenue
(settled payments) — the ledger only records money that actually moved.
"""

from __future__ import annotations

from enum import Enum


class PipelineStage(str, Enum):
    IDENTIFY = "IDENTIFY"
    QUALIFY = "QUALIFY"
    CONTACT = "CONTACT"
    CONVERT = "CONVERT"
    PAYMENT = "PAYMENT"
    VERIFY = "VERIFY"
    DELIVER = "DELIVER"
    COMPLETE = "COMPLETE"
    GET_PAID = "GET_PAID"
    STABILISE = "STABILISE"
    AUTOMATE = "AUTOMATE"
    NEXT_OPPORTUNITY = "NEXT_OPPORTUNITY"


PIPELINE_ORDER: tuple[PipelineStage, ...] = tuple(PipelineStage)


class ProposalStatus(str, Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PAID = "PAID"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class InvoiceStatus(str, Enum):
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    VOID = "VOID"


class PaymentStatus(str, Enum):
    AUTHORISED = "AUTHORISED"   # authorised is NOT settled funds
    SETTLED = "SETTLED"         # verified, irrevocable receipt
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class FulfilmentStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    RETURNED = "RETURNED"
