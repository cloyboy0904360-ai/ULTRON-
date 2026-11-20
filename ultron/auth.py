"""API-key authentication and permission checks.

Keys are configured via ``ULTRON_API_KEYS`` (semicolon-separated). Each key
may carry scopes using the form ``rawkey:scope1,scope2``; a key without
a scope suffix has all scopes. Keys are only ever compared via a
constant-time digest and are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from .errors import UltronError


class AuthenticationError(UltronError):
    code = "UNAUTHENTICATED"


class PermissionDeniedError(UltronError):
    code = "PERMISSION_DENIED"


@dataclass(frozen=True)
class Principal:
    key_digest: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class Authenticator:
    def __init__(self, raw_keys: tuple[str, ...]):
        self._entries: dict[str, frozenset[str]] = {}
        for entry in raw_keys:
            raw, _, scope_part = entry.partition(":")
            if not raw.strip():
                continue
            scopes = (
                frozenset(s.strip() for s in scope_part.split(",") if s.strip())
                if scope_part
                else frozenset({"*"})
            )
            self._entries[_digest(raw.strip())] = scopes

    @property
    def enabled(self) -> bool:
        return bool(self._entries)

    def authenticate(self, presented_key: str | None) -> Principal:
        if not self._entries:
            # Auth disabled (development only). Refuse to look authenticated.
            raise AuthenticationError(
                "No API keys configured; authentication is unavailable"
            )
        if not presented_key:
            raise AuthenticationError("Missing API key")
        digest = _digest(presented_key.strip())
        for stored, scopes in self._entries.items():
            if hmac.compare_digest(stored, digest):
                return Principal(key_digest=digest, scopes=scopes)
        raise AuthenticationError("Invalid API key")

    def require(self, presented_key: str | None, scope: str) -> Principal:
        principal = self.authenticate(presented_key)
        if not principal.can(scope):
            raise PermissionDeniedError(f"Scope '{scope}' required")
        return principal
