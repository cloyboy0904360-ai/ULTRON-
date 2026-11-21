# Specialist-agent orchestration

The `ultron.agents` module provides a fixed, auditable catalogue for NOVA,
FORGE, SALES, OPS, FINANCE, PIXEL, SEO, GUARDIAN and PM.

`route_objective()` is deterministic and has no network or filesystem side
effects. Unknown or ambiguous objectives route to PM, which keeps planning
explicit rather than silently selecting an unsafe specialist.

`ObjectiveExecutor` adds the safe execution boundary. Applications inject
handlers explicitly; the executor routes the objective, persists a durable
recovery record, claims a worker lease, records success or failure, and never
chooses credentials or performs provider calls implicitly.

Handlers should use ULTRON operation approvals, integration health checks and
evidence gates before performing external or consequential actions.
