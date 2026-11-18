import pytest

from ultron.errors import (
    DuplicateError,
    EvidenceRequiredError,
    StateTransitionError,
    ValidationError,
)
from ultron.revenue.engine import RevenueEngine
from ultron.revenue.models import (
    InvoiceStatus,
    OrderStatus,
    PaymentStatus,
    PipelineStage,
    ProposalStatus,
)


@pytest.fixture()
def engine(db):
    return RevenueEngine(db)


def _paid_order(engine, price=10000, cost=4000):
    prospect = engine.create_prospect(name="Acme", estimated_value_pence=price)
    customer = engine.convert_prospect(prospect["id"])
    product = engine.create_product(name="Widget")
    sku = engine.create_sku(
        product_id=product["id"], sku="W-1",
        unit_price_pence=price, unit_cost_pence=cost,
    )
    order = engine.create_order(customer_id=customer["id"], idempotency_key="o-1")
    engine.add_order_item(order_id=order["id"], sku_id=sku["id"], quantity=1)
    engine.confirm_order(order["id"])
    invoice = engine.create_invoice(order_id=order["id"])
    payment = engine.record_payment_authorised(
        invoice_id=invoice["id"], provider="stripe",
        amount_pence=price, provider_reference="ch_1",
    )
    return prospect, customer, order, invoice, payment


def test_full_pipeline_to_get_paid(engine):
    prospect, _, order, invoice, payment = _paid_order(engine)

    # Unpaid invoice is not received revenue.
    assert engine.pipeline_report()["received_revenue_pence"] == 0

    # Authorisation is not settled funds: DELIVER is blocked.
    with pytest.raises(StateTransitionError):
        engine.advance_prospect(prospect["id"], PipelineStage.DELIVER)

    engine.advance_prospect(prospect["id"], PipelineStage.PAYMENT)
    engine.advance_prospect(prospect["id"], PipelineStage.VERIFY)

    # Settlement requires evidence.
    with pytest.raises(EvidenceRequiredError):
        engine.settle_payment(payment_id=payment["id"], verification_evidence="")
    settled = engine.settle_payment(
        payment_id=payment["id"], verification_evidence="stripe-payout-po_1"
    )
    assert settled["status"] == PaymentStatus.SETTLED.value
    assert engine.get_invoice(invoice["id"])["status"] == InvoiceStatus.PAID.value
    assert engine.get_order(order["id"])["status"] == OrderStatus.PAID.value
    assert engine.pipeline_report()["received_revenue_pence"] == 10000

    engine.advance_prospect(prospect["id"], PipelineStage.DELIVER)

    # Delivery requires evidence.
    fulfilment = engine.create_fulfilment(order_id=order["id"])
    engine.dispatch_fulfilment(
        fulfilment_id=fulfilment["id"], carrier="Royal Mail",
        tracking_reference="RM123",
    )
    with pytest.raises(EvidenceRequiredError):
        engine.mark_delivered(fulfilment_id=fulfilment["id"])
    engine.add_delivery_evidence(
        fulfilment_id=fulfilment["id"], kind="signature", reference="sig-photo-1"
    )
    delivered = engine.mark_delivered(fulfilment_id=fulfilment["id"])
    assert delivered["status"] == "DELIVERED"
    assert engine.get_order(order["id"])["status"] == OrderStatus.DELIVERED.value

    for stage in (
        PipelineStage.COMPLETE, PipelineStage.GET_PAID,
        PipelineStage.STABILISE, PipelineStage.AUTOMATE,
        PipelineStage.NEXT_OPPORTUNITY,
    ):
        engine.advance_prospect(prospect["id"], stage)
    assert engine.get_prospect(prospect["id"])["stage"] == "NEXT_OPPORTUNITY"

    report = engine.pipeline_report()
    assert report["received_revenue_pence"] == 10000
    assert report["costs_pence"] == 4000
    assert report["profit_pence"] == 6000


def test_pipeline_cannot_go_backwards(engine):
    prospect = engine.create_prospect(name="A")
    engine.advance_prospect(prospect["id"], PipelineStage.QUALIFY)
    with pytest.raises(StateTransitionError):
        engine.advance_prospect(prospect["id"], PipelineStage.IDENTIFY)


def test_payment_stage_requires_order(engine):
    prospect = engine.create_prospect(name="A")
    engine.convert_prospect(prospect["id"])
    with pytest.raises(StateTransitionError):
        engine.advance_prospect(prospect["id"], PipelineStage.PAYMENT)


