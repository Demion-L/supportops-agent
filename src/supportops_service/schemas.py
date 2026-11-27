"""HTTP response schemas for ``/v1/tickets`` (owner: Alpha track).

``TicketResponse`` is the *only* shape a ticket leaves the API in. It has no field for
``customer_id``, ``message``, ``order_id``, ``tenant_id`` or anything credential-related, so those
values cannot be serialised by accident. ``extra="forbid"`` keeps the model closed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from supportops_service.repositories.protocols import CreateResult, TicketView

RunStatus = Literal["accepted", "running", "completed", "failed"]


class CreateTicketRequest(BaseModel):
    """Body of ``POST /v1/tickets``: exactly ``{customer_id, message, order_id?}``.

    ``extra="forbid"`` rejects ``tenant_id``, ``ticket_id``, ``run_id``, ``status`` and anything
    else with 422 (an integration mistake must be visible, not silently ignored). Types are strict
    (no ``123`` -> ``"123"`` coercion) and surrounding whitespace is stripped *before* the
    idempotency hash is computed, so the hash is taken over the normalised values.

    Customer identity model (security contract §2a): ``customer_id`` is a **tenant-asserted
    business identifier**. The authenticated tenant backend says "this ticket concerns customer X
    of mine". It is stored scoped to the tenant, handed to the agent as the tenant's own claim, and
    is **never** proof that the end user is X and never the sole basis for authorising a sensitive
    operation.
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)

    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    order_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("customer_id", "message", "order_id")
    @classmethod
    def _storable_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:  # PostgreSQL text cannot hold NUL
            raise ValueError("nul")
        try:
            value.encode("utf-8")  # lone surrogates cannot reach the driver
        except UnicodeEncodeError:
            raise ValueError("encoding") from None
        return value


class CreateTicketResponse(BaseModel):
    """What a create call returns. No customer/message/order/tenant fields exist to leak."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    run_id: str
    status: RunStatus
    created_at: datetime

    @classmethod
    def from_result(cls, result: CreateResult) -> CreateTicketResponse:
        return cls(
            ticket_id=result.ticket_id,
            run_id=result.run_id,
            status=result.status,  # type: ignore[arg-type]  # validated against the closed Literal
            created_at=result.created_at,
        )


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z_]{1,64}$")  # closed-set code, same shape as the DB CHECK


class TicketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    run_id: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    answer: str | None = None
    failure: Failure | None = None

    @classmethod
    def from_view(cls, view: TicketView) -> TicketResponse:
        return cls(
            ticket_id=view.ticket_id,
            run_id=view.run_id,
            status=view.status,  # type: ignore[arg-type]  # validated against the closed Literal
            created_at=view.created_at,
            started_at=view.started_at,
            finished_at=view.finished_at,
            answer=view.answer if view.status == "completed" else None,
            failure=Failure(code=view.failure_code)
            if view.status == "failed" and view.failure_code is not None
            else None,
        )
