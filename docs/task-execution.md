# ULTRON task execution

The FastAPI workforce routes expose `GET /agents`,
`POST /agents/{agent_name}/tasks`, and
`POST /agents/tasks/{operation_id}/execute`.

The executor only invokes explicitly registered in-process Python handlers.
A role without a registered handler is moved to BLOCKED with an explanation.
A task requiring approval moves to AWAITING APPROVAL before its handler can
run. Handlers must return non-empty evidence for all seven ordered completion
gates or the task cannot become VERIFIED.

This does not create independent AI agents or connect an LLM automatically.
Production handlers must be reviewed, tested and registered by the deployment
owner. External actions must have their own permission and approval controls.
