# ULTRON Repository Audit — 18 November 2026

Scope: `main` branch, all branches (`copilot/revenue-operations-process`),
open PRs, workflows, configuration. Repository state at audit time:
two files (`README.md`, `.github/workflows/github-pr-review-to-asana.yml`),
one draft documentation PR (#1), one successful workflow_dispatch run.

## Findings

| # | Finding | Severity | Evidence | Root cause | Repair status |
|---|---------|----------|----------|------------|----------------|
| 1 | No application code existed at all — no entry point, database, API, auth, error handling, logging or tests | **Critical** | Full-tree listing showed only `README.md` (21 bytes) + one workflow | Repository never progressed past documentation | **Repaired** — full Python application under `ultron/` with SQLite persistence, FastAPI API, auth, health checks, JSON logging, 58 passing tests |
| 2 | No persistence of business records | **Critical** | No database code, schema or storage config in repo | Not implemented | **Repaired** — `ultron/db.py` schema (operations, prospects, customers, products, SKUs, orders, invoices, payments, refunds, fulfilments, delivery evidence, ledger); restart persistence verified by smoke test |
| 3 | No operations controller, statuses or completion gates | **Critical** | Nothing to inspect | Not implemented | **Repaired** — `ultron/core/` with the 7 required statuses, ordered completion gates, evidence requirement for VERIFIED, approvals, duplicate prevention, safe retry |
| 4 | No revenue engine | **Critical** | Nothing to inspect | Not implemented | **Repaired** — `ultron/revenue/` implementing the 12-stage pipeline with enforced financial invariants (potential ≠ received, authorisation ≠ settled, no delivery without evidence, refunds capped) |
| 5 | Workflow triggered on wrong event: `pull_request` `review_requested` instead of submitted reviews | **High** | Old workflow `on:` block | Wrong event chosen | **Repaired** — now `pull_request_review: [submitted]` + `workflow_dispatch` replay |
| 6 | Workflow never read the actual review, its state, inline comments, or commit SHA | **High** | Old workflow only fetched PR metadata | Incomplete implementation | **Repaired** — `scripts/pr_review_to_asana.py` fetches review + inline comments + commit SHA and includes them in the task |
| 7 | Workflow created tasks for irrelevant events (every review request) and claimed "exactly-once" without guaranteeing it | **High** | Step name "Create exactly-once Asana follow-up"; trigger fired on assignment, not review outcome | Overclaimed semantics | **Repaired** — only CHANGES_REQUESTED and COMMENTED-with-inline-comments create tasks; header documents at-least-once + idempotent marker, not exactly-once |
| 8 | Hardcoded, unverified Asana assignee GID `1217093232814211`; workspace never verified | **High** | `ASSIGNEE_GID` env literal in old workflow | Assumed account permissions | **Repaired** — assignee removed; workspace is verified via API before task creation; project attached only if `ASANA_PROJECT_GID` verifies; tasks created at workspace level otherwise |
| 9 | No rate-limit handling or retries on Asana/GitHub calls | **Medium** | Single `curl --fail-with-body` in old workflow | Missing resilience | **Repaired** — `call_with_retry` with exponential backoff, honours `Retry-After`, no retry on 401/403/404 |
| 10 | No recovery path on failure mid-run (e.g. after task creation) | **Medium** | Old workflow had no failure handling | Missing design | **Repaired** — recovery record (`asana-recovery.json`) written on failure, uploaded as a workflow artifact; replay via `workflow_dispatch`; failure-after-creation records the created task GID |
| 11 | Asana task notes contained literal `\n` sequences | **Low** | `NOTES="...\n..."` in bash double quotes passed through `jq --arg` | Bash/jq escaping bug | **Repaired** — Python builds notes with real newlines; covered by tests |
| 12 | Duplicate detection race (check-then-act within one run only) | **Medium** | Old marker check | No concurrency control | **Repaired** — Actions concurrency group per (repo, PR, review); idempotency marker per review id; duplicate-delivery test passes |
| 13 | No CI to run tests | **Medium** | No CI workflow existed | Not implemented | **Repaired** — `.github/workflows/ci.yml` runs pytest on push/PR |
| 14 | Workflow permissions acceptable but untrusted-code risk not addressed | **Medium** | Old workflow had no checkout; risk would appear once code ran | Not addressed | **Repaired** — checkout pinned to `github.event.repository.default_branch`; no `pull_request_target`; no PR content executed as shell |
| 15 | Secrets handling: token passed inline to curl (process-list exposure), no log-redaction anywhere | **Medium** | Old workflow curl header | Unsafe handling | **Repaired** — token via env/header in Python, never printed; `ultron/logging_config.py` redacts token-shaped values; test asserts redaction |
| 16 | No integration adapters (GitHub, Asana, Make, Shopify, Stripe, eBay) | **High** | Nothing to inspect | Not implemented | **Repaired** — `ultron/adapters/` with health checks, permission probes, retries, timeouts, evidence records, BLOCKED status when unconfigured |
| 17 | README contained no installation/startup information | **Low** | 21-byte stub | Not written | **Repaired** — full README with architecture, config table, startup, rules |

## Verification evidence

- `python -m pytest` → **58 passed** (operations, revenue, adapters, API, and all 14 required workflow scenarios).
- Live smoke test: `uvicorn ultron.api.app:create_app --factory` booted; `GET /health` → `{"status":"ok"}`; operation created; server killed and restarted; operation persisted (SQLite).
- GitHub adapter health check succeeded live (token present in environment). Asana/Make/Shopify/Stripe/eBay correctly report `BLOCKED` when unconfigured.

## Not verified (honest limits)

- No live Asana/Shopify/Stripe/eBay/Make calls were made from tests; those paths are mock-tested only. Live verification requires the real secrets configured in repository settings.
- Asana task creation against workspace `1217093232814223` has not been executed live from this audit; the script verifies the workspace at runtime and fails safely with a recovery artifact if unauthorised.
- As the account reports zero accessible projects, tasks are created at workspace level; set `ASANA_PROJECT_GID` only after the project is verified.
