"""ULTRON central operating controller.

Every business operation is a persisted record with a lifecycle:

    READY -> RUNNING -> VERIFIED | BLOCKED | AWAITING_APPROVAL | FAILED
    Any active state -> PAUSED -> (resume to previous active state)

Rules enforced here:
- VERIFIED requires every completion gate passed with evidence.
- Operations requiring approval cannot leave AWAITING_APPROVAL without
  an approver identity.
- Duplicate prevention via a caller-supplied idempotency key.
- Safe retry: only FAILED or BLOCKED operations can be retried, and the
  retry count is persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..db import Database, dumps, loads
from ..errors import (
    ApprovalRequiredError,
    DuplicateError,
    EvidenceRequiredError,
    NotFoundError,
    StateTransitionError,
    ValidationError,
)
from ..logging_config import get_logger
from .gates import GATE_ORDER, Gate, next_gate

log = get_logger("ultron.core.operations")


class OperationStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING APPROVAL"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


_ALLOWED_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.READY: {
        OperationStatus.RUNNING,
        OperationStatus.BLOCKED,
        OperationStatus.AWAITING_APPROVAL,
        OperationStatus.PAUSED,
        OperationStatus.FAILED,
    },
    OperationStatus.RUNNING: {
        OperationStatus.VERIFIED,
        OperationStatus.BLOCKED,
        OperationStatus.AWAITING_APPROVAL,
        OperationStatus.FAILED,
        OperationStatus.PAUSED,
    },
    OperationStatus.PAUSED: {
        OperationStatus.READY,
        OperationStatus.RUNNING,
        OperationStatus.FAILED,
    },
    OperationStatus.BLOCKED: {OperationStatus.READY, OperationStatus.FAILED},
    OperationStatus.AWAITING_APPROVAL: {
        OperationStatus.READY,   # approved -> back to ready to run
        OperationStatus.FAILED,  # rejected
        OperationStatus.BLOCKED,
    },
    OperationStatus.FAILED: {OperationStatus.READY},  # retry path
    OperationStatus.VERIFIED: set(),  # terminal
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class OperationController:
    def __init__(self, db: Database):
        self.db = db

    # ----------------------------------------------------------- queries
    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM operations WHERE id = ?", (operation_id,))
        if not row:
            raise NotFoundError(f"Operation {operation_id} not found")
        return self._hydrate(row)

    def list(self, status: OperationStatus | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self.db.query_all("SELECT * FROM operations ORDER BY created_at")
        else:
            rows = self.db.query_all(
                "SELECT * FROM operations WHERE status = ? ORDER BY created_at",
                (status.value,),
            )
        return [self._hydrate(r) for r in rows]

    # ----------------------------------------------------------- commands
    def create(
        self,
        *,
        description: str,
        owner: str,
        priority: int = 3,
        dependencies: list[str] | None = None,
        required_approval: bool = False,
        idempotency_key: str | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any]:
        if not description.strip():
            raise ValidationError("Operation description is required")
        if not owner.strip():
            raise ValidationError("Operation owner is required")
        if not 1 <= priority <= 5:
            raise ValidationError("Priority must be between 1 and 5")

        if idempotency_key:
            existing = self.db.query_one(
                "SELECT * FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            )
            if existing:
                log.info(
                    "duplicate operation suppressed",
                    extra={"context": {"idempotency_key": idempotency_key}},
                )
                raise DuplicateError(
                    "Operation with this idempotency key already exists",
                    details={"operation_id": existing["id"]},
                )

        op_id = _new_id()
        now = _now()
        deps = dependencies or []
        for dep in deps:
            self.get(dep)  # raises NotFoundError for unknown dependency
        self.db.execute(
            """INSERT INTO operations
               (id, idempotency_key, description, owner, priority, status,
                created_at, updated_at, dependencies, required_approval,
                next_action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                op_id,
                idempotency_key,
                description.strip(),
                owner.strip(),
                priority,
                OperationStatus.READY.value,
                now,
                now,
                dumps(deps),
                1 if required_approval else 0,
                next_action,
            ),
        )
        self._record_event(op_id, "CREATED", {"owner": owner, "priority": priority})
        return self.get(op_id)

    def transition(
        self,
        operation_id: str,
        target: OperationStatus,
        *,
        actor: str,
        error_details: str | None = None,
        approver: str | None = None,
    ) -> dict[str, Any]:
        op = self.get(operation_id)
        current = OperationStatus(op["status"])
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise StateTransitionError(
                f"Cannot move operation from {current.value} to {target.value}",
                details={"operation_id": operation_id},
            )

        if target == OperationStatus.RUNNING:
            self._require_dependencies_verified(op)

        if target == OperationStatus.AWAITING_APPROVAL and not op["required_approval"]:
            raise StateTransitionError(
                "Operation does not require approval; cannot await it"
            )

        if current == OperationStatus.AWAITING_APPROVAL and target == OperationStatus.READY:
            if not op["required_approval"]:
                raise ApprovalRequiredError("Operation never requested approval")
            if not approver:
                raise ApprovalRequiredError(
                    "Approval requires an approver identity",
                    details={"operation_id": operation_id},
                )
            self.db.execute(
                "UPDATE operations SET approved_by = ? WHERE id = ?",
                (approver, operation_id),
            )

        if target == OperationStatus.VERIFIED:
            self._require_all_gates_passed(op)

        self._set_status(operation_id, target, error_details=error_details)
        self._record_event(
            operation_id,
            f"STATUS_{target.value.replace(' ', '_')}",
            {"actor": actor, "from": current.value},
        )
        return self.get(operation_id)

    def pass_gate(
        self, operation_id: str, gate: Gate, *, evidence: str, actor: str
    ) -> dict[str, Any]:
        """Pass the next completion gate, backed by evidence."""
        if not evidence.strip():
            raise EvidenceRequiredError("Passing a gate requires evidence")
        op = self.get(operation_id)
        passed = [g for g, _ in op["gates"].items()]
        expected = next_gate(passed)
        if expected is None:
            raise StateTransitionError("All gates are already passed")
        if gate != expected:
            raise StateTransitionError(
                f"Next gate must be {expected.value}, not {gate.value}"
            )
        gates = dict(op["gates"])
        gates[gate.value] = {"evidence": evidence.strip(), "by": actor, "at": _now()}
        self.db.execute(
            "UPDATE operations SET gates = ?, updated_at = ? WHERE id = ?",
            (dumps(gates), _now(), operation_id),
        )
        self.add_evidence(
            operation_id, kind=f"gate:{gate.value}", reference=evidence, actor=actor
        )
        self._record_event(operation_id, f"GATE_{gate.value}", {"actor": actor})
        return self.get(operation_id)

    def add_evidence(
        self, operation_id: str, *, kind: str, reference: str, actor: str
    ) -> dict[str, Any]:
        if not reference.strip():
            raise EvidenceRequiredError("Evidence reference must not be empty")
        op = self.get(operation_id)
        evidence = list(op["evidence"])
        evidence.append(
            {"kind": kind, "reference": reference.strip(), "by": actor, "at": _now()}
        )
        self.db.execute(
            "UPDATE operations SET evidence = ?, updated_at = ? WHERE id = ?",
            (dumps(evidence), _now(), operation_id),
        )
        return self.get(operation_id)

    def set_next_action(self, operation_id: str, next_action: str) -> dict[str, Any]:
        self.get(operation_id)
        self.db.execute(
            "UPDATE operations SET next_action = ?, updated_at = ? WHERE id = ?",
            (next_action, _now(), operation_id),
        )
        return self.get(operation_id)

    def retry(self, operation_id: str, *, actor: str) -> dict[str, Any]:
        """Safely retry a FAILED or BLOCKED operation.

        The operation returns to READY (not RUNNING) so dependencies and
        approvals are re-evaluated, and the persisted retry count is
        incremented exactly once per call.
        """
        op = self.get(operation_id)
        current = OperationStatus(op["status"])
        if current not in {OperationStatus.FAILED, OperationStatus.BLOCKED}:
            raise StateTransitionError(
                f"Only FAILED or BLOCKED operations can be retried (got {current.value})"
            )
        self.db.execute(
            """UPDATE operations
               SET status = ?, retry_count = retry_count + 1,
                   error_details = NULL, updated_at = ?
               WHERE id = ?""",
            (OperationStatus.READY.value, _now(), operation_id),
        )
        self._record_event(
            operation_id, "RETRY", {"actor": actor, "attempt": op["retry_count"] + 1}
        )
        return self.get(operation_id)

    def events(self, operation_id: str) -> list[dict[str, Any]]:
        self.get(operation_id)
        return self.db.query_all(
            "SELECT * FROM operation_events WHERE operation_id = ? ORDER BY id",
            (operation_id,),
        )

    # ----------------------------------------------------------- internals
    def _require_dependencies_verified(self, op: dict[str, Any]) -> None:
        unverified = [
            dep_id
            for dep_id in op["dependencies"]
            if self.get(dep_id)["status"] != OperationStatus.VERIFIED.value
        ]
        if unverified:
            raise StateTransitionError(
                "Dependencies are not VERIFIED",
                details={"unverified_dependencies": unverified},
            )

    def _require_all_gates_passed(self, op: dict[str, Any]) -> None:
        missing = [g.value for g in GATE_ORDER if g.value not in op["gates"]]
        if missing:
            raise EvidenceRequiredError(
                "Operation cannot be VERIFIED until all completion gates pass with evidence",
                details={"missing_gates": missing},
            )
        if not op["evidence"]:
            raise EvidenceRequiredError(
                "Operation cannot be VERIFIED without captured evidence"
            )

    def _set_status(
        self, operation_id: str, status: OperationStatus, *, error_details: str | None
    ) -> None:
        self.db.execute(
            "UPDATE operations SET status = ?, error_details = ?, updated_at = ? WHERE id = ?",
            (status.value, error_details, _now(), operation_id),
        )

    def _record_event(self, operation_id: str, event: str, detail: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO operation_events (operation_id, event, detail, at) VALUES (?,?,?,?)",
            (operation_id, event, dumps(detail), _now()),
        )

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["dependencies"] = loads(row["dependencies"], [])
        row["evidence"] = loads(row["evidence"], [])
        row["gates"] = loads(row["gates"], {})
        row["required_approval"] = bool(row["required_approval"])
        return row
