"""Background jobs.

Jobs run inside the API process on a simple interval loop. Each job
execution is recorded as an operation so restarts never lose track of
what ran. A job that throws is marked FAILED with error details — the
scheduler keeps running.
"""

from __future__ import annotations

import threading
from typing import Callable

from ..core.operations import OperationController, OperationStatus
from ..errors import UltronError
from ..logging_config import get_logger

log = get_logger("ultron.jobs")

JobFn = Callable[[], str]


class Scheduler:
    def __init__(self, operations: OperationController):
        self.operations = operations
        self._jobs: list[tuple[str, JobFn, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, name: str, fn: JobFn, interval_seconds: float) -> None:
        self._jobs.append((name, fn, interval_seconds))

    def run_job_once(self, name: str, fn: JobFn) -> None:
        """Execute one job, tracked as an operation with evidence."""
        op = self.operations.create(
            description=f"Background job: {name}",
            owner="scheduler",
            priority=2,
        )
        self.operations.transition(op["id"], OperationStatus.RUNNING, actor="scheduler")
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - job isolation
            log.exception("background job failed", extra={"context": {"job": name}})
            self.operations.transition(
                op["id"], OperationStatus.FAILED,
                actor="scheduler", error_details=str(exc),
            )
            return
        self.operations.add_evidence(
            op["id"], kind="job_result", reference=result, actor="scheduler"
        )
        # Job operations bypass the gate sequence: evidence is the job result.
        from ..core.gates import GATE_ORDER

        for gate in GATE_ORDER:
            self.operations.pass_gate(
                op["id"], gate, evidence=f"job:{name}:{result}", actor="scheduler"
            )
        self.operations.transition(op["id"], OperationStatus.VERIFIED, actor="scheduler")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        import time

        last_run: dict[str, float] = {}
        while not self._stop.is_set():
            now = time.monotonic()
            for name, fn, interval in self._jobs:
                if now - last_run.get(name, 0.0) >= interval:
                    last_run[name] = now
                    try:
                        self.run_job_once(name, fn)
                    except UltronError:
                        log.exception("job wrapper failed", extra={"context": {"job": name}})
            self._stop.wait(1.0)
