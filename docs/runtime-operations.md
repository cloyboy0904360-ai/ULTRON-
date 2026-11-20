# Runtime, backups and operations

## Local/container runtime

Build with `docker build -t ultron .` and run with a strong
`ULTRON_API_KEYS` value supplied through the host environment. Do not commit
secrets or put them in this file. `compose.yaml` persists SQLite data in the
`ultron_data` volume and runs as a non-root user with a read-only container
filesystem.

## Database backup

Run `python -m scripts.backup_db --database-path /data/ultron.db --backup-dir /data/backups --keep 7`
on the host or in a scheduled container job. It uses SQLite's online backup
API, verifies source and destination integrity, writes a SHA-256 manifest and
retains the newest configured number of its own backups. Restrict access to
the backup directory. Copy backups to an independent off-host location using
a separately approved free storage arrangement; this script does not provide
off-site protection.

## Daily reporting\n\nThe Compose stack runs a separate report worker. It writes one aggregate JSON report per UTC day to `/data/reports`, with operation counts, integration evidence totals and ledger totals in pence. It excludes customer contact details. Reports remain on the same Docker volume; configure an independent off-host copy and notification destination separately.\n\n## Monitoring and scheduled checks

`GET /health` is a liveness check. `GET /ready` checks database readiness
and reports unconfigured or unreachable integrations as blocked/degraded.
`GET /integrations/status` requires the `integrations:read` API scope.
The GitHub CI workflow runs on pull requests, main pushes and a nightly UTC
schedule, including compilation, tests and dependency vulnerability checks.

A hosted API URL, persistent host, off-site backup target and alert destination
have not been configured in this repository. A scheduled CI run is not a
substitute for monitoring a deployed service.
