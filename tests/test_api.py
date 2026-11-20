import pytest
from fastapi.testclient import TestClient

from ultron.api.app import create_app
from ultron.config import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "api.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture()
def secured_client(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        api_keys=("test-key:operations:read,operations:write,revenue:write,revenue:read",),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_ready_reports_blocked_integrations(client):
    body = client.get("/ready").json()
    assert body["ready"] is True
    assert set(body["integrations_blocked"]) == {
        "github", "asana", "make", "shopify", "stripe", "ebay"
    }


def test_operation_lifecycle_over_http(client):
    created = client.post(
        "/operations", json={"description": "Do the thing", "owner": "sam"}
    )
    assert created.status_code == 201
    op_id = created.json()["id"]

    running = client.post(
        f"/operations/{op_id}/transition", json={"target": "RUNNING", "actor": "sam"}
    )
    assert running.status_code == 200

    blocked = client.post(
        f"/operations/{op_id}/transition", json={"target": "VERIFIED", "actor": "sam"}
    )
    assert blocked.status_code == 422  # evidence required
    assert blocked.json()["error"]["code"] == "EVIDENCE_REQUIRED"


def test_duplicate_idempotency_key_conflict(client):
    payload = {"description": "x", "owner": "a", "idempotency_key": "k1"}
    assert client.post("/operations", json=payload).status_code == 201
    assert client.post("/operations", json=payload).status_code == 409


def test_unknown_operation_is_404(client):
    assert client.get("/operations/nope").status_code == 404


def test_auth_required_when_keys_configured(secured_client):
    assert secured_client.post(
        "/operations", json={"description": "x", "owner": "a"}
    ).status_code == 401
    assert secured_client.post(
        "/operations",
        json={"description": "x", "owner": "a"},
        headers={"X-API-Key": "wrong"},
    ).status_code == 401
    ok = secured_client.post(
        "/operations",
        json={"description": "x", "owner": "a"},
        headers={"X-API-Key": "test-key"},
    )
    assert ok.status_code == 201


def test_revenue_report_requires_scope(secured_client):
    assert secured_client.get(
        "/revenue/report", headers={"X-API-Key": "test-key"}
    ).status_code == 200



def test_workforce_api_lists_and_assigns_persisted_tasks(client):
    agents = client.get("/agents")
    assert agents.status_code == 200
    assert [agent["name"] for agent in agents.json()] == [
        "NOVA", "FORGE", "PM", "GUARDIAN", "FINANCE", "SALES", "OPS"
    ]

    assigned = client.post(
        "/agents/SALES/tasks",
        json={
            "description": "Check supplier stock and landed margin",
            "priority": 1,
            "required_approval": True,
            "idempotency_key": "api-sales-first-sale",
        },
    )
    assert assigned.status_code == 201
    task = assigned.json()
    assert task["owner"] == "SALES"
    assert task["status"] == "READY"
    assert task["required_approval"] is True
    assert client.get(f"/operations/{task['id']}").json()["id"] == task["id"]


def test_workforce_api_rejects_unknown_agent(client):
    response = client.post(
        "/agents/UNKNOWN/tasks", json={"description": "not valid"}
    )
    assert response.status_code == 422


def test_operation_reads_require_read_scope(secured_client):
    assert secured_client.get("/operations").status_code == 401
    assert secured_client.get(
        "/operations", headers={"X-API-Key": "test-key"}
    ).status_code == 200



def test_api_executor_blocks_when_agent_handler_is_not_registered(client):
    task = client.post(
        "/agents/OPS/tasks", json={"description": "Run operational check"}
    ).json()

    result = client.post(f"/agents/tasks/{task['id']}/execute")

    assert result.status_code == 200
    assert result.json()["status"] == "BLOCKED"
    assert "No execution handler registered" in result.json()["error_details"]



def test_fulfilment_api_requires_delivery_evidence_before_delivered(client):
    revenue = client.app.state.revenue
    prospect = revenue.create_prospect(name="Fulfilment API test")
    customer = revenue.convert_prospect(prospect["id"])
    product = revenue.create_product(name="Test radio")
    sku = revenue.create_sku(
        product_id=product["id"], sku="RADIO-TEST-001",
        unit_price_pence=2599, unit_cost_pence=1200,
    )
    order = revenue.create_order(customer_id=customer["id"])
    revenue.add_order_item(order_id=order["id"], sku_id=sku["id"], quantity=1)
    revenue.confirm_order(order["id"])
    invoice = revenue.create_invoice(order_id=order["id"])
    payment = revenue.record_payment_authorised(
        invoice_id=invoice["id"], provider="test",
        amount_pence=2599, provider_reference="fulfilment-test-payment",
    )
    revenue.settle_payment(
        payment_id=payment["id"], verification_evidence="test settlement receipt"
    )

    created = client.post("/revenue/fulfilments", json={"order_id": order["id"]})
    assert created.status_code == 201
    fulfilment_id = created.json()["id"]

    dispatched = client.post(
        f"/revenue/fulfilments/{fulfilment_id}/dispatch",
        json={"carrier": "Test Carrier", "tracking_reference": "TRACK-TEST-001"},
    )
    assert dispatched.status_code == 200
    assert dispatched.json()["status"] == "DISPATCHED"

    premature = client.post(f"/revenue/fulfilments/{fulfilment_id}/delivered")
    assert premature.status_code == 422

    evidence = client.post(
        f"/revenue/fulfilments/{fulfilment_id}/evidence",
        json={"kind": "carrier_delivery_confirmation", "reference": "proof:TRACK-TEST-001"},
    )
    assert evidence.status_code == 201

    delivered = client.post(f"/revenue/fulfilments/{fulfilment_id}/delivered")
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "DELIVERED"
    assert revenue.get_order(order["id"])["status"] == "DELIVERED"
