"""Financial ledger: received revenue, costs and reconciled profit.

Only settled payments and refunds create revenue entries. Issued
invoices, proposals and pipeline estimates never touch the ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from ..errors import ValidationError
from ..logging_config import get_logger

log = get_logger("ultron.revenue.ledger")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        kind: str,
        source_type: str,
        source_id: str,
        amount_pence: int,
        note: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"REVENUE_RECEIVED", "COST"}:
            raise ValidationError(f"Unknown ledger kind: {kind}")
        entry_id = uuid.uuid4().hex
        self.db.execute(
            """INSERT INTO ledger_entries
               (id, kind, source_type, source_id, amount_pence, recorded_at, note)
               VALUES (?,?,?,?,?,?,?)""",
            (entry_id, kind, source_type, source_id, amount_pence, _now(), note),
        )
        log.info(
            "ledger entry recorded",
            extra={"context": {"kind": kind, "source_type": source_type,
                               "source_id": source_id, "amount_pence": amount_pence}},
        )
        return self.db.query_one("SELECT * FROM ledger_entries WHERE id = ?", (entry_id,))

    def totals(self) -> dict[str, int]:
        revenue = self.db.query_one(
            "SELECT COALESCE(SUM(amount_pence), 0) AS total FROM ledger_entries "
            "WHERE kind = 'REVENUE_RECEIVED'"
        )["total"]
        costs = self.db.query_one(
            "SELECT COALESCE(SUM(amount_pence), 0) AS total FROM ledger_entries "
            "WHERE kind = 'COST'"
        )["total"]
        return {
            "received_revenue_pence": revenue,
            "costs_pence": costs,
            "profit_pence": revenue - costs,
        }

    def entries(self, *, source_type: str | None = None) -> list[dict[str, Any]]:
        if source_type:
            return self.db.query_all(
                "SELECT * FROM ledger_entries WHERE source_type = ? ORDER BY recorded_at",
                (source_type,),
            )
        return self.db.query_all("SELECT * FROM ledger_entries ORDER BY recorded_at")
