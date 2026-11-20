# ULTRON workforce

The seven registered roles are NOVA (coordination), FORGE (engineering), PM
(delivery management), GUARDIAN (security), FINANCE (financial control), SALES
(revenue development), and OPS (operations).

The registry is deterministic role metadata. It does not create seven
independent AI processes or claim autonomous execution. Tasks are handed to the
existing SQLite-backed operations controller through `ultron.agents.assign_task`.
Every task begins READY and remains subject to the normal approvals, lifecycle
transitions, completion gates and evidence requirements.

Example:

```python
from ultron.agents import assign_task
from ultron.core.operations import OperationController
from ultron.db import Database

controller = OperationController(Database("ultron.db"))
task = assign_task(
    controller,
    agent="SALES",
    description="Verify supplier stock, UK delivery and net margin",
    priority=1,
    required_approval=True,
)
print(task["id"], task["status"])
```

No customer contact, listing publication, purchase, payment change or other
external action is performed by task assignment.
