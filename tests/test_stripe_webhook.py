from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from ultron.api.app import create_app
from ultron.config import Settings

WEBHOOK_SECRET = "whsec_ultron_test_only"


def make_client(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "stripe-webhook.db"),
        integrations={"STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET},
    )
    client = TestClient(create_app(settings))
    db = client.app.state.db
    db.execute(
        "INSERT INTO customers (id, name, created_at) VALUES (?,?,?)",
        ("cus-test", "Webhook test customer", "now"),
    )
    db.execute(
        "INSERT INTO orders (id, customer_id, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        ("ord-test", "cus-test", "CONFIRMED", "now", "now"),
    )
    db.execute(
        "INSERT INTO invoices (id, order_id, status, amount_pence, issued_at) "
        "VALUES (?,?,?,?,?)",
        ("inv-test", "ord-test", "ISSUED", 2599, "now"),
    )
    return client


def signed_headers(payload: bytes):
    timestamp = int(time.time())
    signed = str(timestamp).encode() + b"." + payload
    digest = hmac.new(WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def checkout_event(event_id="evt-test-1", *, amount=2599, payment_status="paid"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs-test-1",
                "mode": "payment",
                "payment_status": payment_status,
                "currency": "gbp",
                "amount_total": amount,
                "payment_intent": "pi-test-1",
                "metadata": {"ultron_invoice_id": "inv-test"},
            }
        },
    }


def post_event(client, event):
    payload = json.dumps(event, separators=(",", ":")).encode()
    return client.post("/webhooks/stripe", content=payload, headers=signed_headers(payload))


def test_paid_checkout_webhook_settles_invoice_and_records_revenue_once(tmp_path):
    with make_client(tmp_path) as client:
        response = post_event(client, checkout_event())
        assert response.status_code == 200
        assert response.json()["status"] == "PROCESSED"

        db = client.app.state.db
        payment = db.query_one("SELECT * FROM payments WHERE provider_reference = ?", ("pi-test-1",))
        assert payment["status"] == "SETTLED"
        assert db.query_one("SELECT status FROM invoices WHERE id = ?", ("inv-test",))["status"] == "PAID"
        assert db.query_one(
            "SELECT SUM(amount_pence) AS total FROM ledger_entries WHERE kind='REVENUE_RECEIVED'"
        )["total"] == 2599

        duplicate = post_event(client, checkout_event())
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert db.query_one("SELECT COUNT(*) AS n FROM ledger_entries")["n"] == 1


def test_invalid_signature_is_rejected(tmp_path):
    with make_client(tmp_path) as client:
        payload = json.dumps(checkout_event()).encode()
        response = client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": "t=1,v1=not-a-signature"},
        )
        assert response.status_code == 400
        assert client.app.state.db.query_one("SELECT COUNT(*) AS n FROM payments")["n"] == 0


def test_unpaid_checkout_event_never_enters_revenue_ledger(tmp_path):
    with make_client(tmp_path) as client:
        response = post_event(client, checkout_event(payment_status="unpaid"))
        assert response.status_code == 200
        assert response.json()["status"] == "IGNORED_UNPAID"
        assert client.app.state.db.query_one("SELECT COUNT(*) AS n FROM payments")["n"] == 0
        assert client.app.state.db.query_one("SELECT COUNT(*) AS n FROM ledger_entries")["n"] == 0


def test_amount_mismatch_is_not_reconciled(tmp_path):
    with make_client(tmp_path) as client:
        response = post_event(client, checkout_event(amount=2500))
        assert response.status_code == 422
        assert client.app.state.db.query_one("SELECT COUNT(*) AS n FROM payments")["n"] == 0
        event = client.app.state.db.query_one(
            "SELECT status FROM webhook_events WHERE event_id = ?", ("evt-test-1",)
        )
        assert event["status"] == "FAILED"
