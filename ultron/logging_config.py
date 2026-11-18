"""Safe, structured logging for ULTRON.

- JSON lines to stderr so logs can be collected by any runner.
- Secret redaction: any value that looks like a credential is masked
  before a record is emitted. Tokens must never appear in logs.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_REDACTED = "***REDACTED***"

# Key fragments whose values are always redacted.
_SENSITIVE_KEYS = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|auth|credential|private[_-]?key)",
    re.IGNORECASE,
)

# Value patterns that look like bearer tokens / PATs / API keys even when
# the key name is innocuous.
_SENSITIVE_VALUES = re.compile(
    r"(Bearer\s+\S+|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk_live_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}|[0-9]+/[0-9]+:[A-Za-z0-9]{20,})"
)


def redact(value: Any) -> Any:
    """Recursively redact sensitive data from arbitrary structures."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _SENSITIVE_KEYS.search(str(key)):
                out[key] = _REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUES.sub(_REDACTED, value)
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = redact(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger exactly once."""
    root = logging.getLogger()
    if getattr(root, "_ultron_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root._ultron_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
