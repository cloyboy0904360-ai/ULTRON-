from __future__ import annotations

import json

from scripts.daily_report import write_report
from ultron.db import Database


def test_daily_report_contains_aggregate_operations_and_financial_totals(tmp_path):
    source = tmp_path / "ultron.db"
    db = Database(source)
    db.execute(
        "INSERT INTO operations (id, description, owner, priority, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("op-1", "private customer task", "SALES", 2, "READY", "now", "now"),
    )
    db.execute(
        "INSERT INTO ledger_entries (id, kind, source_type, source_id, amount_pence, recorded_at) "
        "VALUES (?,?,?,?,?,?)",
        ("ledger-1", "REVENUE_RECEIVED", "payment", "pay-1", 2500, "now"),
    )
    db.close()

    path = write_report(source, tmp_path / "reports")
    report = json.loads(path.read_text())

    assert report["operations_by_status"] == {"READY": 1}
    assert report["financial_ledger_pence"]["received_revenue_pence"] == 2500
    assert "private customer task" not in path.read_text()
