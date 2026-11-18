"""Asana integration adapter (API v1).

Rules:
- The workspace is verified before any task is created.
- Tasks are created at workspace level; a project is attached only when
  it has been verified to exist. Project identifiers are never invented.
- The access token is never logged or embedded in task content.
"""

from __future__ import annotations

from typing import Any

from ..errors import IntegrationError, ValidationError
from .base import BaseAdapter


class AsanaAdapter(BaseAdapter):
    name = "asana"
    base_url = "https://app.asana.com/api/1.0"

    def __init__(self, *, workspace_gid: str | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.workspace_gid = workspace_gid

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._require_configured()}"}

    def health_check(self) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url}/users/me", headers=self._headers(),
            action="health_check",
        )

    def check_permissions(self) -> dict[str, Any]:
        me = self.health_check().get("data", {})
        return {
            "integration": self.name,
            "user": me.get("name"),
            "workspaces": [w.get("gid") for w in me.get("workspaces", [])],
        }

    def verify_workspace(self, workspace_gid: str | None = None) -> dict[str, Any]:
        """Verify the connected account can actually see the workspace."""
        gid = workspace_gid or self.workspace_gid
        if not gid:
            raise ValidationError("No Asana workspace GID configured")
        payload = self._request(
            "GET", f"{self.base_url}/workspaces/{gid}",
            headers=self._headers(), action="verify_workspace",
        )
        data = payload.get("data")
        if not data or data.get("gid") != gid:
            raise IntegrationError(
                "Asana workspace verification failed: unexpected response",
                integration=self.name,
            )
        return data

    def create_task(
        self,
        *,
        name: str,
        notes: str,
        workspace_gid: str | None = None,
        project_gid: str | None = None,
    ) -> dict[str, Any]:
        gid = workspace_gid or self.workspace_gid
        if not gid:
            raise ValidationError("No Asana workspace GID configured")
        self.verify_workspace(gid)
        data: dict[str, Any] = {"name": name, "notes": notes, "workspace": gid}
        if project_gid:
            # Only attached after an explicit, successful verification.
            self._request(
                "GET", f"{self.base_url}/projects/{project_gid}",
                headers=self._headers(), action="verify_project",
            )
            data["projects"] = [project_gid]
        payload = self._request(
            "POST", f"{self.base_url}/tasks", headers=self._headers(),
            json_body={"data": data}, action="create_task", expected=(201,),
        )
        task = payload.get("data") or {}
        if not task.get("gid"):
            raise IntegrationError(
                "Asana create_task returned no task GID; treating as failure",
                integration=self.name,
            )
        return task
