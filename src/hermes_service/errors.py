"""Unified HTTP error contract.

Every error response has the shape::

    {"error": {"code": str, "message": str, "request_id": str}}

Codes and messages are fixed text. Responses never contain tracebacks,
``str(exc)``, internal paths, secrets, or any value taken from the request or
from an exception.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hermes_service.db.errors import (
    IdempotencyConflict,
    InvalidRepositoryInput,
    StorageFailure,
    StorageUnavailable,
)
from hermes_service.middleware import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger("hermes_service")

_FIXED: Final[dict[int, tuple[str, str]]] = {
    400: ("bad_request", "The request is invalid."),
    401: ("unauthenticated", "Authentication required."),
    403: ("insufficient_scope", "Access denied."),
    404: ("not_found", "Resource not found."),
    409: ("idempotency_key_reuse", "Idempotency key was already used with a different request."),
    405: ("method_not_allowed", "Method not allowed."),
    413: ("payload_too_large", "Request body too large."),
    422: ("validation_error", "Request validation failed."),
    429: ("rate_limited", "Too many requests."),
    500: ("internal_error", "Internal server error."),
    503: ("service_unavailable", "Service temporarily unavailable."),
}
# Request-shape rejections raised by route handlers. Codes are a closed set; text is fixed.
_REJECTIONS: Final[dict[int, tuple[str, str]]] = {
    400: ("idempotency_key_required", "A valid Idempotency-Key header is required."),
    413: _FIXED[413],
    415: ("unsupported_media_type", "Content-Type must be application/json."),
}


class RequestRejected(Exception):
    """A request rejected before the operation. Only the status is carried; the body is fixed."""

    def __init__(self, status: int) -> None:
        if status not in _REJECTIONS:
            raise ValueError("unsupported rejection status")
        super().__init__("Request rejected.")
        self.status = status


_GENERIC_4XX: Final = ("client_error", "The request could not be processed.")
_GENERIC_5XX: Final = _FIXED[500]

_SAFE_NAME: Final = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
# Error types whose ``loc`` may end in a client-supplied key.
_USER_KEY_TYPES: Final = frozenset({"extra_forbidden"})


def error_body(code: str, message: str, request_id: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def _response(
    status: int, code: str, message: str, request: Request, extra: dict[str, Any] | None = None
) -> JSONResponse:
    request_id = get_request_id(request.scope)
    body = error_body(code, message, request_id)
    if extra:
        body["error"].update(extra)
    # Set the header here too: ServerErrorMiddleware sits outside our middleware.
    return JSONResponse(body, status_code=status, headers={REQUEST_ID_HEADER: request_id})


def _safe_path(loc: tuple[Any, ...], error_type: str) -> list[str | int]:
    """Field path without reflecting client-supplied keys or values."""
    path: list[str | int] = []
    last = len(loc) - 1
    for index, item in enumerate(loc):
        if isinstance(item, int):
            path.append(item)
        elif index == last and error_type in _USER_KEY_TYPES:
            path.append("*")
        elif isinstance(item, str) and _SAFE_NAME.fullmatch(item):
            path.append(item)
        else:
            path.append("*")
    return path


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    code, message = _FIXED.get(status) or (_GENERIC_5XX if status >= 500 else _GENERIC_4XX)
    # exc.detail is deliberately ignored.
    return _response(status, code, message, request)


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = []
    for err in exc.errors():
        error_type = str(err.get("type", ""))
        fields.append(
            {
                "path": _safe_path(tuple(err.get("loc", ())), error_type),
                "type": error_type if _SAFE_NAME.fullmatch(error_type) else "invalid",
            }
        )
    code, message = _FIXED[422]
    return _response(422, code, message, request, {"fields": fields})


async def _rejection_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestRejected)
    code, message = _REJECTIONS[exc.status]
    return _response(exc.status, code, message, request)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # Server-side log carries only the exception class, never its message or traceback text.
    logger.error("unhandled_exception type=%s", type(exc).__name__)
    code, message = _FIXED[500]
    return _response(500, code, message, request)


async def _storage_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("storage_unavailable type=%s", type(exc).__name__)
    code, message = _FIXED[503]
    return _response(503, code, message, request)  # no Retry-After: not a load-shedding signal


async def _idempotency_conflict_handler(request: Request, exc: Exception) -> JSONResponse:
    code, message = _FIXED[409]  # carries nothing from the first request
    return _response(409, code, message, request)


async def _storage_failure_handler(request: Request, exc: Exception) -> JSONResponse:
    # StorageFailure (non-availability DB failure) and InvalidRepositoryInput (programming
    # error) are server-side faults: fixed 500, class name only in the log.
    logger.error("repository_failure type=%s", type(exc).__name__)
    code, message = _FIXED[500]
    return _response(500, code, message, request)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestRejected, _rejection_handler)
    app.add_exception_handler(StorageUnavailable, _storage_unavailable_handler)
    app.add_exception_handler(IdempotencyConflict, _idempotency_conflict_handler)
    app.add_exception_handler(StorageFailure, _storage_failure_handler)
    app.add_exception_handler(InvalidRepositoryInput, _storage_failure_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
