"""PRELIMINARY interface sketch for the future Alpha integration (M02).

STATUS: provisional and NOT agreed with Alpha. Names, fields and semantics may
change. Nothing here implements authentication, credential lookup or scope
enforcement; there are deliberately no concrete implementations. Do not wire
any of this into the application in WP1. Source: docs/design/api-004-m01-wp1-api-skeleton.md §6.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TenantContext:
    """PRELIMINARY. Identity derived only from a verified credential."""

    tenant_id: str
    credential_id: str
    scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """PRELIMINARY. Stored credential metadata (never the raw key)."""

    credential_id: str
    tenant_id: str
    key_prefix: str
    key_hash: str
    scopes: frozenset[str]
    revoked_at: str | None = None


class CredentialRepository(Protocol):
    """PRELIMINARY. Persistence-backed lookup; PG implementation is Alpha's (M02)."""

    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None: ...


class Authenticate(Protocol):
    """PRELIMINARY shape of the FastAPI dependency ``authenticate``.

    Expected to resolve to a ``TenantContext`` and use one uniform 401 for all
    failure reasons. Semantics are Alpha's to define.
    """

    def __call__(self, *args: object, **kwargs: object) -> TenantContext: ...


# PRELIMINARY shape of ``require_scope(scope)``: returns a dependency yielding a
# ``TenantContext`` or raising 403 ``insufficient_scope``. Semantics are Alpha's.
RequireScope = Callable[[str], Authenticate]
