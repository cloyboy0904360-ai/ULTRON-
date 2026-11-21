import time

from ultron.agents import AgentName
from ultron.core.executor import ExecutionStatus, execute_plan, plan_objective


def test_plan_routes_and_requires_approval_before_handler():
    plan = plan_objective("publish a marketing video", approval_required=True)
    called = []
    result = execute_plan(plan, lambda _: called.append(True) or "published")
    assert result.status is ExecutionStatus.AWAITING_APPROVAL
    assert result.attempts == 0
    assert called == []
    assert plan.agent is AgentName.PIXEL


def test_executor_returns_evidence_and_retries_failures():
    calls = []

    def handler(_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary")
        return "test evidence"

    result = execute_plan(plan_objective("research opportunities", max_retries=1), handler)
    assert result.status is ExecutionStatus.VERIFIED
    assert result.attempts == 2
    assert result.evidence == "test evidence"


def test_executor_times_out_and_does_not_claim_success():
    result = execute_plan(
        plan_objective("run a task", timeout_seconds=0.01),
        lambda _: time.sleep(0.1) or "late evidence",
    )
    assert result.status is ExecutionStatus.FAILED
    assert "timed out" in result.error
