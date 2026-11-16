"""Input validation and the canonical request hash, shared by every implementation
so the PostgreSQL repository and the fake cannot drift apart."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Final

from supportops_service.db.errors import InvalidRepositoryInput

TENANT_ID_RE: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")  # == CHECK in migration 0001
IDEMPOTENCY_KEY_RE: Final = re.compile(r"[A-Za-z0-9._:-]{8,100}")
MAX_CUSTOMER_ID: Final = 100
MAX_MESSAGE: Final = 4000
MAX_ORDER_ID: Final = 100


def validate_tenant_id(value: object) -> str:
    if not isinstance(value, str) or TENANT_ID_RE.fullmatch(value) is None:
        raise InvalidRepositoryInput("tenant_id")
    return value


def validate_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        raise InvalidRepositoryInput("idempotency_key")
    return value


def _validate_text(value: object, field: str, max_len: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= max_len:
        raise InvalidRepositoryInput(field)
    if "\x00" in value:  # PostgreSQL text cannot hold NUL
        raise InvalidRepositoryInput(field)
    try:
        value.encode("utf-8")  # rejects lone surrogates before they reach the driver
    except UnicodeEncodeError:
        raise InvalidRepositoryInput(field) from None
    return value


def validate_create_args(
    *,
    tenant_id: object,
    idempotency_key: object,
    customer_id: object,
    message: object,
    order_id: object,
) -> tuple[str, str, str, str, str | None]:
    tenant = validate_tenant_id(tenant_id)
    key = validate_idempotency_key(idempotency_key)
    customer = _validate_text(customer_id, "customer_id", MAX_CUSTOMER_ID)
    text = _validate_text(message, "message", MAX_MESSAGE)
    order = None if order_id is None else _validate_text(order_id, "order_id", MAX_ORDER_ID)
    return tenant, key, customer, text, order


def compute_request_hash(customer_id: str, message: str, order_id: str | None) -> bytes:
    """SHA-256 of the canonical JSON ``{customer_id, message, order_id}`` (sorted keys,
    compact separators, UTF-8, ``order_id`` null when absent)."""
    canonical = json.dumps(
        {"customer_id": customer_id, "message": message, "order_id": order_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()
