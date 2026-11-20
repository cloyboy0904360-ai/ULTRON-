from __future__ import annotations

import pytest

from ultron.agents import assign_task, list_agents
from ultron.core.operations import OperationController
from ultron.db import Database
from ultron.errors import ValidationError


def test_workforce_registers_all_seven_named_roles():
    agents = list_agents()
    assert [agent["name"] for agent in agents] == [
        "NOVA", "FORGE", "PM", "GUARDIAN", "FINANCE", "SALES", "OPS"
    ]
    assert all(agent["role"] and agent["purpose"] for agent in agents)


def test_assign_task_persists_it_under_the_selected_agent(tmp_path):
    db = Database(str(tmp_path / "agents.db"))
    controller = OperationController(db)

    task = assign_task(
        controller,
        agent="sales",
        description="Verify one UK supplier and landed margin",
        priority=1,
        required_approval=True,
        idempotency_key="sales-first-product",
    )

    assert task["owner"] == "SALES"
    assert task["status"] == "READY"
    assert task["required_approval"] is True
    assert controller.get(task["id"])["description"] == "Verify one UK supplier and landed margin"


def test_unknown_agent_is_rejected_without_creating_a_task(tmp_path):
    db = Database(str(tmp_path / "agents.db"))
    controller = OperationController(db)

    with pytest.raises(ValidationError, match="Unknown ULTRON agent"):
        assign_task(controller, agent="GHOST", description="Do something")

    assert controller.list() == []
