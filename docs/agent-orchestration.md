# Specialist-agent orchestration

The `ultron.agents` module is the first safe orchestration layer for ULTRON.
It provides a fixed, auditable catalogue for NOVA, FORGE, SALES, OPS,
FINANCE, PIXEL, SEO, GUARDIAN and PM.

`route_objective()` is deterministic and has no network or filesystem side
effects. Unknown or ambiguous objectives route to PM, which keeps planning
explicit rather than silently selecting an unsafe specialist.

This registry does not grant permissions or execute external actions. Any
future executor must still use the existing operation lifecycle, approval
requirements, integration health checks and evidence gates.
