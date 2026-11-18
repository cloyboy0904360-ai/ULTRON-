"""Completion gates for ULTRON operations.

An operation may only become VERIFIED after every gate has passed, in
order, each backed by at least one evidence record.
"""

from __future__ import annotations

from enum import Enum


class Gate(str, Enum):
    CORRECT = "CORRECT"
    COMPLETE = "COMPLETE"
    CONNECTED = "CONNECTED"
    WORKING = "WORKING"
    LIVE = "LIVE"
    MEASURED = "MEASURED"
    EVIDENCE_CAPTURED = "EVIDENCE_CAPTURED"


# Gates must be passed in this exact order.
GATE_ORDER: tuple[Gate, ...] = (
    Gate.CORRECT,
    Gate.COMPLETE,
    Gate.CONNECTED,
    Gate.WORKING,
    Gate.LIVE,
    Gate.MEASURED,
    Gate.EVIDENCE_CAPTURED,
)


def next_gate(passed: list[str]) -> Gate | None:
    """Return the next gate that must be passed, or None if all passed."""
    passed_set = set(passed)
    for gate in GATE_ORDER:
        if gate.value not in passed_set:
            return gate
    return None
