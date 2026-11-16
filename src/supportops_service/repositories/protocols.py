"""Repository contracts shared by the PostgreSQL and in-memory implementations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CreateResult:
    ticket_id: str
    run_id: str
    status: str  # current run status ('accepted' for a fresh create)
    created_at: datetime
    replayed: bool


@dataclass(frozen=True)
class TicketView:
    """API-facing projection. Deliberately has no message/customer_id/order_id."""

    ticket_id: str
    run_id: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    answer: str | None
    failure_code: str | None


class TicketRepository(Protocol):
    """API-facing ticket persistence.

    ``tenant_id`` is keyword-only with no default on every method and must come from
    the authenticated credential, never from a request body, LLM output, global state
    or ticket text.

    Errors (all with fixed text, no values):
      * ``InvalidRepositoryInput`` -- argument violates the contract (programming error);
      * ``IdempotencyConflict``    -- key reused with a different request;
      * ``StorageUnavailable``     -- database unreachable / timed out / lock timeout;
      * ``StorageFailure``         -- any other database failure (nothing was created).
    """

    def create_ticket(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        customer_id: str,
        message: str,
        order_id: str | None,
    ) -> CreateResult: ...

    def get_ticket(self, *, tenant_id: str, ticket_id: str) -> TicketView | None:
        """``None`` for foreign, unknown and malformed ids alike."""
        ...
