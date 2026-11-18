#!/usr/bin/env python3
"""Create Asana follow-up tasks for submitted GitHub pull request reviews.

Design notes
------------
- Trigger: ``pull_request_review`` (submitted). ``workflow_dispatch`` is
  supported for manual recovery/replay with an explicit PR + review id.
- Delivery semantics: at-least-once from GitHub; this script is
  idempotent per (repository, PR, review id) via a hidden marker stored
  in a PR comment. It is NOT exactly-once: a crash between Asana task
  creation and marker comment can duplicate the task. The GitHub Actions
  concurrency group prevents simultaneous duplicate runs for the same
  review, which closes the common race.
- Actionable reviews only: CHANGES_REQUESTED always creates a task;
  COMMENTED creates one only when inline comments exist; APPROVED,
  DISMISSED and PENDING never create tasks.
- Fail-safe: if Asana is unreachable or rejects the call, the event
  payload is written to a recovery file and the job exits non-zero so
  the workflow run is visibly failed and can be replayed.
- The Asana workspace is verified before task creation. No project ID is
  ever fabricated: tasks are created at workspace level unless a verified
  project GID is supplied via ``ASANA_PROJECT_GID``.
- Secrets are read from the environment and never printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

GITHUB_API = "https://api.github.com"
ASANA_API = "https://app.asana.com/api/1.0"

MARKER_PREFIX = "asana-review-followup:"
RECOVERY_FILE = "asana-recovery.json"

# Reviews that always produce a follow-up task.
ACTIONABLE_STATES = {"changes_requested"}

MAX_RETRIES = 4


class ProcessingError(Exception):
    """Fatal, non-retryable processing failure."""


class RetryableError(Exception):
    """Transient failure (rate limit, 5xx, network)."""


HttpCaller = Callable[[str, str, dict[str, str], dict[str, Any] | None], tuple[int, Any]]


def _http(
    method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> tuple[int, Any]:
    body = json.dumps(json_body).encode() if json_body is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload: Any = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:300]}
        if isinstance(payload, dict):
            payload["_retry_after"] = (
                exc.headers.get("Retry-After") if exc.headers else None
            )
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise RetryableError(f"network error calling {url}: {exc.reason}") from exc


def call_with_retry(
    caller: HttpCaller,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    *,
    expected: tuple[int, ...] = (200,),
    what: str,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status, payload = caller(method, url, headers, json_body)
        except RetryableError:
            if attempt == MAX_RETRIES:
                raise
            sleep(min(2 ** attempt, 30))
            continue
        if status in expected:
            return payload
        if status == 401 or status == 403:
            raise ProcessingError(f"{what}: authentication/permission failed (HTTP {status})")
        if status == 404:
            raise ProcessingError(f"{what}: not found (HTTP 404)")
        retryable = status == 429 or 500 <= status < 600
        if retryable and attempt < MAX_RETRIES:
            retry_after = payload.get("_retry_after") if isinstance(payload, dict) else None
            delay = min(2 ** attempt, 30)
            if retry_after:
                try:
                    delay = max(delay, min(float(retry_after), 60))
                except ValueError:
                    pass
            sleep(delay)
            continue
        raise ProcessingError(f"{what}: HTTP {status}")
    raise ProcessingError(f"{what}: exhausted retries")


# ------------------------------------------------------------- event input

def load_event(env: dict[str, str]) -> dict[str, Any]:
    """Load and validate the GitHub event. Returns a normalised context."""
    event_name = env.get("GITHUB_EVENT_NAME", "")
    repository = env.get("GITHUB_REPOSITORY", "")
    if not repository or "/" not in repository:
        raise ProcessingError("GITHUB_REPOSITORY is missing or invalid")

    if event_name == "pull_request_review":
        path = env.get("GITHUB_EVENT_PATH")
        if not path:
            raise ProcessingError("GITHUB_EVENT_PATH is missing")
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
        review = event.get("review") or {}
        pr = event.get("pull_request") or {}
        context = {
            "repository": repository,
            "pr_number": pr.get("number"),
            "review_id": review.get("id"),
            "review_state": (review.get("state") or "").lower(),
            "reviewer": (review.get("user") or {}).get("login"),
            "review_body": review.get("body") or "",
            "commit_sha": review.get("commit_id") or "",
            "dry_run": False,
        }
    elif event_name == "workflow_dispatch":
        inputs = event_inputs(env)
        context = {
            "repository": repository,
            "pr_number": inputs.get("pr_number"),
            "review_id": inputs.get("review_id"),
            "review_state": None,   # resolved from the API below
            "reviewer": None,
            "review_body": "",
            "commit_sha": "",
            "dry_run": str(inputs.get("dry_run", "false")).lower() == "true",
        }
    else:
        raise ProcessingError(f"Unsupported event: {event_name!r}")

    if not isinstance(context["pr_number"], int) or context["pr_number"] <= 0:
        try:
            context["pr_number"] = int(str(context["pr_number"]))
        except (TypeError, ValueError):
            raise ProcessingError(f"Invalid PR number: {context['pr_number']!r}")
        if context["pr_number"] <= 0:
            raise ProcessingError(f"Invalid PR number: {context['pr_number']!r}")
    return context


def event_inputs(env: dict[str, str]) -> dict[str, Any]:
    path = env.get("GITHUB_EVENT_PATH")
    if path:
        with open(path, encoding="utf-8") as handle:
            return (json.load(handle).get("inputs") or {})
    return {}


# ----------------------------------------------------------------- helpers

def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def asana_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def marker_for(repository: str, pr_number: int, review_id: Any) -> str:
    return f"{MARKER_PREFIX}{repository}#{pr_number}:{review_id}"


def decide_action(review_state: str, inline_comments: list[dict[str, Any]]) -> str:
    """Return 'create_task' or 'skip' with an explicit reason elsewhere."""
    if review_state in ACTIONABLE_STATES:
        return "create_task"
    if review_state == "commented" and inline_comments:
        return "create_task"
    return "skip"


def build_task_content(context: dict[str, Any], pr: dict[str, Any],
                       inline_comments: list[dict[str, Any]]) -> tuple[str, str]:
    findings = []
    if context["review_body"].strip():
        findings.append(f"Review summary: {context['review_body'].strip()}")
    for comment in inline_comments:
        path = comment.get("path", "?")
        body = (comment.get("body") or "").strip()
        if body:
            findings.append(f"- {path}: {body}")
    findings_text = "\n".join(findings) if findings else "(no written findings)"
    name = f"PR review follow-up: {context['repository']}#{context['pr_number']} ({context['review_state']})"
    notes = (
        "GitHub pull request review follow-up\n\n"
        f"Repository: {context['repository']}\n"
        f"Pull request: {pr.get('html_url')}\n"
        f"Reviewer: {context['reviewer']}\n"
        f"Review state: {context['review_state']}\n"
        f"Commit: {context['commit_sha']}\n\n"
        f"Findings and required actions:\n{findings_text}\n\n"
        f"Idempotency marker: {marker_for(context['repository'], context['pr_number'], context['review_id'])}"
    )
    return name, notes


# -------------------------------------------------------------------- main

def run(
    env: dict[str, str],
    *,
    caller: HttpCaller = _http,
    sleep: Callable[[float], None] = time.sleep,
    recovery_path: str = RECOVERY_FILE,
) -> dict[str, Any]:
    context = load_event(env)
    repo, pr_number = context["repository"], context["pr_number"]

    gh_token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
    if not gh_token:
        raise ProcessingError("GH_TOKEN/GITHUB_TOKEN is not available")
    gh = github_headers(gh_token)

    pr = call_with_retry(
        caller, "GET", f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}", gh,
        what="fetch pull request", sleep=sleep,
    )

    review_id = context["review_id"]
    if review_id is None:
        # workflow_dispatch without a review id: use the latest submitted review.
        reviews = call_with_retry(
            caller, "GET",
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews?per_page=100",
            gh, what="list reviews", sleep=sleep,
        )
        submitted = [r for r in reviews if (r.get("state") or "").lower() != "pending"]
        if not submitted:
            raise ProcessingError(f"No submitted reviews found on PR #{pr_number}")
        latest = submitted[-1]
        review_id = latest.get("id")
        context["review_id"] = review_id
        context["review_state"] = (latest.get("state") or "").lower()
        context["reviewer"] = (latest.get("user") or {}).get("login")
        context["review_body"] = latest.get("body") or ""
        context["commit_sha"] = latest.get("commit_id") or ""
    else:
        review = call_with_retry(
            caller, "GET",
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews/{review_id}",
            gh, what="fetch review", sleep=sleep,
        )
        if context["review_state"] is None:
            context["review_state"] = (review.get("state") or "").lower()
        if context["reviewer"] is None:
            context["reviewer"] = (review.get("user") or {}).get("login")
            context["review_body"] = review.get("body") or ""
            context["commit_sha"] = review.get("commit_id") or ""

    if not context["reviewer"]:
        raise ProcessingError("Review has no reviewer identity; refusing to continue")
    if not context["review_state"]:
        raise ProcessingError("Review state is missing; refusing to continue")

    inline_comments = call_with_retry(
        caller, "GET",
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews/{review_id}/comments?per_page=100",
        gh, what="fetch inline review comments", sleep=sleep,
    )
    if not isinstance(inline_comments, list):
        inline_comments = []

    action = decide_action(context["review_state"], inline_comments)
    result: dict[str, Any] = {
        "repository": repo, "pr_number": pr_number, "review_id": review_id,
        "review_state": context["review_state"], "reviewer": context["reviewer"],
        "commit_sha": context["commit_sha"], "action": action,
        "asana_task_gid": None, "asana_task_url": None,
    }

    if action == "skip":
        result["reason"] = "review is not actionable"
        return result

    marker = marker_for(repo, pr_number, review_id)
    comments = call_with_retry(
        caller, "GET",
        f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        gh, what="check idempotency marker", sleep=sleep,
    )
    if any(marker in (c.get("body") or "") for c in comments if isinstance(c, dict)):
        result["action"] = "skip"
        result["reason"] = "already processed (idempotency marker present)"
        return result

    if context["dry_run"]:
        result["action"] = "dry_run"
        return result

    asana_token = env.get("ASANA_ACCESS_TOKEN") or ""
    if not asana_token:
        _write_recovery(recovery_path, context, "missing ASANA_ACCESS_TOKEN secret")
        raise ProcessingError(
            "ASANA_ACCESS_TOKEN secret is not configured; event preserved for recovery"
        )
    workspace_gid = env.get("ASANA_WORKSPACE_GID") or ""
    if not workspace_gid:
        _write_recovery(recovery_path, context, "missing ASANA_WORKSPACE_GID")
        raise ProcessingError("ASANA_WORKSPACE_GID is not configured")
    ah = asana_headers(asana_token)

    try:
        workspace = call_with_retry(
            caller, "GET", f"{ASANA_API}/workspaces/{workspace_gid}", ah,
            what="verify Asana workspace", sleep=sleep,
        )
        if (workspace.get("data") or {}).get("gid") != workspace_gid:
            raise ProcessingError("Asana workspace verification returned unexpected data")

        name, notes = build_task_content(context, pr, inline_comments)
        task_data: dict[str, Any] = {"name": name, "notes": notes, "workspace": workspace_gid}
        project_gid = env.get("ASANA_PROJECT_GID") or ""
        if project_gid:
            project = call_with_retry(
                caller, "GET", f"{ASANA_API}/projects/{project_gid}", ah,
                what="verify Asana project", sleep=sleep,
            )
            if (project.get("data") or {}).get("gid") == project_gid:
                task_data["projects"] = [project_gid]

        created = call_with_retry(
            caller, "POST", f"{ASANA_API}/tasks", ah, {"data": task_data},
            expected=(201,), what="create Asana task", sleep=sleep,
        )
        task = created.get("data") or {}
        if not task.get("gid"):
            raise ProcessingError("Asana task creation returned no GID")
    except (ProcessingError, RetryableError) as exc:
        _write_recovery(recovery_path, context, str(exc))
        raise

    result["asana_task_gid"] = task["gid"]
    result["asana_task_url"] = task.get("permalink_url")

    comment_body = (
        f"Asana follow-up created: {result['asana_task_url'] or task['gid']}\n\n"
        f"Review state: {context['review_state']} by {context['reviewer']}\n"
        f"<!-- {marker} -->"
    )
    try:
        call_with_retry(
            caller, "POST",
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            gh, {"body": comment_body}, expected=(201,),
            what="record Asana reference in GitHub", sleep=sleep,
        )
    except (ProcessingError, RetryableError) as exc:
        # The task exists but the marker comment failed. Record recovery data
        # so a replay does not lose the created task reference.
        _write_recovery(
            recovery_path, context,
            f"task created ({task['gid']}) but GitHub comment failed: {exc}",
        )
        raise ProcessingError(
            f"Asana task {task['gid']} created but GitHub comment failed; "
            "recovery record written"
        ) from exc
    return result


def _write_recovery(path: str, context: dict[str, Any], reason: str) -> None:
    record = {"reason": reason, "context": context, "needs_replay": True}
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
    except OSError:
        print(f"::error::could not write recovery file: {reason}", file=sys.stderr)


def main() -> int:
    try:
        result = run(dict(os.environ))
    except ProcessingError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    except RetryableError as exc:
        print(f"::error::retryable failure exhausted retries: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
