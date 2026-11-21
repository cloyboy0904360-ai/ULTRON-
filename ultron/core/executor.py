"""Safe objective execution over the agent registry and recovery store.

Handlers are explicitly injected by the application. The executor never
chooses credentials, spends money, or calls a provider implicitly.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..agents import AgentName, route_objective
from .recovery import RecoveryStore

Handler = Callable[[str], tuple[dict[str, Any], str]]


class ObjectiveExecutor:
    def __init__(self, recovery: RecoveryStore, handlers: Mapping[AgentName, Handler] | None = None) -> None:
        self.recovery = recovery
        self.handlers = dict(handlers or {})

    def submit(self, *, task_key: str, objective: str, max_attempts: int = 3) -> dict[str, Any]:
        agent = route_objective(objective)
        task = self.recovery.enqueue(task_key=task_key, objective=objective, max_attempts=max_attempts)
        task["agent"] = agent.name.value
        task["mission"] = agent.mission
        return task

    def execute(self, *, task_key: str, worker: str) -> dict[str, Any]:
        task = self.recovery.claim(task_key=task_key, worker=worker)
        if task is None:
            return self.recovery.get(task_key)
        agent = route_objective(task["objective"])
        handler = self.handlers.get(agent.name)
        if handler is None:
            return self.recovery.fail(
                task_key=task_key,
                worker=worker,
                error=f"No handler registered for agent {agent.name.value}",
            )
        try:
            result, evidence = handler(task["objective"])
            return self.recovery.finish(
                task_key=task_key, worker=worker, result=result, evidence=evidence
            )
        except Exception as exc:  # noqa: BLE001 - persist failure for recovery
            return self.recovery.fail(task_key=task_key, worker=worker, error=str(exc))
