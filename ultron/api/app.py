"""ULTRON HTTP API (FastAPI).

Routes are thin: they translate HTTP to engine/controller calls and map
structured errors to status codes. All business rules live in the core
and revenue modules.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, Request
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
        return operations.transition(
            operation_id,
            OperationStatus(body.get("target", "")),
            actor=body.get("actor", p.key_digest[:8]),
            error_details=body.get("error_details"),
            approver=body.get("approver"),
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
            actor=body.get("actor", p.key_digest[:8]),
        )

    @app.post("/operations/{operation_id}/retry")
    def retry_operation(
        operation_id: str, body: dict[str, Any], p: Principal = Depends(principal)
    ) -> dict[str, Any]:
        _require(p, "operations:write")
        return operations.retry(operation_id, actor=body.get("actor", p.key_digest[:8]))

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
