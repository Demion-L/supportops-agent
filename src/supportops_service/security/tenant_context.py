"""Validated factory for ``TenantContext``.

The type itself is defined once, in ``supportops_service.interfaces``. This module only builds
instances from already-verified credential data and fails closed on anything malformed.
It is called from the authentication layer and nowhere else (enforced by a static test).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from supportops_service.interfaces import TenantContext
from supportops_service.security.contracts import Scope

_TENANT_ID: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_CREDENTIAL_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_KNOWN_SCOPES: Final = frozenset(s.value for s in Scope)


class TenantContextError(ValueError):
    """Raised when a context cannot be built safely. Message is fixed text (no data echoed)."""


def filter_scopes(raw_scopes: Iterable[object]) -> frozenset[Scope]:
    """Keep only members of the closed ``Scope`` set; everything else is silently dropped."""
    return frozenset(
        Scope(s) for s in raw_scopes if isinstance(s, str) and s in _KNOWN_SCOPES
    )


def build_tenant_context(
    *, tenant_id: object, credential_id: object, raw_scopes: Iterable[object]
) -> TenantContext:
    if not isinstance(tenant_id, str) or not _TENANT_ID.fullmatch(tenant_id):
        raise TenantContextError("invalid tenant")
    if not isinstance(credential_id, str) or not _CREDENTIAL_ID.fullmatch(credential_id):
        raise TenantContextError("invalid credential id")
    try:
        scopes = filter_scopes(raw_scopes)
    except TypeError:
        raise TenantContextError("invalid scopes") from None
    return TenantContext(tenant_id=tenant_id, credential_id=credential_id, scopes=scopes)
