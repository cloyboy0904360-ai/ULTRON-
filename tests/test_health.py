from ultron.db import Database
from ultron.health import check_readiness


def test_readiness_includes_secret_safe_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "not-returned")
    with Database(tmp_path / "health.db") as db:
        result = check_readiness(db, [])
    assert result["database"] == "ok"
    github = next(item for item in result["capabilities"] if item["integration"] == "github")
    assert github["status"] == "CONFIGURED"
    assert "not-returned" not in str(result)
