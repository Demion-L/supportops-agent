"""In-memory TicketRepository with the same public behaviour as the PostgreSQL one.

Test double only. Validation and the request hash come from the same module as the
PostgreSQL implementation; the same contract suite runs against both. The internal
lock stands in for the database's transaction + unique-index serialisation.
"""

from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from supportops_service.db.errors import IdempotencyConflict, StorageFailure, StorageUnavailable
from supportops_service.ids import is_ticket_id, new_run_id, new_ticket_id
from supportops_service.repositories import _inputs
from supportops_service.repositories.protocols import CreateResult, TicketView


@dataclass
class _Record:
    ticket: TicketView
    request_hash: bytes


class InMemoryTicketRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[tuple[str, str], _Record] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        # Test-only fault switches (see tests/db/ticket_harness.py).
        self._unavailable = False
        self._fail_between_inserts = False

    def create_ticket(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        customer_id: str,
        message: str,
        order_id: str | None,
    ) -> CreateResult:
        tenant, key, customer, text, order = _inputs.validate_create_args(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            customer_id=customer_id,
            message=message,
            order_id=order_id,
        )
        request_hash = _inputs.compute_request_hash(customer, text, order)
        with self._lock:
            if self._unavailable:
                raise StorageUnavailable()
            known = self._by_key.get((tenant, key))
            if known is not None:
                record = self._by_id[(tenant, known)]
                if not hmac.compare_digest(record.request_hash, request_hash):
                    raise IdempotencyConflict()
                t = record.ticket
                return CreateResult(t.ticket_id, t.run_id, t.status, t.created_at, True)
            # Stage both rows; publish together or not at all (atomic create).
            now = datetime.now(UTC)
            view = TicketView(
                ticket_id=new_ticket_id(),
                run_id=new_run_id(),
                status="accepted",
                created_at=now,
                started_at=None,
                finished_at=None,
                answer=None,
                failure_code=None,
            )
            if self._fail_between_inserts:
                raise StorageFailure()
            self._by_id[(tenant, view.ticket_id)] = _Record(view, request_hash)
            self._by_key[(tenant, key)] = view.ticket_id
            return CreateResult(view.ticket_id, view.run_id, view.status, now, False)

    def get_ticket(self, *, tenant_id: str, ticket_id: str) -> TicketView | None:
        tenant = _inputs.validate_tenant_id(tenant_id)
        if not is_ticket_id(ticket_id):
            return None
        with self._lock:
            if self._unavailable:
                raise StorageUnavailable()
            record = self._by_id.get((tenant, ticket_id))
            return None if record is None else record.ticket

    # --- test-only helpers -------------------------------------------------
    def _counts(self) -> tuple[int, int]:
        with self._lock:
            return len(self._by_id), len(self._by_id)  # one run per ticket by construction

    def _force_run_failed(self, tenant_id: str, ticket_id: str, failure_code: str) -> None:
        with self._lock:
            record = self._by_id[(tenant_id, ticket_id)]
            record.ticket = replace(
                record.ticket,
                status="failed",
                failure_code=failure_code,
                finished_at=datetime.now(UTC),
            )
