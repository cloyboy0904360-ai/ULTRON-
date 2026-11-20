from __future__ import annotations

from ultron.agents import assign_task
from ultron.core.gates import GATE_ORDER
from ultron.core.operations import OperationController, OperationStatus
from ultron.db import Database
from ultron.executor import AgentExecutor


def test_unconfigured_agent_execution_is_blocked_not_faked(tmp_path):
    db = Database(str(tmp_path / "executor.db"))
    operations = OperationController(db)
    task = assign_task(operations, agent="OPS", description="Check monitoring")
    executor = AgentExecutor(operations)

    result = executor.execute(task["id"])

    assert result["status"] == OperationStatus.BLOCKED.value
    assert "No execution handler registered" in result["error_details"]


def test_registered_handler_must_supply_evidence_for_every_gate(tmp_path):
    db = Database(str(tmp_path / "executor.db"))
    operations = OperationController(db)
    task = assign_task(operations, agent="OPS", description="Check health")
    executor = AgentExecutor(operations)
    executor.register_handler("OPS", lambda _: {
        gate.value: f"verified test evidence for {gate.value}" for gate in GATE_ORDER
    })

    result = executor.execute(task["id"])

    assert result["status"] == OperationStatus.VERIFIED.value
    assert set(result["gates"]) == {gate.value for gate in GATE_ORDER}


def test_approval_required_task_waits_for_approval_before_handler_runs(tmp_path):
    db = Database(str(tmp_path / "executor.db"))
    operations = OperationController(db)
    task = assign_task(
        operations, agent="FINANCE", description="Approve financial action",
        required_approval=True,
    )
    called = []
    executor = AgentExecutor(operations)
    executor.register_handler("FINANCE", lambda _: called.append(True) or {})

    result = executor.execute(task["id"])

    assert result["status"] == OperationStatus.AWAITING_APPROVAL.value
    assert called == []
