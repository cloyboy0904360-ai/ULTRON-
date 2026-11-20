"""ULTRON HTTP API (FastAPI).

Routes are thin: they translate HTTP to engine/controller calls and map
structured errors to status codes. All business rules live in the core
and revenue modules.
"""

from __future__ import annotations

from typing import Any
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..agents import assign_task, list_agents
from ..adapters.asana import AsanaAdapter
from ..adapters.ebay import EbayAdapter
from ..adapters.github import GitHubAdapter
from ..adapters.make import MakeAdapter
from ..adapters.shopify import ShopifyAdapter
from ..adapters.stripe import StripeAdapter
from ..auth import AuthenticationError, Authenticator, PermissionDeniedError, Principal
from ..config import Settings
from ..core.gates import Gate
from ..core.operations import OperationController, OperationStatus
from ..db import Database
from ..executor import AgentExecutor
from ..errors import (
    ApprovalRequiredError,
    DuplicateError,
    EvidenceRequiredError,
    NotFoundError,
    StateTransitionError,
    UltronError,
    ValidationError,
)
from ..health import check_health, check_readiness
from ..logging_config import configure_logging
from ..revenue.engine import RevenueEngine
from ..revenue.models import PipelineStage, ProposalStatus

_STATUS_CODES = {
    NotFoundError: 404,
    ValidationError: 422,
    DuplicateError: 409,
    StateTransitionError: 409,
    ApprovalRequiredError: 403,
    EvidenceRequiredError: 422,
    AuthenticationError: 401,
    PermissionDeniedError: 403,
}


