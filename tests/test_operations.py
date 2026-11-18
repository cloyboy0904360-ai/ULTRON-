import pytest

from ultron.core.gates import GATE_ORDER, Gate, next_gate
from ultron.core.operations import OperationController, OperationStatus
from ultron.errors import (
    ApprovalRequiredError,
    DuplicateError,
    EvidenceRequiredError,
    NotFoundError,
    StateTransitionError,
    ValidationError,
)


@pytest.fixture()
def ops(db):
    return OperationController(db)


def test_create_assigns_all_required_fields(ops):
    op = ops.create(description="Set up fulfilment", owner="alice", priority=1)
    for field in (
        "id", "description", "owner", "priority", "status",
        "created_at", "updated_at", "dependencies", "required_approval",
        "evidence", "next_action", "error_details",
    ):
        assert field in op
    assert op["status"] == OperationStatus.READY.value


def test_create_validates_input(ops):
    with pytest.raises(ValidationError):
        ops.create(description="", owner="alice")
    with pytest.raises(ValidationError):
        ops.create(description="x", owner="")
    with pytest.raises(ValidationError):
        ops.create(description="x", owner="a", priority=9)


def test_duplicate_prevention_by_idempotency_key(ops):
    ops.create(description="x", owner="a", idempotency_key="key-1")
    with pytest.raises(DuplicateError):
        ops.create(description="x", owner="a", idempotency_key="key-1")


def test_invalid_transition_rejected(ops):
    op = ops.create(description="x", owner="a")
    with pytest.raises(StateTransitionError):
        ops.transition(op["id"], OperationStatus.VERIFIED, actor="bob")


def test_verified_requires_all_gates_with_evidence(ops):
    op = ops.create(description="x", owner="a")
    ops.transition(op["id"], OperationStatus.RUNNING, actor="bob")
    with pytest.raises(EvidenceRequiredError):
        ops.transition(op["id"], OperationStatus.VERIFIED, actor="bob")

    for gate in GATE_ORDER:
        ops.pass_gate(op["id"], gate, evidence=f"proof-{gate.value}", actor="bob")
    done = ops.transition(op["id"], OperationStatus.VERIFIED, actor="bob")
    assert done["status"] == OperationStatus.VERIFIED.value
    assert len(done["gates"]) == len(GATE_ORDER)
    assert len(done["evidence"]) >= len(GATE_ORDER)


def test_gates_must_pass_in_order(ops):
    op = ops.create(description="x", owner="a")
    with pytest.raises(StateTransitionError):
        ops.pass_gate(op["id"], Gate.LIVE, evidence="proof", actor="bob")
    ops.pass_gate(op["id"], Gate.CORRECT, evidence="proof", actor="bob")
    assert next_gate([Gate.CORRECT.value]) == Gate.COMPLETE


def test_gate_requires_evidence(ops):
    op = ops.create(description="x", owner="a")
    with pytest.raises(EvidenceRequiredError):
        ops.pass_gate(op["id"], Gate.CORRECT, evidence="  ", actor="bob")


def test_dependencies_must_be_verified_before_running(ops):
    dep = ops.create(description="dep", owner="a")
    op = ops.create(description="main", owner="a", dependencies=[dep["id"]])
    with pytest.raises(StateTransitionError):
        ops.transition(op["id"], OperationStatus.RUNNING, actor="bob")
    ops.transition(dep["id"], OperationStatus.RUNNING, actor="bob")
    for gate in GATE_ORDER:
        ops.pass_gate(dep["id"], gate, evidence="p", actor="bob")
    ops.transition(dep["id"], OperationStatus.VERIFIED, actor="bob")
    running = ops.transition(op["id"], OperationStatus.RUNNING, actor="bob")
    assert running["status"] == OperationStatus.RUNNING.value


def test_unknown_dependency_rejected(ops):
    with pytest.raises(NotFoundError):
        ops.create(description="x", owner="a", dependencies=["nope"])


def test_approval_flow(ops):
    op = ops.create(description="x", owner="a", required_approval=True)
    ops.transition(op["id"], OperationStatus.RUNNING, actor="bob")
    waiting = ops.transition(
        op["id"], OperationStatus.AWAITING_APPROVAL, actor="bob"
    )
    assert waiting["status"] == OperationStatus.AWAITING_APPROVAL.value
    with pytest.raises(ApprovalRequiredError):
        ops.transition(op["id"], OperationStatus.READY, actor="bob")
    approved = ops.transition(
        op["id"], OperationStatus.READY, actor="bob", approver="carol"
    )
    assert approved["approved_by"] == "carol"


def test_retry_only_from_failed_or_blocked(ops):
    op = ops.create(description="x", owner="a")
    with pytest.raises(StateTransitionError):
        ops.retry(op["id"], actor="bob")
    ops.transition(op["id"], OperationStatus.RUNNING, actor="bob")
    ops.transition(op["id"], OperationStatus.FAILED, actor="bob",
                   error_details="boom")
    retried = ops.retry(op["id"], actor="bob")
    assert retried["status"] == OperationStatus.READY.value
    assert retried["retry_count"] == 1
    assert retried["error_details"] is None


def test_persistence_across_connections(tmp_path):
    path = tmp_path / "persist.db"
    from ultron.db import Database

    db1 = Database(path)
    op = OperationController(db1).create(description="durable", owner="a")
    db1.close()

    db2 = Database(path)
    loaded = OperationController(db2).get(op["id"])
    assert loaded["description"] == "durable"
    db2.close()
