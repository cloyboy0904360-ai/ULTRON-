from ultron.core.operations import OperationController
from ultron.db import Database
from ultron.jobs.durable import RecoveryAwareScheduler


def test_recovery_aware_scheduler_persists_job_run(tmp_path):
    with Database(tmp_path / "scheduler.db") as db:
        scheduler = RecoveryAwareScheduler(OperationController(db))
        scheduler.run_job_once("research", lambda: "evidence-1")
        rows = db.query_all("SELECT * FROM task_recovery")
        assert len(rows) == 1
        assert rows[0]["status"] == "VERIFIED"
        assert rows[0]["last_error"] is None
