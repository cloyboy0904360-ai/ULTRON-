"""Structured error types used across ULTRON.

Every error carries a machine-readable ``code``, a human-readable
``message`` and an optional ``details`` mapping so API responses, logs
and operation records can share one shape.
"""

from __future__ import annotations

from typing import Any


class UltronError(Exception):
    """Base class for all ULTRON errors."""

    code = "ULTRON_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigError(UltronError):
    code = "CONFIG_ERROR"


class ValidationError(UltronError):
    code = "VALIDATION_ERROR"


class NotFoundError(UltronError):
    code = "NOT_FOUND"


class DuplicateError(UltronError):
    code = "DUPLICATE"


class StateTransitionError(UltronError):
    code = "INVALID_STATE_TRANSITION"


class ApprovalRequiredError(UltronError):
    code = "APPROVAL_REQUIRED"


class EvidenceRequiredError(UltronError):
    code = "EVIDENCE_REQUIRED"


class IntegrationError(UltronError):
    code = "INTEGRATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        status_code: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details=details)
        self.integration = integration
        self.status_code = status_code
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["details"].update(
            {
                "integration": self.integration,
                "status_code": self.status_code,
                "retryable": self.retryable,
            }
        )
        return data


class IntegrationUnavailableError(IntegrationError):
    code = "INTEGRATION_UNAVAILABLE"

    def __init__(self, message: str, *, integration: str, **kwargs: Any):
        super().__init__(message, integration=integration, retryable=True, **kwargs)
