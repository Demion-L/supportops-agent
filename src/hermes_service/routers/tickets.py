"""``/v1/tickets`` HTTP routes (owner: Alpha track).

``POST /v1/tickets`` creates a ticket + run (idempotent per tenant); ``GET /v1/tickets/{id}``
reads one. Neither executes the agent: a created run stays ``accepted`` until a worker (a later
phase) picks it up. No LLM, network or ``supportops_agent`` code is reachable from here.

Security shape (covered by tests/service/test_tickets_http.py and
tests/integration/test_tickets_http_pg.py):

* ``SecuredRoute`` authenticates and checks the scope before the body is read (401 -> 403 first);
* the tenant comes only from ``TenantContext`` (the verified credential); a ``tenant_id`` in the
  body is a 422, never a value;
* ``customer_id`` is a tenant-asserted business identifier (see ``CreateTicketRequest``): it is
  stored scoped to the tenant and is not an authorisation input anywhere in this module;
* check order for POST: 401 -> 403 -> Idempotency-Key 400 -> size 413 -> Content-Type 415 -> 422;
* a foreign, unknown or malformed ticket id gives one and the same 404;
* the repository is injected; this module imports no driver, no SQL and nothing from
  ``supportops_agent``.
"""

from __future__ import annotations

import re
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from hermes_service.errors import RequestRejected
from hermes_service.interfaces import Scope, TenantContext
from hermes_service.repositories.protocols import TicketRepository
from hermes_service.schemas import CreateTicketRequest, CreateTicketResponse, TicketResponse
from hermes_service.security.dependencies import SecuredRoute, require_scope

MAX_BODY_BYTES: Final = 16 * 1024
_IDEMPOTENCY_KEY: Final = re.compile(r"[A-Za-z0-9._:-]{8,100}")
_JSON_MEDIA_TYPE: Final = re.compile(r"application/json(?:\s*;\s*charset\s*=\s*\"?utf-8\"?)?", re.IGNORECASE)
_BODY_SCHEMA: Final = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": CreateTicketRequest.model_json_schema()}},
    }
}


def _idempotency_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if len(values) != 1 or _IDEMPOTENCY_KEY.fullmatch(values[0]) is None:
        raise RequestRejected(400)
    return values[0]


async def _read_limited_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise RequestRejected(413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():  # also bounds chunked bodies without a Content-Length
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise RequestRejected(413)
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_body(raw: bytes) -> CreateTicketRequest:
    try:
        return CreateTicketRequest.model_validate_json(raw)
    except ValidationError as exc:
        # Re-raised as the app's 422 (path + type only, never input). ``loc`` gets the "body" prefix.
        errors = [
            {"type": e["type"], "loc": ("body", *e["loc"]), "msg": "", "input": None}
            for e in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise RequestValidationError(errors) from None


def create_tickets_router(tickets: TicketRepository) -> APIRouter:
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.post(
        "/tickets",
        status_code=202,
        response_model=CreateTicketResponse,
        openapi_extra=_BODY_SCHEMA,
    )
    async def create_ticket(
        request: Request,
        response: Response,
        ctx: TenantContext = Depends(require_scope(Scope.TICKETS_WRITE)),
    ) -> CreateTicketResponse:
        # The body is read by hand (not as a FastAPI body parameter) so that the documented
        # order 400 -> 413 -> 415 -> 422 holds; authentication already happened in SecuredRoute.
        key = _idempotency_key(request)
        raw = await _read_limited_body(request)
        if _JSON_MEDIA_TYPE.fullmatch(request.headers.get("content-type", "").strip()) is None:
            raise RequestRejected(415)
        body = _parse_body(raw)
        result = await run_in_threadpool(
            tickets.create_ticket,
            tenant_id=ctx.tenant_id,  # the only source of the tenant
            idempotency_key=key,
            customer_id=body.customer_id,
            message=body.message,
            order_id=body.order_id,
        )
        response.headers["Location"] = f"/v1/tickets/{result.ticket_id}"
        if result.replayed:
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
        return CreateTicketResponse.from_result(result)

    @router.get(
        "/tickets/{ticket_id}",
        response_model=TicketResponse,
        response_model_exclude_none=True,
    )
    def get_ticket(
        ticket_id: str,
        response: Response,
        ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ)),
    ) -> TicketResponse:
        view = tickets.get_ticket(tenant_id=ctx.tenant_id, ticket_id=ticket_id)
        if view is None:
            raise HTTPException(status_code=404)  # fixed `not_found` body; detail is ignored
        response.headers["Cache-Control"] = "no-store"
        return TicketResponse.from_view(view)

    return router
