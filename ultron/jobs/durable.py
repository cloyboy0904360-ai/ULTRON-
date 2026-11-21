"""Recovery-aware scheduler wrapper.

The existing scheduler remains backwards compatible. This wrapper adds a
persistent lease/retry record around each job execution without changing the
job callable contract.
"""
from __future__ import annotations

import uuid

from ..core.operations import OperationController, OperationStatus
from ..core.recovery import RecoveryStore
from .scheduler import JobFn, Scheduler


class RecoveryAwareScheduler(Scheduler):
    def __init__(self, operations: OperationController, recovery: RecoveryStore | None = None):
        super().__init__(operations)
        self.recovery = recovery or RecoveryStore(operations.db)

    def run_job_once(self, name: str, fn: JobFn) -> None:
        task_key = f"scheduler:{name}:{uuid.uuid4().hex}"
        self.recovery.enqueue(task_key=task_key, objective=f"run scheduled job {name}")
        claimed = self.recovery.claim(task_key=task_key, worker="scheduler")
        if claimed is None:
            return
        try:
            super().run_job_once(name, fn)
            self.recovery.finish(
                task_key=task_key,
                worker="scheduler",
                evidence=f"operation recorded for job:{name}",
            )
        except Exception as exc:  # noqa: BLE001 - persist then re-raise
            self.recovery.fail(task_key=task_key, worker="scheduler", error=str(exc))
            raise
