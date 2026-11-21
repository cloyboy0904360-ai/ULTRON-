import pytest

from ultron.agents import AgentName, list_agents, route_objective


def test_registry_contains_all_specialists():
    assert {agent.name for agent in list_agents()} == set(AgentName)


def test_routes_objectives_without_side_effects():
    assert route_objective("research new market opportunities").name is AgentName.NOVA
    assert route_objective("fix a software bug and run tests").name is AgentName.FORGE
    assert route_objective("prepare a revenue reconciliation").name is AgentName.FINANCE
    assert route_objective("coordinate the project deadline").name is AgentName.PM


def test_ambiguous_and_unknown_objectives_fall_back_to_pm():
    assert route_objective("make a plan").name is AgentName.PM
    assert route_objective("something unrelated").name is AgentName.PM
    with pytest.raises(ValueError):
        route_objective("   ")
