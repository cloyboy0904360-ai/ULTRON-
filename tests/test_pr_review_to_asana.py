"""Mock-based tests for scripts/pr_review_to_asana.py.

These tests use fake HTTP callers only. They prove the workflow LOGIC;
they are not evidence of live GitHub or Asana integration.
"""

import json

import pytest

import pr_review_to_asana as mod


class FakeCaller:
    """Matches requests by (method, url substring) to queued responses."""

    def __init__(self):
        self.routes: list[dict] = []
        self.requests: list[dict] = []

    def add(self, method, url_part, responses):
        if not isinstance(responses, list):
            responses = [responses]
        self.routes.append(
            {"method": method, "url_part": url_part, "responses": list(responses)}
        )

    def __call__(self, method, url, headers, json_body):
        self.requests.append({"method": method, "url": url, "body": json_body})
        # Most specific URL first so "/pulls/7" does not swallow
        # "/pulls/7/reviews/424242".
        for route in sorted(self.routes, key=lambda r: -len(r["url_part"])):
            if route["method"] == method and route["url_part"] in url:
                if not route["responses"]:
                    raise AssertionError(f"Route exhausted: {method} {url}")
                item = route["responses"].pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"No route for {method} {url}")

    def count(self, method, url_part):
        return sum(
            1 for r in self.requests
            if r["method"] == method and url_part in r["url"]
        )


REPO = "cloyboy0904360-ai/ULTRON-"
PR = 7
REVIEW_ID = 424242
WS = "1217093232814223"

PR_PAYLOAD = {"number": PR, "html_url": f"https://github.com/{REPO}/pull/{PR}",
              "title": "Fix things"}
REVIEW_PAYLOAD = {
    "id": REVIEW_ID, "state": "changes_requested",
    "user": {"login": "reviewer1"}, "body": "Please fix the tests.",
    "commit_id": "abc123def456",
}


