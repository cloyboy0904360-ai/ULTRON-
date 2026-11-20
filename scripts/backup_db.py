"""Create and verify an atomic SQLite backup.

This utility backs up a database file on the same host. It does not upload
backups to a remote provider; configure off-host replication separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def backup_database(database_path: str | Path, backup_dir: str | Path, keep: int = 7) -> dict[str, Any]:
    source = Path(database_path)
    target_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    if keep < 1:
        raise ValueError("keep must be at least 1")
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = target_dir / f"ultron-{stamp}.sqlite3"
    fd, temp_name = tempfile.mkstemp(prefix=".ultron-backup-", suffix=".sqlite3", dir=target_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as src:
            check = src.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise sqlite3.DatabaseError(f"Source integrity check failed: {check}")
            with sqlite3.connect(temp_path) as dst:
                src.backup(dst)
                backup_check = dst.execute("PRAGMA integrity_check").fetchone()
                if not backup_check or backup_check[0] != "ok":
                    raise sqlite3.DatabaseError(f"Backup integrity check failed: {backup_check}")
        os.chmod(temp_path, 0o600)
        digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        os.replace(temp_path, final_path)
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source.name,
            "backup": final_path.name,
            "sha256": digest,
            "integrity_check": "ok",
        }
        manifest_path = final_path.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)

        backups = sorted(target_dir.glob("ultron-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        return manifest
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", default=os.getenv("ULTRON_DATABASE_PATH", "ultron.db"))
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(backup_database(args.database_path, args.backup_dir, args.keep), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
