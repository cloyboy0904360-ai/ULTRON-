"""Durable task leases, retries and recovery records.

This layer is intentionally independent of any particular worker or agent.
It provides atomic claim/lease semantics over the existing SQLite Database,
so a crashed worker can be recovered without duplicate active claims.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Database


_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_recovery (
    id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL UNIQUE,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_until TEXT,
    last_error TEXT,
    result TEXT,
    evidence TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    next_attempt_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_recovery_due ON task_recovery(status, next_attempt_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


class RecoveryStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        with self.db.transaction() as conn:
            conn.executescript(_SCHEMA)

    def enqueue(self, *, task_key: str, objective: str, max_attempts: int = 3) -> dict[str, Any]:
        if not task_key.strip() or not objective.strip():
            raise ValueError("task_key and objective are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = _iso(_now())
        task_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT OR IGNORE INTO task_recovery "
            "(id, task_key, objective, status, max_attempts, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, task_key, objective.strip(), "READY", max_attempts, now, now),
        )
        return self.get(task_key)

    def get(self, task_key: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM task_recovery WHERE task_key = ?", (task_key,))
        if not row:
            raise KeyError(f"Unknown recovery task: {task_key}")
        row["evidence"] = json.loads(row["evidence"] or "[]")
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def claim(self, *, task_key: str, worker: str, lease_seconds: int = 300) -> dict[str, Any] | None:
        if lease_seconds < 1 or not worker.strip():
            raise ValueError("worker and a positive lease are required")
        now = _now()
        until = _iso(now + timedelta(seconds=lease_seconds))
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE task_recovery SET status='RUNNING', lease_owner=?, lease_until=?, "
                "attempts=attempts+1, updated_at=?, last_error=NULL "
                "WHERE task_key=? AND attempts < max_attempts AND "
                "(status IN ('READY','FAILED','BLOCKED') OR lease_until IS NULL OR lease_until < ?)",
                (worker, until, _iso(now), task_key, _iso(now)),
            )
            if cur.rowcount != 1:
                return None
        return self.get(task_key)

    def heartbeat(self, *, task_key: str, worker: str, lease_seconds: int = 300) -> dict[str, Any]:
        now = _now()
        cur = self.db.execute(
            "UPDATE task_recovery SET lease_until=?, updated_at=? WHERE task_key=? "
            "AND status='RUNNING' AND lease_owner=?",
            (_iso(now + timedelta(seconds=lease_seconds)), _iso(now), task_key, worker),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task is not owned by this worker")
        return self.get(task_key)

    def finish(self, *, task_key: str, worker: str, result: dict[str, Any] | None = None, evidence: str = "") -> dict[str, Any]:
        return self._close(task_key, worker, "VERIFIED", result=result, evidence=evidence)

    def fail(self, *, task_key: str, worker: str, error: str, retry_at: datetime | None = None) -> dict[str, Any]:
        if not error.strip():
            raise ValueError("error is required")
        return self._close(task_key, worker, "FAILED", error=error, retry_at=retry_at)

    def recover_expired(self) -> int:
        now = _iso(_now())
        cur = self.db.execute(
            "UPDATE task_recovery SET status='FAILED', lease_owner=NULL, lease_until=NULL, "
            "last_error='lease expired', updated_at=? WHERE status='RUNNING' AND lease_until < ?",
            (now, now),
        )
        return cur.rowcount

    def _close(self, task_key: str, worker: str, status: str, *, result: dict[str, Any] | None = None, evidence: str = "", error: str | None = None, retry_at: datetime | None = None) -> dict[str, Any]:
        now = _now()
        cur = self.db.execute(
            "UPDATE task_recovery SET status=?, lease_owner=NULL, lease_until=NULL, "
            "last_error=?, result=?, evidence=?, next_attempt_at=?, updated_at=? "
            "WHERE task_key=? AND status='RUNNING' AND lease_owner=?",
            (status, error, json.dumps(result) if result is not None else None,
             json.dumps([evidence]) if evidence else "[]", _iso(retry_at) if retry_at else None,
             _iso(now), task_key, worker),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task is not owned by this worker")
        return self.get(task_key)
