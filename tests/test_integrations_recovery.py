from datetime import timedelta

import pytest

from ultron.db import Database
from ultron.integrations import CapabilityRegistry
from ultron.core.recovery import RecoveryStore, _now


def test_capability_registry_never_exposes_values():
    registry = CapabilityRegistry.from_environment({"GITHUB_TOKEN": "secret-value"})
    rows = registry.snapshot()
    github = next(row for row in rows if row["integration"] == "github")
    assert github["status"] == "CONFIGURED"
    assert "secret-value" not in str(rows)
    assert next(row for row in rows if row["integration"] == "stripe")["status"] == "BLOCKED"


def test_recovery_claim_is_idempotent_and_worker_owned(tmp_path):
    with Database(tmp_path / "recovery.db") as db:
        store = RecoveryStore(db)
        store.enqueue(task_key="task-1", objective="research", max_attempts=2)
        assert store.claim(task_key="task-1", worker="w1")
        assert store.claim(task_key="task-1", worker="w2") is None
        with pytest.raises(RuntimeError):
            store.finish(task_key="task-1", worker="w2")
        done = store.finish(task_key="task-1", worker="w1", evidence="run-1")
        assert done["status"] == "VERIFIED"


def test_expired_lease_becomes_recoverable(tmp_path):
    with Database(tmp_path / "recovery.db") as db:
        store = RecoveryStore(db)
        store.enqueue(task_key="task-2", objective="repair", max_attempts=2)
        store.claim(task_key="task-2", worker="w1", lease_seconds=1)
        db.execute("UPDATE task_recovery SET lease_until=? WHERE task_key=?", ((_now() - timedelta(seconds=1)).isoformat(), "task-2"))
        assert store.recover_expired() == 1
        assert store.claim(task_key="task-2", worker="w2")
