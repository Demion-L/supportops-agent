"""PROPOSED shared types awaiting Beta's confirmation (see docs/security/api-004-interface-proposal.md).

These definitions are the Alpha side of the agreed-upon interface. They are meant to move
VERBATIM into ``supportops_service/interfaces.py`` (owner: Beta) once Beta confirms, after which
this module becomes a re-export shim and is then deleted. Until then ``interfaces.py`` is not
modified.

``TenantContext`` is deliberately NOT defined here: the single definition lives in
``supportops_service.interfaces``.
"""

from __future__ import annotations

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
class CredentialRecord:
    """Stored credential metadata. Never contains the raw key.

    ``scopes`` holds raw strings on purpose: filtering to the closed ``Scope`` set happens in
    the security layer, so a corrupt/unknown value cannot crash a repository.
    """

    credential_id: str
    tenant_id: str
    key_prefix: str
    key_hash: bytes = field(repr=False)
    pepper_id: str
    scopes: frozenset[str]
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class CredentialRepository(Protocol):
    """Credential lookup. Used only by the authentication layer (never by the worker)."""

    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None: ...

    def touch_last_used(self, credential_id: str) -> None:
        """Best-effort bookkeeping; must never influence an authentication decision."""
        ...


class Clock(Protocol):
    """Injectable time source (timezone-aware UTC) so expiry is testable."""

    def now(self) -> datetime: ...
