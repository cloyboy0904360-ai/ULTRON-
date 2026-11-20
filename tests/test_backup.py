from __future__ import annotations

import hashlib
import json
import sqlite3

from scripts.backup_db import backup_database
from ultron.db import Database


def test_backup_is_integrity_checked_and_has_sha256_manifest(tmp_path):
    source = tmp_path / "ultron.db"
    db = Database(source)
    db.execute(
        "INSERT INTO operations (id, description, owner, priority, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("op-1", "backup test", "OPS", 3, "READY", "now", "now"),
    )
    db.close()

    result = backup_database(source, tmp_path / "backups")
    backup_path = tmp_path / "backups" / result["backup"]
    manifest = json.loads(backup_path.with_suffix(".json").read_text())

    assert result["integrity_check"] == "ok"
    assert manifest["sha256"] == hashlib.sha256(backup_path.read_bytes()).hexdigest()
    with sqlite3.connect(backup_path) as conn:
        assert conn.execute("SELECT description FROM operations").fetchone()[0] == "backup test"


def test_backup_refuses_missing_database(tmp_path):
    try:
        backup_database(tmp_path / "missing.db", tmp_path / "backups")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing database should be rejected")