def test_invoice_only_for_confirmed_order(engine):
    prospect = engine.create_prospect(name="A")
    customer = engine.convert_prospect(prospect["id"])
    order = engine.create_order(customer_id=customer["id"])
    with pytest.raises(StateTransitionError):
        engine.create_invoice(order_id=order["id"])


def test_fulfilment_never_for_unpaid_order(engine):
    prospect = engine.create_prospect(name="A")
    customer = engine.convert_prospect(prospect["id"])
    product = engine.create_product(name="W")
    sku = engine.create_sku(product_id=product["id"], sku="W-1", unit_price_pence=500)
    order = engine.create_order(customer_id=customer["id"])
    engine.add_order_item(order_id=order["id"], sku_id=sku["id"], quantity=1)
    engine.confirm_order(order["id"])
    with pytest.raises(StateTransitionError):
        engine.create_fulfilment(order_id=order["id"])


def test_duplicate_payment_reference_rejected(engine):
    _, _, _, invoice, _ = _paid_order(engine)
    with pytest.raises(DuplicateError):
        engine.record_payment_authorised(
            invoice_id=invoice["id"], provider="stripe",
            amount_pence=1, provider_reference="ch_1",
        )


def test_duplicate_order_idempotency_key_rejected(engine):
    prospect = engine.create_prospect(name="A")
    customer = engine.convert_prospect(prospect["id"])
    engine.create_order(customer_id=customer["id"], idempotency_key="dup")
    with pytest.raises(DuplicateError):
        engine.create_order(customer_id=customer["id"], idempotency_key="dup")


def test_refund_cannot_exceed_settled_amount(engine):
    _, _, _, _, payment = _paid_order(engine)
    with pytest.raises(StateTransitionError):
        engine.refund_payment(payment_id=payment["id"], amount_pence=1)
    engine.settle_payment(payment_id=payment["id"], verification_evidence="po_1")
    with pytest.raises(ValidationError):
        engine.refund_payment(payment_id=payment["id"], amount_pence=10001)
    engine.refund_payment(payment_id=payment["id"], amount_pence=6000, reason="partial")
    report = engine.pipeline_report()
    assert report["received_revenue_pence"] == 4000
    with pytest.raises(ValidationError):
        engine.refund_payment(payment_id=payment["id"], amount_pence=4001)


def test_double_settle_rejected(engine):
    _, _, _, _, payment = _paid_order(engine)
    engine.settle_payment(payment_id=payment["id"], verification_evidence="po_1")
    with pytest.raises(StateTransitionError):
        engine.settle_payment(payment_id=payment["id"], verification_evidence="po_2")


def test_cancel_confirmed_order_reverses_costs(engine):
    prospect = engine.create_prospect(name="A")
    customer = engine.convert_prospect(prospect["id"])
    product = engine.create_product(name="W")
    sku = engine.create_sku(
        product_id=product["id"], sku="W-1", unit_price_pence=500, unit_cost_pence=200
    )
    order = engine.create_order(customer_id=customer["id"])
    engine.add_order_item(order_id=order["id"], sku_id=sku["id"], quantity=2)
    engine.confirm_order(order["id"])
    assert engine.pipeline_report()["costs_pence"] == 400
    engine.cancel_order(order["id"])
    assert engine.pipeline_report()["costs_pence"] == 0


def test_proposal_status_flow(engine):
    prospect = engine.create_prospect(name="A")
    proposal = engine.create_proposal(prospect_id=prospect["id"], amount_pence=1000)
    engine.set_proposal_status(proposal["id"], ProposalStatus.SENT)
    accepted = engine.set_proposal_status(proposal["id"], ProposalStatus.ACCEPTED)
    assert accepted["status"] == "ACCEPTED"
    with pytest.raises(StateTransitionError):
        engine.set_proposal_status(proposal["id"], ProposalStatus.DRAFT)


def test_sku_validation(engine):
    product = engine.create_product(name="W")
    engine.create_sku(product_id=product["id"], sku="UNIQUE-1", unit_price_pence=100)
    with pytest.raises(DuplicateError):
        engine.create_sku(product_id=product["id"], sku="UNIQUE-1", unit_price_pence=100)
    with pytest.raises(ValidationError):
        engine.create_sku(product_id=product["id"], sku="X", unit_price_pence=-1)