def verify_stripe_signature(
    payload: bytes, signature_header: str, secret: str, *, tolerance_seconds: int = 300
) -> bool:
    """Verify Stripe's timestamped HMAC-SHA256 signature without dependencies."""
    timestamp: int | None = None
    signatures: list[str] = []
    for item in signature_header.split(","):
        key, separator, value = item.strip().partition("=")
        if not separator:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        return False
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        return False
    signed_payload = str(timestamp).encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def _stripe_event_object(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    return obj if isinstance(obj, dict) else {}


def build_adapters(settings: Settings, db: Database) -> list:
    cfg = settings.integrations
    common: dict[str, Any] = {
        "timeout_seconds": settings.http_timeout_seconds,
        "max_retries": settings.max_retries,
        "db": db,
    }
    return [
        GitHubAdapter(token=cfg.get("GITHUB_TOKEN"), **common),
        AsanaAdapter(
            token=cfg.get("ASANA_ACCESS_TOKEN"),
            workspace_gid=cfg.get("ASANA_WORKSPACE_GID"),
            **common,
        ),
        MakeAdapter(token=cfg.get("MAKE_API_TOKEN"), **common),
        ShopifyAdapter(
            token=cfg.get("SHOPIFY_ACCESS_TOKEN"), shop=cfg.get("SHOPIFY_SHOP"), **common
        ),
        StripeAdapter(token=cfg.get("STRIPE_SECRET_KEY"), **common),
        EbayAdapter(
            client_id=cfg.get("EBAY_CLIENT_ID"),
            client_secret=cfg.get("EBAY_CLIENT_SECRET"),
            **common,
        ),
    ]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    configure_logging(settings.log_level)
    db = Database(settings.database_path)
    auth = Authenticator(settings.api_keys)
    operations = OperationController(db)
    revenue = RevenueEngine(db)
    adapters = build_adapters(settings, db)
    executor = AgentExecutor(operations)

    app = FastAPI(title="ULTRON", version="0.2.0")
    app.state.db = db
    app.state.operations = operations
    app.state.revenue = revenue
    app.state.executor = executor

    @app.exception_handler(UltronError)
    async def ultron_error_handler(_: Request, exc: UltronError) -> JSONResponse:
        status = next(
            (code for klass, code in _STATUS_CODES.items() if isinstance(exc, klass)),
            500,
        )
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    def _require(principal_obj: Principal, scope: str) -> None:
        if not principal_obj.can(scope):
            raise PermissionDeniedError(f"Scope '{scope}' required")

    def principal(x_api_key: str | None = Header(default=None)) -> Principal:
        if not auth.enabled:
            # Development mode: no keys configured, allow but mark unauthenticated.
            return Principal(key_digest="dev", scopes=frozenset({"*"}))
        return auth.authenticate(x_api_key)

    # ------------------------------------------------------------- health
    @app.get("/health")
    def health() -> dict[str, Any]:
        return check_health(db, adapters)

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        return check_readiness(db, adapters)

    # ---------------------------------------------------------- workforce
    @app.get("/agents")
    def agents(p: Principal = Depends(principal)) -> list[dict[str, str]]:
        _require(p, "operations:read")
        return list_agents()

    @app.post("/agents/{agent_name}/tasks", status_code=201)
    def assign_agent_task(
        agent_name: str, body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        return assign_task(
            operations,
            agent=agent_name,
            description=body.get("description", ""),
            priority=int(body.get("priority", 3)),
            required_approval=bool(body.get("required_approval", False)),
            idempotency_key=body.get("idempotency_key"),
            next_action=body.get("next_action"),
        )

    @app.post("/agents/tasks/{operation_id}/execute")
    def execute_agent_task(
        operation_id: str, p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        return executor.execute(operation_id)

    # --------------------------------------------------------- operations
    @app.post("/operations", status_code=201)
    def create_operation(
        body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        if body.get("status") is not None:
            raise ValidationError("Status is managed by the controller, not by callers")
        return operations.create(
            description=body.get("description", ""),
            owner=body.get("owner", ""),
            priority=int(body.get("priority", 3)),
            dependencies=body.get("dependencies"),
            required_approval=bool(body.get("required_approval", False)),
            idempotency_key=body.get("idempotency_key"),
            next_action=body.get("next_action"),
        )

    @app.get("/operations")
    def list_operations(
        status: str | None = None, p: Principal = Depends(principal)
    ) -> list[dict[str, Any]]:
        _require(p, "operations:read")
        return operations.list(OperationStatus(status) if status else None)

    @app.get("/operations/{operation_id}")
    def get_operation(
        operation_id: str, p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:read")
        return operations.get(operation_id)

    @app.post("/operations/{operation_id}/transition")
    def transition_operation(
        operation_id: str, body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        target = OperationStatus(body.get("target", ""))
        current = operations.get(operation_id)
        approver = None
        if (
            current["status"] == OperationStatus.AWAITING_APPROVAL.value
            and target == OperationStatus.READY
        ):
            _require(p, "operations:approve")
            approver = p.key_digest[:8]
        return operations.transition(
            operation_id,
            target,
            actor=p.key_digest[:8],
            error_details=body.get("error_details"),
            approver=approver,
        )

    @app.post("/operations/{operation_id}/gates")
    def pass_gate(
        operation_id: str, body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        return operations.pass_gate(
            operation_id,
            Gate(body.get("gate", "")),
            evidence=body.get("evidence", ""),
            actor=p.key_digest[:8],
        )

    @app.post("/operations/{operation_id}/retry")
    def retry_operation(
        operation_id: str, body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        return operations.retry(operation_id, actor=p.key_digest[:8])


    # -------------------------------------------------------- Stripe webhook
    @app.post("/webhooks/stripe")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    ) -> dict[str, Any]:
        webhook_secret = settings.integrations.get("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise HTTPException(status_code=503, detail="Stripe webhook signing secret is not configured")
        payload = await request.body()
        if not stripe_signature or not verify_stripe_signature(
            payload, stripe_signature, webhook_secret
        ):
            raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Invalid JSON webhook payload")
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Stripe event must be a JSON object")
        event_id = event.get("id")
        event_type = event.get("type")
        if not isinstance(event_id, str) or not event_id or not isinstance(event_type, str):
            raise HTTPException(status_code=400, detail="Stripe event id and type are required")

        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(payload).hexdigest()
        db.execute(
            """INSERT OR IGNORE INTO webhook_events
               (provider, event_id, event_type, status, received_at, payload_sha256)
               VALUES ('stripe', ?, ?, 'RECEIVED', ?, ?)""",
            (event_id, event_type, now, digest),
        )
        claim = db.execute(
            """UPDATE webhook_events SET status = 'PROCESSING', error_details = NULL
               WHERE provider = 'stripe' AND event_id = ?
                 AND status IN ('RECEIVED', 'FAILED')""",
            (event_id,),
        )
        if claim.rowcount != 1:
            existing = db.query_one(
                "SELECT status FROM webhook_events WHERE provider = 'stripe' AND event_id = ?",
                (event_id,),
            )
            return {
                "received": True,
                "event_id": event_id,
                "status": existing["status"] if existing else "UNKNOWN",
                "duplicate": True,
            }

        result_status = "IGNORED_UNSUPPORTED_EVENT"
        try:
            if event_type in {
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            }:
                session = _stripe_event_object(event)
                metadata = session.get("metadata") or {}
                invoice_id = metadata.get("ultron_invoice_id") if isinstance(metadata, dict) else None
                if not invoice_id:
                    result_status = "IGNORED_NO_ULTRON_INVOICE"
                elif session.get("payment_status") != "paid":
                    result_status = "IGNORED_UNPAID"
                else:
                    currency = str(session.get("currency") or "").lower()
                    amount_pence = session.get("amount_total")
                    payment_intent = session.get("payment_intent")
                    if isinstance(payment_intent, dict):
                        payment_intent = payment_intent.get("id")
                    reference = payment_intent or session.get("id")
                    if currency != "gbp":
                        raise ValidationError("ULTRON Stripe webhook accepts GBP payments only")
                    if not isinstance(amount_pence, int) or amount_pence <= 0:
                        raise ValidationError("Stripe session amount_total must be positive integer pence")
                    if not isinstance(reference, str) or not reference:
                        raise ValidationError("Stripe session has no payment reference")
                    invoice = db.query_one("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
                    if not invoice:
                        raise NotFoundError(f"ULTRON invoice {invoice_id} not found")
                    if invoice["status"] == "VOID":
                        raise ValidationError("Cannot reconcile payment against a VOID invoice")
                    if amount_pence != invoice["amount_pence"]:
                        raise ValidationError(
                            "Stripe amount does not match the ULTRON invoice amount",
                            details={
                                "stripe_amount_pence": amount_pence,
                                "invoice_amount_pence": invoice["amount_pence"],
                            },
                        )

                    existing_payment = db.query_one(
                        "SELECT * FROM payments WHERE provider_reference = ?", (reference,)
                    )
                    if existing_payment:
                        if (
                            existing_payment["invoice_id"] != invoice_id
                            or existing_payment["amount_pence"] != amount_pence
                        ):
                            raise ValidationError("Stripe payment reference conflicts with an existing ULTRON payment")
                        if existing_payment["status"] == "SETTLED":
                            result_status = "PROCESSED_ALREADY_SETTLED"
                        elif existing_payment["status"] == "AUTHORISED":
                            revenue.settle_payment(
                                payment_id=existing_payment["id"],
                                verification_evidence=f"stripe_event:{event_id};session:{session.get('id')};payment_intent:{reference}",
                            )
                            result_status = "PROCESSED"
                        else:
                            raise StateTransitionError(
                                f"Existing Stripe payment is {existing_payment['status']}; automatic reconciliation stopped"
                            )
                    else:
                        payment = revenue.record_payment_authorised(
                            invoice_id=invoice_id,
                            provider="stripe",
                            amount_pence=amount_pence,
                            provider_reference=reference,
                        )
                        revenue.settle_payment(
                            payment_id=payment["id"],
                            verification_evidence=f"stripe_event:{event_id};session:{session.get('id')};payment_intent:{reference}",
                        )
                        result_status = "PROCESSED"
        except Exception as exc:
            db.execute(
                """UPDATE webhook_events SET status = 'FAILED', processed_at = ?,
                   error_details = ? WHERE provider = 'stripe' AND event_id = ?""",
                (datetime.now(timezone.utc).isoformat(), f"{type(exc).__name__}: {exc}"[:1000], event_id),
            )
            raise

        db.execute(
            """UPDATE webhook_events SET status = ?, processed_at = ?
               WHERE provider = 'stripe' AND event_id = ?""",
            (result_status, datetime.now(timezone.utc).isoformat(), event_id),
        )
        return {"received": True, "event_id": event_id, "status": result_status}

    # ------------------------------------------------------------ revenue
    @app.post("/revenue/prospects", status_code=201)
    def create_prospect(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_prospect(
            name=body.get("name", ""),
            contact=body.get("contact"),
            source=body.get("source"),
            estimated_value_pence=body.get("estimated_value_pence"),
            notes=body.get("notes"),
        )

    @app.post("/revenue/prospects/{prospect_id}/advance")
    def advance_prospect(
        prospect_id: str, body: dict[str, Any], p: Principal = Depends(principal)
    ):
        _require(p, "revenue:write")
        return revenue.advance_prospect(prospect_id, PipelineStage(body.get("target", "")))

    @app.post("/revenue/prospects/{prospect_id}/convert", status_code=201)
    def convert_prospect(prospect_id: str, p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.convert_prospect(prospect_id)

    @app.post("/revenue/products", status_code=201)
    def create_product(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_product(
            name=body.get("name", ""), description=body.get("description")
        )

    @app.post("/revenue/skus", status_code=201)
    def create_sku(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_sku(
            product_id=body.get("product_id", ""),
            sku=body.get("sku", ""),
            unit_price_pence=int(body.get("unit_price_pence", 0)),
            unit_cost_pence=int(body.get("unit_cost_pence", 0)),
        )

    @app.post("/revenue/orders", status_code=201)
    def create_order(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_order(
            customer_id=body.get("customer_id", ""),
            idempotency_key=body.get("idempotency_key"),
        )

    @app.post("/revenue/orders/{order_id}/items", status_code=201)
    def add_order_item(order_id: str, body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.add_order_item(
            order_id=order_id, sku_id=body.get("sku_id", ""),
            quantity=int(body.get("quantity", 0)),
        )

    @app.post("/revenue/orders/{order_id}/confirm")
    def confirm_order(order_id: str, p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.confirm_order(order_id)

    @app.post("/revenue/invoices", status_code=201)
    def create_invoice(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_invoice(
            order_id=body.get("order_id", ""), due_at=body.get("due_at")
        )

    @app.post("/revenue/payments", status_code=201)
    def record_payment(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.record_payment_authorised(
            invoice_id=body.get("invoice_id", ""),
            provider=body.get("provider", ""),
            amount_pence=int(body.get("amount_pence", 0)),
            provider_reference=body.get("provider_reference", ""),
        )

    @app.post("/revenue/payments/{payment_id}/settle")
    def settle_payment(payment_id: str, body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.settle_payment(
            payment_id=payment_id,
            verification_evidence=body.get("verification_evidence", ""),
        )

    @app.post("/revenue/payments/{payment_id}/refund", status_code=201)
    def refund_payment(payment_id: str, body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.refund_payment(
            payment_id=payment_id,
            amount_pence=int(body.get("amount_pence", 0)),
            reason=body.get("reason"),
        )

    @app.post("/revenue/fulfilments", status_code=201)
    def create_fulfilment(body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.create_fulfilment(order_id=body.get("order_id", ""))

    @app.post("/revenue/fulfilments/{fulfilment_id}/dispatch")
    def dispatch_fulfilment(fulfilment_id: str, body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.dispatch_fulfilment(
            fulfilment_id=fulfilment_id,
            carrier=body.get("carrier", ""),
            tracking_reference=body.get("tracking_reference", ""),
        )

    @app.post("/revenue/fulfilments/{fulfilment_id}/evidence", status_code=201)
    def add_delivery_evidence(fulfilment_id: str, body: dict[str, Any], p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.add_delivery_evidence(
            fulfilment_id=fulfilment_id,
            kind=body.get("kind", ""),
            reference=body.get("reference", ""),
        )

    @app.post("/revenue/fulfilments/{fulfilment_id}/delivered")
    def mark_delivered(fulfilment_id: str, p: Principal = Depends(principal)):
        _require(p, "revenue:write")
        return revenue.mark_delivered(fulfilment_id=fulfilment_id)

    @app.get("/revenue/report")
    def revenue_report(p: Principal = Depends(principal)):
        _require(p, "revenue:read")
        return revenue.pipeline_report()

    # -------------------------------------------------------- integrations
    @app.get("/integrations/status")
    def integration_status(p: Principal = Depends(principal)):
        _require(p, "integrations:read")
        return [adapter.status() for adapter in adapters]

    return app
