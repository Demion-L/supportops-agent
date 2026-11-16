"""Shared service interfaces (agreed Alpha/Beta contract, API-004).

Implementations live elsewhere: authentication in ``security/`` and the PostgreSQL credential
store in ``repositories/credentials.py`` (owner: Alpha). This module holds only the types both
sides depend on. ``TenantContext`` has exactly one definition (here) and is constructed only by
``security.tenant_context.build_tenant_context`` after successful authentication.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Scope(StrEnum):
    """Closed set of API scopes. Unknown strings are never granted."""

    TICKETS_WRITE = "tickets:write"
    TICKETS_READ = "tickets:read"
    TRACE_READ = "trace:read"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Identity derived only from a verified credential. Immutable."""

    tenant_id: str
    credential_id: str
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """Stored credential metadata. Never contains the raw key.

    ``scopes`` holds raw strings on purpose: filtering to the closed ``Scope`` set happens in
    the security layer, so a corrupt/unknown value cannot crash a repository.
    """

    credential_id: str
    tenant_id: str
    key_prefix: str
    key_hash: bytes = field(repr=False)  # HMAC-SHA256, 32 bytes
    pepper_id: str
    scopes: frozenset[str]
    expires_at: datetime | None = None  # timezone-aware
    revoked_at: datetime | None = None  # timezone-aware


class CredentialRepository(Protocol):
    """Credential lookup. Used only by the authentication layer (never by the worker)."""

    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None: ...

    def touch_last_used(self, credential_id: str) -> None:
        """Best-effort bookkeeping; must never influence an authentication decision."""
        ...


class Clock(Protocol):
    """Injectable time source (timezone-aware UTC) so expiry is testable."""

    def now(self) -> datetime: ...


class Authenticate(Protocol):
    """Shape of the FastAPI dependency ``authenticate`` (implemented in ``security/``)."""

    def __call__(self, *args: object, **kwargs: object) -> TenantContext: ...


# ``require_scope(scope)`` returns a dependency yielding a ``TenantContext`` or raising 403
# ``insufficient_scope``.
RequireScope = Callable[[Scope], Authenticate]
