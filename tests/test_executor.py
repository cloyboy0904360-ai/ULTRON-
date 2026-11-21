from ultron.agents import AgentName
from ultron.core.executor import ObjectiveExecutor
from ultron.core.recovery import RecoveryStore
from ultron.db import Database


def test_executor_routes_and_persists_success(tmp_path):
    with Database(tmp_path / "executor.db") as db:
        store = RecoveryStore(db)
        executor = ObjectiveExecutor(
            store,
            {AgentName.NOVA: lambda objective: ({"count": 1}, "research-evidence")},
        )
        submitted = executor.submit(task_key="research-1", objective="discover market trends")
        assert submitted["agent"] == "NOVA"
        result = executor.execute(task_key="research-1", worker="worker-1")
        assert result["status"] == "VERIFIED"
        assert result["result"] == {"count": 1}


def test_executor_records_missing_handler_as_failure(tmp_path):
    with Database(tmp_path / "executor.db") as db:
        executor = ObjectiveExecutor(RecoveryStore(db))
        executor.submit(task_key="code-1", objective="fix software bug")
        result = executor.execute(task_key="code-1", worker="worker-1")
        assert result["status"] == "FAILED"
        assert "No handler" in result["last_error"]
