"""Security errors and their HTTP rendering.

Beta's generic ``errors.py`` maps 401/403 to ``unauthorized``/``forbidden``, ignores
``HTTPException.headers`` (so ``WWW-Authenticate`` is impossible) and cannot carry the
``insufficient_scope`` code from the contract. Security errors therefore use their own exception
types and handlers, built on Beta's public ``error_body`` envelope helper.

Responses contain fixed text only. The internal ``reason`` goes to the server log only.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hermes_service.errors import error_body
from hermes_service.middleware import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger("hermes_service.security")

_LOG_SAFE_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class DenyReason(StrEnum):
    """Internal-only categories (contract §7.3). Never serialized into a response."""

    BAD_FORMAT = "bad_format"
    UNKNOWN_PREFIX = "unknown_prefix"
    BAD_SECRET = "bad_secret"
    REVOKED = "revoked"
    EXPIRED = "expired"
    BAD_RECORD = "bad_record"
    SCOPE = "scope"
    REPOSITORY_UNAVAILABLE = "repository_unavailable"
    NOT_CONFIGURED = "not_configured"
    OVERLOADED = "overloaded"


class SecurityError(Exception):
    status_code: int
    code: str
    message: str
    headers: dict[str, str]

    def __init__(
        self,
        reason: DenyReason,
        *,
        credential_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__(self.code)  # str(exc) is the fixed public code, never a reason/value
        self.reason = reason
        self.credential_id = credential_id
        self.tenant_id = tenant_id


class Unauthenticated(SecurityError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication required."
    headers = {"WWW-Authenticate": "Bearer"}


class InsufficientScope(SecurityError):
    status_code = 403
    code = "insufficient_scope"
    message = "Access denied."
    headers: dict[str, str] = {}


class AuthenticationUnavailable(SecurityError):
    status_code = 503
    code = "service_unavailable"
    message = "Service temporarily unavailable."
    headers: dict[str, str] = {}


def _log_id(value: str | None) -> str:
    return value if value and _LOG_SAFE_ID.fullmatch(value) else "-"


async def _security_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SecurityError)
    request_id = get_request_id(request.scope)
    # No key, prefix, header value, body or exception text is ever logged (contract §7.3).
    logger.warning(
        "auth_denied request_id=%s status=%d reason=%s credential_id=%s tenant_id=%s",
        request_id,
        exc.status_code,
        exc.reason.value,
        _log_id(exc.credential_id),
        _log_id(exc.tenant_id),
    )
    headers = {**exc.headers, REQUEST_ID_HEADER: request_id}
    if exc.reason is DenyReason.OVERLOADED:
        headers["Retry-After"] = "1"  # gate saturation only; other 503s carry no hint
    return JSONResponse(
        error_body(exc.code, exc.message, request_id), status_code=exc.status_code, headers=headers
    )


def install_security_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(SecurityError, _security_error_handler)
