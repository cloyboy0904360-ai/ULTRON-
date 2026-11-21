"""Specialist-agent registry and deterministic routing for ULTRON.

The registry is deliberately side-effect free: selecting an agent never
executes external actions. Execution remains the responsibility of the
operation controller and its approval/evidence gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentName(str, Enum):
    NOVA = "NOVA"
    FORGE = "FORGE"
    SALES = "SALES"
    OPS = "OPS"
    FINANCE = "FINANCE"
    PIXEL = "PIXEL"
    SEO = "SEO"
    GUARDIAN = "GUARDIAN"
    PM = "PM"


@dataclass(frozen=True)
class AgentSpec:
    name: AgentName
    mission: str
    keywords: tuple[str, ...]


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(AgentName.NOVA, "research and opportunity discovery", ("research", "opportunity", "market", "discover")),
    AgentSpec(AgentName.FORGE, "software development, repair and maintenance", ("code", "software", "bug", "repair", "test", "deploy")),
    AgentSpec(AgentName.SALES, "lead generation, sales and customer acquisition", ("lead", "sales", "customer", "prospect", "proposal")),
    AgentSpec(AgentName.OPS, "business administration and online operations", ("admin", "operations", "workflow", "form", "upload")),
    AgentSpec(AgentName.FINANCE, "income, costs, reconciliation and reporting", ("finance", "income", "cost", "payment", "reconcile", "revenue")),
    AgentSpec(AgentName.PIXEL, "marketing content and creative assets", ("content", "creative", "video", "thumbnail", "marketing")),
    AgentSpec(AgentName.SEO, "search optimisation and backlinks", ("seo", "search", "keyword", "backlink", "ranking")),
    AgentSpec(AgentName.GUARDIAN, "security, permissions and compliance", ("security", "permission", "credential", "compliance", "risk")),
    AgentSpec(AgentName.PM, "project management and coordination", ("project", "deadline", "coordinate", "priority", "plan")),
)


def list_agents() -> tuple[AgentSpec, ...]:
    """Return the immutable specialist catalogue."""
    return AGENTS


def route_objective(objective: str) -> AgentSpec:
    """Select the best specialist without performing any work.

    Ties and no-match objectives intentionally fall back to PM so every
    objective has an explicit coordinator and can be reviewed before action.
    """
    text = objective.strip().lower()
    if not text:
        raise ValueError("objective is required")
    scored = [(sum(word in text for word in spec.keywords), spec) for spec in AGENTS]
    best_score, best = max(scored, key=lambda item: item[0])
    if best_score == 0 or sum(score == best_score for score, _ in scored) > 1:
        return next(spec for spec in AGENTS if spec.name is AgentName.PM)
    return best
