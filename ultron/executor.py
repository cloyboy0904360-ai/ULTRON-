"""Safe local task execution for registered ULTRON workforce handlers.

Handlers are ordinary in-process Python callables. No model or external
account is invoked implicitly. Each handler must return evidence for every
completion gate before the operation can become VERIFIED.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .agents import AGENTS
from .core.gates import GATE_ORDER, Gate
from .core.operations import OperationController, OperationStatus
from .errors import ApprovalRequiredError, StateTransitionError

TaskHandler = Callable[[dict[str, Any]], Mapping[str, str]]


class AgentExecutor:
    def __init__(self, operations: OperationController):
        self.operations = operations
        self._handlers: dict[str, TaskHandler] = {}

    def register_handler(self, agent: str, handler: TaskHandler) -> None:
        key = agent.strip().upper()
        if key not in AGENTS:
            from .errors import ValidationError
            raise ValidationError(f"Unknown ULTRON agent: {agent}")
        self._handlers[key] = handler

    def execute(self, operation_id: str) -> dict[str, Any]:
        op = self.operations.get(operation_id)
        owner = op["owner"].strip().upper()
        if owner not in AGENTS:
            raise StateTransitionError(
                "Operation owner is not a registered ULTRON agent",
                details={"owner": op["owner"]},
            )
        if OperationStatus(op["status"]) != OperationStatus.READY:
            raise StateTransitionError("Only READY agent tasks can be executed")
        handler = self._handlers.get(owner)
        if handler is None:
            return self.operations.transition(
                operation_id,
                OperationStatus.BLOCKED,
                actor="ultron-executor",
                error_details=f"No execution handler registered for {owner}; no work was run.",
            )
        if op["required_approval"] and not op["approved_by"]:
            return self.operations.transition(
                operation_id,
                OperationStatus.AWAITING_APPROVAL,
                actor="ultron-executor",
            )

        self.operations.transition(
            operation_id, OperationStatus.RUNNING, actor=f"agent:{owner}"
        )
        try:
            evidence = handler(op)
            if not isinstance(evidence, Mapping):
                raise TypeError("Agent handler must return a gate-to-evidence mapping")
            missing = [
                gate.value for gate in GATE_ORDER
                if not isinstance(evidence.get(gate.value), str)
                or not evidence[gate.value].strip()
            ]
            if missing:
                return self.operations.transition(
                    operation_id,
                    OperationStatus.BLOCKED,
                    actor=f"agent:{owner}",
                    error_details=f"Handler did not supply evidence for gates: {', '.join(missing)}",
                )
            for gate in GATE_ORDER:
                self.operations.pass_gate(
                    operation_id,
                    gate,
                    evidence=evidence[gate.value],
                    actor=f"agent:{owner}",
                )
            self.operations.add_evidence(
                operation_id,
                kind="agent_execution",
                reference=f"{owner} handler completed with gate evidence",
                actor=f"agent:{owner}",
            )
            return self.operations.transition(
                operation_id, OperationStatus.VERIFIED, actor=f"agent:{owner}"
            )
        except Exception as exc:
            current = self.operations.get(operation_id)
            if current["status"] == OperationStatus.RUNNING.value:
                return self.operations.transition(
                    operation_id,
                    OperationStatus.FAILED,
                    actor=f"agent:{owner}",
                    error_details=f"{type(exc).__name__}: {exc}",
                )
            raise
