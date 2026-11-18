"""Base class for ULTRON integration adapters.

Every adapter provides:
- connection validation and auth/permission checks
- API health checks
- structured errors (IntegrationError / IntegrationUnavailableError)
- retry with exponential backoff on 429 and 5xx (honouring Retry-After)
- per-request timeouts
- secret-safe logging (tokens are never logged)
- last successful / failed operation records
- evidence of execution persisted to the integration_evidence table

An adapter whose credentials are absent is BLOCKED — it reports that
honestly instead of pretending to work.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from ..db import Database
from ..errors import IntegrationError, IntegrationUnavailableError
from ..logging_config import get_logger


class AdapterStatus(str, Enum):
    CONNECTED = "CONNECTED"
    BLOCKED = "BLOCKED"
    DEGRADED = "DEGRADED"
    AWAITING_APPROVAL = "AWAITING APPROVAL"


# Transport signature: (method, url, headers, body, timeout) -> (status, dict)
Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, dict[str, Any]]]


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode() or "{}"
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        payload["_retry_after"] = exc.headers.get("Retry-After") if exc.headers else None
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise IntegrationError(
            f"Network error: {exc.reason}", integration="unknown", retryable=True
        ) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseAdapter:
    name = "base"
    base_url = ""

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        db: Database | None = None,
        transport: Transport | None = None,
    ):
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.db = db
        self._transport = transport or _urllib_transport
        self.log = get_logger(f"ultron.adapters.{self.name}")
        self.last_successful_operation: dict[str, Any] | None = None
        self.last_failed_operation: dict[str, Any] | None = None

    # ------------------------------------------------------------ status
    @property
    def configured(self) -> bool:
        return bool(self._token)

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "integration": self.name,
                "status": AdapterStatus.BLOCKED.value,
                "reason": "credentials not configured",
            }
        try:
            self.health_check()
        except IntegrationError as exc:
            return {
                "integration": self.name,
                "status": AdapterStatus.DEGRADED.value,
                "reason": exc.message,
                "retryable": exc.retryable,
            }
        return {"integration": self.name, "status": AdapterStatus.CONNECTED.value}

    # ------------------------------------------------------------- checks
    def health_check(self) -> dict[str, Any]:
        """Override with a cheap authenticated read. Must raise on failure."""
        raise NotImplementedError

    def check_permissions(self) -> dict[str, Any]:
        """Override with a permission probe. Default: unknown, not assumed."""
        return {"integration": self.name, "permissions": "unknown"}

    # ------------------------------------------------------------ requests
    def _require_configured(self) -> str:
        if not self._token:
            raise IntegrationUnavailableError(
                f"{self.name} credentials are not configured; integration is BLOCKED",
                integration=self.name,
            )
        return self._token

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        action: str,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        """Execute an HTTP request with retries, timeout and evidence."""
        self._require_configured()
        body = raw_body
        if json_body is not None:
            body = json.dumps(json_body).encode()
        hdrs = dict(headers or {})
        if body is not None:
            hdrs.setdefault("Content-Type", "application/json")

        attempt = 0
        while True:
            attempt += 1
            try:
                status, payload = self._transport(
                    method, url, hdrs, body, self.timeout_seconds
                )
            except IntegrationError as exc:
                exc.integration = self.name
                if attempt > self.max_retries or not exc.retryable:
                    self._record_failure(action, exc.message)
                    raise
                self._sleep_before_retry(attempt, None)
                continue

            if status in expected:
                self._record_success(action, {"status": status})
                return payload

            retryable = status == 429 or 500 <= status < 600
            message = self._error_message(status, payload)
            if retryable and attempt <= self.max_retries:
                retry_after = payload.get("_retry_after")
                self.log.warning(
                    "retrying after retryable response",
                    extra={"context": {"action": action, "status": status,
                                       "attempt": attempt}},
                )
                self._sleep_before_retry(attempt, retry_after)
                continue
            error = IntegrationError(
                message, integration=self.name, status_code=status,
                retryable=retryable,
            )
            self._record_failure(action, message, status=status)
            raise error

    def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        delay = min(2 ** (attempt - 1), 30)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 60))
            except ValueError:
                pass
        time.sleep(delay)

    @staticmethod
    def _error_message(status: int, payload: dict[str, Any]) -> str:
        detail = payload.get("message") or payload.get("error") or payload.get("raw") \
            or payload.get("errors") or "no detail returned"
        return f"HTTP {status}: {detail}"

    # ------------------------------------------------------------ evidence
    def _record_success(self, action: str, detail: dict[str, Any]) -> None:
        self.last_successful_operation = {"action": action, "at": _now(), **detail}
        self._evidence(action, "success", detail)

    def _record_failure(
        self, action: str, message: str, *, status: int | None = None
    ) -> None:
        self.last_failed_operation = {
            "action": action, "at": _now(), "error": message, "status": status,
        }
        self._evidence(action, "failure", {"error": message, "status": status})

    def _evidence(self, action: str, outcome: str, detail: dict[str, Any]) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT INTO integration_evidence (integration, action, outcome, detail, at) "
            "VALUES (?,?,?,?,?)",
            (self.name, action, outcome, json.dumps(detail, default=str), _now()),
        )
