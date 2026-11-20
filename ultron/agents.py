"""Deterministic ULTRON workforce registry and task hand-off.

These are operational roles, not autonomous LLM agents. They create persisted
work items in the existing operations controller; external actions remain
subject to the controller's approval and evidence gates.
"""
from __future__ import annotations

from typing import Any

from .core.operations import OperationController


AGENTS: dict[str, dict[str, Any]] = {
    "NOVA": {"name": "NOVA", "role": "Coordinator", "purpose": "Prioritise work and route tasks across the ULTRON workforce."},
    "FORGE": {"name": "FORGE", "role": "Engineering", "purpose": "Maintain code, tests, releases and technical reliability."},
    "PM": {"name": "PM", "role": "Programme management", "purpose": "Maintain milestones, dependencies and delivery status."},
    "GUARDIAN": {"name": "GUARDIAN", "role": "Security and permissions", "purpose": "Review access, secrets, approvals and operational risk."},
    "FINANCE": {"name": "FINANCE", "role": "Financial control", "purpose": "Reconcile costs, settled receipts, refunds and verified profit."},
    "SALES": {"name": "SALES", "role": "Revenue development", "purpose": "Qualify evidence-backed customers and revenue opportunities."},
    "OPS": {"name": "OPS", "role": "Operations", "purpose": "Verify integrations, fulfilment, monitoring and recovery."},
}


def list_agents() -> list[dict[str, str]]:
    """Return a stable, read-only description of the seven registered roles."""
    return [dict(agent) for agent in AGENTS.values()]


def assign_task(
    operations: OperationController,
    *,
    agent: str,
    description: str,
    priority: int = 3,
    required_approval: bool = False,
    idempotency_key: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    """Persist a task under a known agent; never executes external actions."""
    agent_key = agent.strip().upper()
    if agent_key not in AGENTS:
        from .errors import ValidationError
        raise ValidationError(f"Unknown ULTRON agent: {agent}")
    return operations.create(
        description=description,
        owner=agent_key,
        priority=priority,
        required_approval=required_approval,
        idempotency_key=idempotency_key,
        next_action=next_action,
    )
