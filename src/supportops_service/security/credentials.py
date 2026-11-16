"""API credential format, HMAC verifier and the authentication algorithm (contract §3).

Key format: ``sok1.<prefix>.<secret>``; transport: ``Authorization: Bearer <key>`` only.
Verifier at rest: ``HMAC-SHA256(pepper[pepper_id], "sok1.<prefix>.<secret>")``.

All collaborators (repository, peppers, clock) are injected. Nothing here reads the
environment, touches the network or logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from supportops_service.interfaces import TenantContext
from supportops_service.security.contracts import Clock, CredentialRepository
from supportops_service.security.errors import (
    AuthenticationUnavailable,
    DenyReason,
    Unauthenticated,
)
from supportops_service.security.tenant_context import TenantContextError, build_tenant_context

KEY_VERSION: Final = "sok1"
MAX_HEADER_BYTES: Final = 128
MIN_PEPPER_BYTES: Final = 32
_PEPPER_ID: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_KEY: Final = re.compile(r"sok1\.([a-z2-7]{12})\.([A-Za-z0-9_-]{43})")
_HASH_LEN: Final = hashlib.sha256().digest_size


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class PepperSet:
    """Server-side secrets keyed by ``pepper_id``. Values never appear in ``repr``."""

    __slots__ = ("_peppers",)

    def __init__(self, peppers: Mapping[str, bytes]) -> None:
        if not peppers:
            raise ValueError("at least one pepper is required")
        checked: dict[str, bytes] = {}
        for pepper_id, value in peppers.items():
            if not isinstance(pepper_id, str) or not _PEPPER_ID.fullmatch(pepper_id):
                raise ValueError("invalid pepper id")
            if not isinstance(value, bytes) or len(value) < MIN_PEPPER_BYTES:
                raise ValueError(f"pepper must be at least {MIN_PEPPER_BYTES} bytes")
            checked[pepper_id] = value
        self._peppers = checked

    def get(self, pepper_id: object) -> bytes | None:
        return self._peppers.get(pepper_id) if isinstance(pepper_id, str) else None

    def __repr__(self) -> str:
        return f"PepperSet(ids={sorted(self._peppers)})"

    def __reduce__(self) -> object:  # pragma: no cover - defensive: never serialize secrets
        raise TypeError("PepperSet is not serializable")


@dataclass(frozen=True, slots=True)
class ParsedKey:
    prefix: str
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """A freshly generated key. ``render()`` is the only way to get the full key; shown once."""

    prefix: str
    secret: str = field(repr=False)

    def render(self) -> str:
        return f"{KEY_VERSION}.{self.prefix}.{self.secret}"


def generate_api_key() -> IssuedKey:
    """Prefix: 60 random bits (base32); secret: 256 random bits (base64url). CSPRNG only."""
    prefix = base64.b32encode(secrets.token_bytes(8))[:12].decode("ascii").lower()
    return IssuedKey(prefix=prefix, secret=secrets.token_urlsafe(32))


def compute_key_hash(pepper: bytes, prefix: str, secret: str) -> bytes:
    message = f"{KEY_VERSION}.{prefix}.{secret}".encode("ascii")
    return hmac.new(pepper, message, hashlib.sha256).digest()


def parse_authorization(values: Sequence[str]) -> ParsedKey:
    """Strict header parser. Every failure is the same ``Unauthenticated(BAD_FORMAT)``."""
    if len(values) != 1:  # absent, or duplicated header
        raise Unauthenticated(DenyReason.BAD_FORMAT)
    value = values[0]
    if len(value.encode("utf-8", "replace")) > MAX_HEADER_BYTES:
        raise Unauthenticated(DenyReason.BAD_FORMAT)
    scheme, sep, token = value.partition(" ")
    if sep != " " or scheme.lower() != "bearer":
        raise Unauthenticated(DenyReason.BAD_FORMAT)
    match = _KEY.fullmatch(token)
    if match is None:
        raise Unauthenticated(DenyReason.BAD_FORMAT)
    return ParsedKey(prefix=match.group(1), secret=match.group(2))


class CredentialVerifier:
    """Implements the normative algorithm of contract §3.4. Fail-closed on every error path."""

    def __init__(
        self,
        repository: CredentialRepository,
        peppers: PepperSet,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._peppers = peppers
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Used only to equalize work when no real record/pepper is available.
        self._dummy_pepper = secrets.token_bytes(MIN_PEPPER_BYTES)
        self._dummy_hash = secrets.token_bytes(_HASH_LEN)

    def authenticate(self, authorization_values: Sequence[str]) -> TenantContext:
        parsed = parse_authorization(authorization_values)  # 1

        record: object = None
        repository_failed = False
        try:  # 2
            record = self._repository.get_by_prefix(parsed.prefix)
        except Exception:
            repository_failed = True
        if repository_failed:
            # Raised outside the except block so the original exception (which may carry a
            # DSN or SQL) is not attached as __context__. Down is not "unauthenticated" and
            # never "allowed".
            raise AuthenticationUnavailable(DenyReason.REPOSITORY_UNAVAILABLE)

        if record is None:  # 3
            self._dummy_check(parsed)
            raise Unauthenticated(DenyReason.UNKNOWN_PREFIX)

        fields = _read_record(record, parsed.prefix)
        credential_id = fields.credential_id if fields else None
        tenant_id = fields.tenant_id if fields else None
        pepper = self._peppers.get(fields.pepper_id) if fields else None
        if fields is None or pepper is None:
            self._dummy_check(parsed)
            raise Unauthenticated(DenyReason.BAD_RECORD, credential_id=credential_id)

        computed = compute_key_hash(pepper, parsed.prefix, parsed.secret)  # 4
        if not hmac.compare_digest(computed, fields.key_hash):
            raise Unauthenticated(
                DenyReason.BAD_SECRET, credential_id=credential_id, tenant_id=tenant_id
            )

        if fields.revoked_at is not None:  # 5
            raise Unauthenticated(
                DenyReason.REVOKED, credential_id=credential_id, tenant_id=tenant_id
            )
        if fields.expires_at is not None:
            try:
                expired = self._clock.now() >= fields.expires_at
            except Exception:  # naive/odd clock or datetime: unusable, deny (not a 500)
                raise Unauthenticated(
                    DenyReason.BAD_RECORD, credential_id=credential_id
                ) from None
            if expired:
                raise Unauthenticated(
                    DenyReason.EXPIRED, credential_id=credential_id, tenant_id=tenant_id
                )
        try:
            context = build_tenant_context(
                tenant_id=fields.tenant_id,
                credential_id=fields.credential_id,
                raw_scopes=fields.scopes,
            )
        except TenantContextError:
            raise Unauthenticated(DenyReason.BAD_RECORD, credential_id=credential_id) from None

        try:
            self._repository.touch_last_used(fields.credential_id)
        except Exception:  # best-effort bookkeeping must not change the decision
            pass
        return context

    def _dummy_check(self, parsed: ParsedKey) -> None:
        computed = compute_key_hash(self._dummy_pepper, parsed.prefix, parsed.secret)
        hmac.compare_digest(computed, self._dummy_hash)


@dataclass(frozen=True, slots=True)
class _RecordFields:
    credential_id: str
    tenant_id: str
    key_hash: bytes = field(repr=False)
    pepper_id: str
    scopes: frozenset[str]
    expires_at: datetime | None
    revoked_at: datetime | None


def _read_record(record: object, expected_prefix: str) -> _RecordFields | None:
    """Defensive snapshot of a repository result; ``None`` means the record is unusable."""
    try:
        credential_id = record.credential_id  # type: ignore[attr-defined]
        tenant_id = record.tenant_id  # type: ignore[attr-defined]
        key_prefix = record.key_prefix  # type: ignore[attr-defined]
        key_hash = record.key_hash  # type: ignore[attr-defined]
        pepper_id = record.pepper_id  # type: ignore[attr-defined]
        scopes = record.scopes  # type: ignore[attr-defined]
        expires_at = record.expires_at  # type: ignore[attr-defined]
        revoked_at = record.revoked_at  # type: ignore[attr-defined]
    except AttributeError:
        return None
    if not (isinstance(credential_id, str) and isinstance(tenant_id, str)):
        return None
    if not isinstance(key_prefix, str) or key_prefix != expected_prefix:
        return None
    if not isinstance(key_hash, bytes) or len(key_hash) != _HASH_LEN:
        return None
    if not isinstance(pepper_id, str):
        return None
    if not isinstance(scopes, (set, frozenset, list, tuple)):
        return None
    if expires_at is not None and (
        not isinstance(expires_at, datetime) or expires_at.utcoffset() is None
    ):
        return None  # naive/garbled expiry: cannot be trusted, deny
    try:
        scope_set = frozenset(scopes)
    except TypeError:  # unhashable members
        return None
    return _RecordFields(
        credential_id=credential_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        pepper_id=pepper_id,
        scopes=scope_set,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
