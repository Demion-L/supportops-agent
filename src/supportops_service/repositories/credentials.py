"""PostgreSQL ``CredentialRepository`` (owner: Alpha; table ``api_credentials``).

Schema source of truth: ``db/migrations/0001_initial.sql``. Nothing here invents columns.

Security properties (each covered by tests/security_pg and tests/service/security):

* Lookup is by ``key_prefix`` only, with a parametrized constant SQL string. A prefix that is
  not exactly 12 base32 characters never reaches the database.
* The repository returns *data*. Revocation/expiry/pepper/hash decisions are made by
  ``CredentialVerifier`` on every request; this module caches nothing and holds no per-request
  state (the only attribute is the injected, thread-safe pool).
* Any failure (unreachable server, pool timeout, ``statement_timeout``/``lock_timeout``, schema
  drift, unexpected row shape) becomes :class:`CredentialStoreError` with fixed text. The driver
  exception is neither chained nor logged beyond its class name, so no DSN/SQL/host details can
  reach an HTTP response or a log. ``CredentialVerifier`` maps it to 503 ``service_unavailable``.
* ``touch_last_used`` is bookkeeping only. The verifier calls it after every check has passed
  and ignores its failure; this module additionally throttles the write so the hot path does
  not write on every request.
* ``repr`` exposes nothing (no DSN, no pool internals, no key material).

The pool is duck-typed (``connection()`` context manager yielding a psycopg-style connection
with ``dict_row`` rows, i.e. Beta's ``DatabasePool``), so this module imports no driver.
"""

from __future__ import annotations

import logging
import re
from contextlib import AbstractContextManager
from typing import Any, Final, Protocol

from supportops_service.interfaces import CredentialRecord

logger = logging.getLogger("supportops_service.credentials_repository")

_PREFIX: Final = re.compile(r"[a-z2-7]{12}")

# Constant SQL only; every value is bound through ``%s``. Never build SQL from data.
_SELECT_BY_PREFIX: Final = (
    "SELECT credential_id::text AS credential_id, tenant_id, key_prefix, key_hash, "
    "pepper_id, scopes, expires_at, revoked_at "
    "FROM api_credentials WHERE key_prefix = %s"
)
_TOUCH_LAST_USED: Final = (
    "UPDATE api_credentials SET last_used_at = now() "
    "WHERE credential_id = %s::uuid "
    "AND (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s))"
)

DEFAULT_TOUCH_MIN_INTERVAL_SECONDS: Final = 60.0


class ConnectionSource(Protocol):
    """Structural type of Beta's ``DatabasePool`` (only what this module uses)."""

    def connection(self) -> AbstractContextManager[Any]: ...


class CredentialStoreError(RuntimeError):
    """The credential store failed. Fixed text; never carries driver details."""

    def __init__(self) -> None:
        super().__init__("Credential store is unavailable.")


class PostgresCredentialRepository:
    __slots__ = ("_pool", "_touch_interval")

    def __init__(
        self,
        pool: ConnectionSource,
        *,
        touch_min_interval_seconds: float = DEFAULT_TOUCH_MIN_INTERVAL_SECONDS,
    ) -> None:
        if touch_min_interval_seconds < 0:
            raise ValueError("touch_min_interval_seconds must be >= 0")
        self._pool = pool
        self._touch_interval = float(touch_min_interval_seconds)

    def __repr__(self) -> str:
        return "PostgresCredentialRepository()"

    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None:
        if not isinstance(key_prefix, str) or _PREFIX.fullmatch(key_prefix) is None:
            return None  # cannot match a stored prefix (DB CHECK); no query, no injection surface
        failed = False
        row: Any = None
        try:
            with self._pool.connection() as conn:
                row = conn.execute(_SELECT_BY_PREFIX, (key_prefix,)).fetchone()
            record = None if row is None else _to_record(row)
        except Exception as exc:
            _log_failure("lookup", exc)
            failed = True
        if failed:
            # Raised outside ``except`` so the driver exception is not attached as __context__.
            raise CredentialStoreError()
        return record

    def touch_last_used(self, credential_id: str) -> None:
        failed = False
        try:
            with self._pool.connection() as conn:
                conn.execute(_TOUCH_LAST_USED, (credential_id, self._touch_interval))
        except Exception as exc:
            _log_failure("touch", exc)
            failed = True
        if failed:
            raise CredentialStoreError()


def _log_failure(operation: str, exc: Exception) -> None:
    # Class name only: messages from drivers can contain DSNs, hosts and SQL fragments.
    logger.warning("credential_store_error op=%s type=%s", operation, type(exc).__name__)


def _to_record(row: Any) -> CredentialRecord:
    key_hash = row["key_hash"]
    if isinstance(key_hash, (memoryview, bytearray)):
        key_hash = bytes(key_hash)
    scopes = row["scopes"]
    if not isinstance(scopes, (list, tuple, set, frozenset)):
        raise TypeError("scopes")
    return CredentialRecord(
        credential_id=row["credential_id"],
        tenant_id=row["tenant_id"],
        key_prefix=row["key_prefix"],
        key_hash=key_hash,
        pepper_id=row["pepper_id"],
        scopes=frozenset(scopes),
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )
