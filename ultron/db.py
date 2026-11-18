"""SQLite persistence layer.

One ``Database`` owns the connection and the schema. All modules share
it so business records survive restarts. JSON columns are used for
list/dict payloads; every write happens inside a transaction.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    description TEXT NOT NULL,
    owner TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    dependencies TEXT NOT NULL DEFAULT '[]',
    required_approval INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    evidence TEXT NOT NULL DEFAULT '[]',
    next_action TEXT,
    error_details TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    gates TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS prospects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact TEXT,
    source TEXT,
    stage TEXT NOT NULL,
    estimated_value_pence INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    prospect_id TEXT REFERENCES prospects(id),
    name TEXT NOT NULL,
    contact TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    sku TEXT NOT NULL UNIQUE,
    unit_price_pence INTEGER NOT NULL CHECK (unit_price_pence >= 0),
    unit_cost_pence INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost_pence >= 0),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    prospect_id TEXT NOT NULL REFERENCES prospects(id),
    status TEXT NOT NULL,
    amount_pence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    sku_id TEXT NOT NULL REFERENCES skus(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_pence INTEGER NOT NULL,
    unit_cost_pence INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    status TEXT NOT NULL,
    amount_pence INTEGER NOT NULL CHECK (amount_pence >= 0),
    issued_at TEXT NOT NULL,
    due_at TEXT,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    provider TEXT NOT NULL,
    provider_reference TEXT UNIQUE,
    status TEXT NOT NULL,
    amount_pence INTEGER NOT NULL CHECK (amount_pence >= 0),
    authorised_at TEXT,
    settled_at TEXT,
    verification_evidence TEXT
);

CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments(id),
    amount_pence INTEGER NOT NULL CHECK (amount_pence > 0),
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fulfilments (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    status TEXT NOT NULL,
    carrier TEXT,
    tracking_reference TEXT,
    dispatched_at TEXT,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS delivery_evidence (
    id TEXT PRIMARY KEY,
    fulfilment_id TEXT NOT NULL REFERENCES fulfilments(id),
    kind TEXT NOT NULL,
    reference TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- REVENUE_RECEIVED | COST
    source_type TEXT NOT NULL,       -- payment | refund | sku_cost | other
    source_id TEXT NOT NULL,
    amount_pence INTEGER NOT NULL,   -- signed: refunds negative
    recorded_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS operation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    integration TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,           -- success | failure
    detail TEXT,
    at TEXT NOT NULL
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite database file."""

    def __init__(self, path: str | Path = "ultron.db"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def query_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def dumps(value: Any) -> str:
    return json.dumps(value)


def loads(raw: str | None, default: Any = None) -> Any:
    if raw is None:
        return default
    return json.loads(raw)
