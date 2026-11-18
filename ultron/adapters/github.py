"""GitHub integration adapter (REST API v3)."""

from __future__ import annotations

from typing import Any

from .base import BaseAdapter


class GitHubAdapter(BaseAdapter):
    name = "github"
    base_url = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_configured()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def health_check(self) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/user", headers=self._headers(),
            action="health_check",
        )

    def check_permissions(self) -> dict[str, Any]:
        viewer = self.health_check()
        return {"integration": self.name, "login": viewer.get("login")}

    def get_pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/repos/{repo}/pulls/{number}",
            headers=self._headers(), action="get_pull_request",
        )

    def get_review(self, repo: str, number: int, review_id: int) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self.base_url}/repos/{repo}/pulls/{number}/reviews/{review_id}",
            headers=self._headers(), action="get_review",
        )

    def list_review_comments(
        self, repo: str, number: int, review_id: int
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"{self.base_url}/repos/{repo}/pulls/{number}/reviews/{review_id}/comments?per_page=100",
            headers=self._headers(), action="list_review_comments",
        )
        return payload if isinstance(payload, list) else payload.get("items", [])

    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"{self.base_url}/repos/{repo}/issues/{number}/comments?per_page=100",
            headers=self._headers(), action="list_issue_comments",
        )
        return payload if isinstance(payload, list) else []

    def create_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST", f"{self.base_url}/repos/{repo}/issues/{number}/comments",
            headers=self._headers(), json_body={"body": body},
            action="create_issue_comment", expected=(201,),
        )
