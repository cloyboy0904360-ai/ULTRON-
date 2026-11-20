"""Write a privacy-conscious daily ULTRON operations summary."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ultron.db import Database
from ultron.revenue.ledger import Ledger


def build_report(database_path: str | Path) -> dict[str, Any]:
    with Database(database_path) as db:
        operations = db.query_all(
            "SELECT status, COUNT(*) AS count FROM operations GROUP BY status ORDER BY status"
        )
        integrations = db.query_all(
            """SELECT integration,
                      SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes,
                      SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) AS failures,
                      MAX(at) AS last_event
               FROM integration_evidence GROUP BY integration ORDER BY integration"""
        )
        ledger = Ledger(db).totals()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "operations_by_status": {row["status"]: row["count"] for row in operations},
            "integration_evidence": integrations,
            "financial_ledger_pence": ledger,
            "report_scope": "aggregate counts and ledger totals; no customer contact details",
        }


def write_report(database_path: str | Path, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(database_path)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = output / f"ultron-daily-{day}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", default=os.getenv("ULTRON_DATABASE_PATH", "ultron.db"))
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()
    print(write_report(args.database_path, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
