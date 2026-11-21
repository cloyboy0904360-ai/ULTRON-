"""Guarded task execution primitives for ULTRON.

This layer coordinates planning and injected handlers. It does not contain
provider-specific API calls, credentials, or browser automation. Callers must
supply an authorised handler and can require approval before execution.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Callable

from ..agents import AgentName, route_objective


class ExecutionStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    AWAITING_APPROVAL = "AWAITING APPROVAL"


@dataclass(frozen=True)
class ExecutionPlan:
    objective: str
    agent: AgentName
    approval_required: bool
    timeout_seconds: float
    max_retries: int


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    plan: ExecutionPlan
    attempts: int
    evidence: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def plan_objective(
    objective: str,
    *,
    approval_required: bool = False,
    timeout_seconds: float = 30.0,
    max_retries: int = 0,
) -> ExecutionPlan:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    return ExecutionPlan(
        objective=objective.strip(),
        agent=route_objective(objective).name,
        approval_required=approval_required,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def execute_plan(
    plan: ExecutionPlan,
    handler: Callable[[ExecutionPlan], str],
    *,
    approved: bool = False,
) -> ExecutionResult:
    """Execute an injected handler with bounded retries and a timeout.

    A handler must return non-empty evidence. No handler is called when an
    approval gate is required but approval is absent. Exceptions are retried
    only up to the plan's explicit retry limit; the final error is reported.
    """
    if plan.approval_required and not approved:
        return ExecutionResult(ExecutionStatus.AWAITING_APPROVAL, plan, 0)
    started = monotonic()
    attempts = 0
    last_error: str | None = None
    for attempts in range(1, plan.max_retries + 2):
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                evidence = pool.submit(handler, plan).result(timeout=plan.timeout_seconds)
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("handler must return non-empty evidence")
            return ExecutionResult(
                ExecutionStatus.VERIFIED, plan, attempts, evidence=evidence.strip(),
                elapsed_seconds=monotonic() - started,
            )
        except FutureTimeout:
            last_error = f"handler timed out after {plan.timeout_seconds:g}s"
        except Exception as exc:  # handler boundary: convert to a safe result
            last_error = f"{type(exc).__name__}: {exc}"
    return ExecutionResult(
        ExecutionStatus.FAILED, plan, attempts, error=last_error,
        elapsed_seconds=monotonic() - started,
    )