def make_event(tmp_path, state="changes_requested", reviewer="reviewer1",
               body="Please fix the tests.", pr_number=PR):
    event = {
        "action": "submitted",
        "review": {
            "id": REVIEW_ID, "state": state,
            "user": ({"login": reviewer} if reviewer else None),
            "body": body, "commit_id": "abc123def456",
        },
        "pull_request": {"number": pr_number},
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    return {
        "GITHUB_EVENT_NAME": "pull_request_review",
        "GITHUB_EVENT_PATH": str(path),
        "GITHUB_REPOSITORY": REPO,
        "GH_TOKEN": "fake-gh-token",
        "ASANA_ACCESS_TOKEN": "fake-asana-token",
        "ASANA_WORKSPACE_GID": WS,
    }


def wire_happy_path(caller, review=REVIEW_PAYLOAD, inline=None, comments=None):
    caller.add("GET", f"/pulls/{PR}", (200, PR_PAYLOAD))
    caller.add("GET", f"/reviews/{REVIEW_ID}", (200, review))
    caller.add("GET", f"/reviews/{REVIEW_ID}/comments", (200, inline or []))
    caller.add("GET", f"/issues/{PR}/comments", (200, comments or []))
    caller.add("GET", f"/workspaces/{WS}", (200, {"data": {"gid": WS}}))
    caller.add("POST", "/tasks", (201, {"data": {
        "gid": "task-1", "permalink_url": "https://app.asana.com/0/0/task-1"}}))
    caller.add("POST", f"/issues/{PR}/comments", (201, {"id": 99}))


def run_script(tmp_path, env, caller):
    return mod.run(env, caller=caller, sleep=lambda *_: None,
                   recovery_path=str(tmp_path / "recovery.json"))


# 1. Review submitted (happy path: changes requested -> task created)
def test_review_submitted_creates_task(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    result = run_script(tmp_path, make_event(tmp_path), caller)
    assert result["action"] == "create_task"
    assert result["asana_task_gid"] == "task-1"
    assert result["asana_task_url"] == "https://app.asana.com/0/0/task-1"
    assert result["reviewer"] == "reviewer1"
    assert result["commit_sha"] == "abc123def456"
    assert caller.count("POST", "/tasks") == 1
    assert caller.count("POST", f"/issues/{PR}/comments") == 1


# 2. Changes requested is actionable
def test_changes_requested_is_actionable(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    result = run_script(tmp_path, make_event(tmp_path, state="changes_requested"), caller)
    assert result["action"] == "create_task"


# 3. Approved review creates no task
def test_approved_review_skips(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller, review={**REVIEW_PAYLOAD, "state": "approved"})
    result = run_script(tmp_path, make_event(tmp_path, state="approved"), caller)
    assert result["action"] == "skip"
    assert caller.count("POST", "/tasks") == 0


# 4. Review with inline comments is actionable even when state=commented
def test_commented_review_with_inline_comments_creates_task(tmp_path):
    caller = FakeCaller()
    inline = [{"path": "app.py", "body": "This will crash on None"}]
    wire_happy_path(caller, review={**REVIEW_PAYLOAD, "state": "commented", "body": ""},
                    inline=inline)
    result = run_script(tmp_path, make_event(tmp_path, state="commented", body=""), caller)
    assert result["action"] == "create_task"
    task_body = next(r["body"] for r in caller.requests if r["url"].endswith("/tasks"))
    assert "app.py" in task_body["data"]["notes"]
    assert "This will crash on None" in task_body["data"]["notes"]
    assert "fake-asana-token" not in task_body["data"]["notes"]


# 5. Empty review (changes requested, no body) still creates a task
def test_empty_review_body_creates_task(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller, review={**REVIEW_PAYLOAD, "body": ""})
    result = run_script(tmp_path, make_event(tmp_path, body=""), caller)
    assert result["action"] == "create_task"
    task_body = next(r["body"] for r in caller.requests if r["url"].endswith("/tasks"))
    assert "(no written findings)" in task_body["data"]["notes"]


# 6. Missing reviewer is rejected
def test_missing_reviewer_fails(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller, review={**REVIEW_PAYLOAD, "user": None})
    env = make_event(tmp_path, reviewer=None)
    # Event has no reviewer and API returns none either.
    with pytest.raises(mod.ProcessingError, match="reviewer"):
        run_script(tmp_path, env, caller)


# 7. Invalid PR number is rejected
def test_invalid_pr_number_fails(tmp_path):
    caller = FakeCaller()
    env = make_event(tmp_path, pr_number=-3)
    with pytest.raises(mod.ProcessingError, match="PR number"):
        run_script(tmp_path, env, caller)


# 8. Missing Asana secret: fail safely and preserve the event
def test_missing_asana_secret_fails_safely(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    env = make_event(tmp_path)
    del env["ASANA_ACCESS_TOKEN"]
    with pytest.raises(mod.ProcessingError, match="ASANA_ACCESS_TOKEN"):
        run_script(tmp_path, env, caller)
    recovery = json.loads((tmp_path / "recovery.json").read_text())
    assert recovery["needs_replay"] is True
    assert recovery["context"]["review_id"] == REVIEW_ID
    assert caller.count("POST", "/tasks") == 0


# 9. Asana authentication failure is not retried
def test_asana_auth_failure(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    caller.routes = [
        r for r in caller.routes if not (r["method"] == "GET" and "workspaces" in r["url_part"])
    ]
    caller.add("GET", f"/workspaces/{WS}", (401, {"errors": [{"message": "bad token"}]}))
    with pytest.raises(mod.ProcessingError, match="authentication"):
        run_script(tmp_path, make_event(tmp_path), caller)
    assert caller.count("GET", f"/workspaces/{WS}") == 1  # no retry on 401
    assert json.loads((tmp_path / "recovery.json").read_text())["needs_replay"]


# 10. Asana rate limiting: retried and succeeds
def test_asana_rate_limit_retried(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    caller.routes = [r for r in caller.routes if r["url_part"] != "/tasks"]
    caller.add("POST", "/tasks", [
        (429, {"errors": [{"message": "rate limited"}], "_retry_after": "0"}),
        (201, {"data": {"gid": "task-9", "permalink_url": "https://app.asana.com/0/0/task-9"}}),
    ])
    result = run_script(tmp_path, make_event(tmp_path), caller)
    assert result["asana_task_gid"] == "task-9"
    assert caller.count("POST", "/tasks") == 2


# 11. Asana server error: exhausts retries, preserves event
def test_asana_server_error_exhausts_retries(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    caller.routes = [r for r in caller.routes if r["url_part"] != "/tasks"]
    caller.add("POST", "/tasks", [(500, {"errors": [{"message": "boom"}]})] * mod.MAX_RETRIES)
    with pytest.raises(mod.ProcessingError):
        run_script(tmp_path, make_event(tmp_path), caller)
    assert caller.count("POST", "/tasks") == mod.MAX_RETRIES
    assert json.loads((tmp_path / "recovery.json").read_text())["needs_replay"]


# 12. Duplicate event delivery is idempotent via the marker comment
def test_duplicate_delivery_skipped(tmp_path):
    marker = mod.marker_for(REPO, PR, REVIEW_ID)
    caller = FakeCaller()
    wire_happy_path(caller, comments=[{"body": f"done\n<!-- {marker} -->"}])
    result = run_script(tmp_path, make_event(tmp_path), caller)
    assert result["action"] == "skip"
    assert result["reason"].startswith("already processed")
    assert caller.count("POST", "/tasks") == 0


# 13. Failure after task creation records the created task for recovery
def test_failure_after_task_creation_preserved(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    caller.routes = [
        r for r in caller.routes
        if not (r["method"] == "POST" and "/issues/" in r["url_part"])
    ]
    caller.add("POST", f"/issues/{PR}/comments",
               [(500, {"message": "github down"})] * mod.MAX_RETRIES)
    with pytest.raises(mod.ProcessingError, match="task-1"):
        run_script(tmp_path, make_event(tmp_path), caller)
    recovery = json.loads((tmp_path / "recovery.json").read_text())
    assert recovery["needs_replay"] is True
    assert "task-1" in recovery["reason"]


# 14. Recovery after interrupted execution: replay completes and records
#     the Asana reference in GitHub
def test_recovery_replay_completes(tmp_path):
    # First run: task created, marker comment failed.
    caller1 = FakeCaller()
    wire_happy_path(caller1)
    caller1.routes = [
        r for r in caller1.routes
        if not (r["method"] == "POST" and "/issues/" in r["url_part"])
    ]
    caller1.add("POST", f"/issues/{PR}/comments",
                [(500, {"message": "down"})] * mod.MAX_RETRIES)
    with pytest.raises(mod.ProcessingError):
        run_script(tmp_path, make_event(tmp_path), caller1)
    recovery = json.loads((tmp_path / "recovery.json").read_text())
    assert recovery["context"]["review_id"] == REVIEW_ID

    # Replay: the marker now exists (ops recorded the task manually), so the
    # replay is a safe no-op instead of creating a duplicate.
    caller2 = FakeCaller()
    marker = mod.marker_for(REPO, PR, REVIEW_ID)
    wire_happy_path(caller2, comments=[{"body": f"<!-- {marker} -->"}])
    result = run_script(tmp_path, make_event(tmp_path), caller2)
    assert result["action"] == "skip"
    assert caller2.count("POST", "/tasks") == 0


def test_workflow_dispatch_dry_run_creates_nothing(tmp_path):
    caller = FakeCaller()
    wire_happy_path(caller)
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPO,
        "GH_TOKEN": "fake-gh-token",
        "ASANA_ACCESS_TOKEN": "fake-asana-token",
        "ASANA_WORKSPACE_GID": WS,
        "GITHUB_EVENT_PATH": str(tmp_path / "dispatch.json"),
    }
    (tmp_path / "dispatch.json").write_text(json.dumps({
        "inputs": {"pr_number": PR, "review_id": REVIEW_ID, "dry_run": True}
    }))
    result = run_script(tmp_path, env, caller)
    assert result["action"] == "dry_run"
    assert caller.count("POST", "/tasks") == 0
