# ULTRON

Business operating system: a central operations controller, a revenue
engine and verified integration adapters, exposed over an HTTP API with
SQLite persistence.

## Architecture

```
ultron/
├── config.py          Environment configuration with startup validation
├── db.py              SQLite persistence (schema + transactions)
├── auth.py            API-key authentication and scopes
├── errors.py          Structured error types
├── logging_config.py  JSON logging with secret redaction
├── health.py          Liveness and readiness checks
├── core/
│   ├── gates.py       CORRECT→COMPLETE→CONNECTED→WORKING→LIVE→MEASURED→EVIDENCE_CAPTURED
│   └── operations.py  Operations controller (statuses, approvals, retries, evidence)
├── revenue/
│   ├── models.py      Pipeline / order / invoice / payment / fulfilment enums
│   ├── ledger.py      Received revenue, costs, profit
│   └── engine.py      Prospects→…→GET PAID business rules
├── research.py         Free RSS collection, evidence scoring and reports
├── adapters/          GitHub, Asana, Make, Shopify, Stripe, eBay
├── api/app.py         FastAPI routes (thin; rules live in the engine)
└── jobs/scheduler.py  Background jobs tracked as operations
scripts/
└── pr_review_to_asana.py   PR-review → Asana follow-up (used by the workflow)
tests/                 pytest suite
```

## Installation

```bash
pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # + pytest, httpx for tests
```

## Startup

```bash
export ULTRON_DATABASE_PATH=/var/lib/ultron/ultron.db
export ULTRON_API_KEYS="key1:operations:write,key2:revenue:write,revenue:read"  # required in production
uvicorn ultron.api.app:create_app --factory --port 8000
```

Health: `GET /health` (liveness), `GET /ready` (database + integration status).

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `ULTRON_DATABASE_PATH` | no (default `ultron.db`) | SQLite file location |
| `ULTRON_API_KEYS` | in production | Comma-separated `key:scope,scope` entries |
| `ULTRON_ENV` | no | `development` | `test` | `staging` | `production` |
| `ULTRON_LOG_LEVEL` | no | Python logging level |
| `ULTRON_HTTP_TIMEOUT_SECONDS` | no | Adapter request timeout (default 15) |
| `ULTRON_MAX_RETRIES` | no | Adapter retry attempts (default 3) |
| `GITHUB_TOKEN`, `ASANA_ACCESS_TOKEN`, `ASANA_WORKSPACE_GID`, `MAKE_API_TOKEN`, `SHOPIFY_SHOP`, `SHOPIFY_ACCESS_TOKEN`, `STRIPE_SECRET_KEY`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | optional | Integration credentials. Absent credentials mark that integration **BLOCKED** — they never crash the app. |

Invalid configuration fails fast at startup with every problem listed.

## Core rules enforced by code (and tests)

- An operation can only be `VERIFIED` after all seven completion gates
  passed in order, each with evidence.
- Potential revenue (estimates, proposals, issued invoices) is separate
  from received revenue (settled payments).
- An unpaid invoice is never a sale received.
- A payment authorisation is never settled bank funds; settlement
  requires verification evidence.
- An order is never marked delivered without delivery evidence.
- Refunds can never exceed the settled amount.
- Idempotency keys on operations/orders and provider references on
  payments prevent duplicates; failed operations retry safely via
  `POST /operations/{id}/retry`.

## Free revenue opportunity research

The first R&D collector is implemented in `ultron/research.py`. It reads
public RSS/Atom feeds from `config/research-feeds.json`, deduplicates entries,
scores evidence/actionability (not win probability), and writes:
- `data/revenue-opportunities.json`
- `reports/revenue-opportunities.md`

GitHub Actions runs it daily at 09:00 UTC and supports manual dispatch.
The workflow uses no third-party Actions, AI API calls, paid APIs, secrets
or paid connectors. It is configured for a public repository, uses a
five-minute timeout and commits only the report/data output.

Google News RSS feeds are discovery leads only: the endpoint is an observed
community interface rather than a documented API, links may redirect and
feed content may omit the original article body. Verify every lead against
its original source before acting. The report keeps advertised opportunity
values separate from collected revenue; the payment ledger is not connected
in this first version. See [docs/revenue-research.md](docs/revenue-research.md).

## GitHub PR review → Asana

`.github/workflows/github-pr-review-to-asana.yml` reacts to **submitted**
reviews. It creates an Asana follow-up task for `CHANGES_REQUESTED`
reviews and for `COMMENTED` reviews that contain inline comments, then
records the task URL on the PR. Delivery is at-least-once with
idempotent per-review markers — it is not claimed to be exactly-once.
Replay manually via `workflow_dispatch` with a PR number.

Required repository configuration: secret `ASANA_ACCESS_TOKEN`, variable
`ASANA_WORKSPACE_GID`, optional variable `ASANA_PROJECT_GID` (verified
before use; never fabricated).

## Tests

```bash
python -m pytest
```

Integration tests are mock-based by design. Mocks prove workflow logic
only; they are not evidence of live integration. Live adapter status is
visible at `GET /integrations/status` or `GET /ready`.
