# Guarded execution engine

`ultron.core.executor` adds a provider-neutral execution boundary:

1. Route an objective to one of the registered specialists.
2. Stop at `AWAITING APPROVAL` when the plan requires approval.
3. Execute only an injected, authorised handler.
4. Enforce a timeout and explicit retry limit.
5. Return `VERIFIED` only when the handler supplies non-empty evidence.
6. Return `FAILED` with the final error instead of claiming success.

The engine does not create credentials, call undocumented APIs, spend money,
submit applications, publish content or perform browser actions by itself.
Those capabilities must be added as separately permissioned adapters and
remain behind the existing operation gates.
