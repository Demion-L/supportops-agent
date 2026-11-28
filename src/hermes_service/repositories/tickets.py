"""PostgreSQL TicketRepository: atomic ticket+run creation with tenant-scoped idempotency.

Correctness rests on database guarantees only (one transaction, the
``UNIQUE (tenant_id, idempotency_key)`` index, composite FK); there is no
application-level lock. READ COMMITTED is sufficient: the unique index serialises
concurrent inserts of the same key, and a conflicting row is only ever a committed
one, which always has its run (both rows commit together).

SQL text is constant; every value is a bound parameter. This module never touches
``api_credentials`` and performs no external effect (no LLM, no network).
"""

from __future__ import annotations

import hmac
from typing import Final

import psycopg

from hermes_service.db.errors import IdempotencyConflict, StorageFailure, StorageUnavailable
from hermes_service.db.pool import DatabasePool
from hermes_service.ids import is_ticket_id, new_run_id, new_ticket_id
from hermes_service.repositories import _inputs
from hermes_service.repositories.protocols import CreateResult, TicketView

_INSERT_TICKET: Final = (
    "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, "
    "idempotency_key, request_hash) VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING created_at"
)
_INSERT_RUN: Final = "INSERT INTO runs (run_id, tenant_id, ticket_id) VALUES (%s, %s, %s) RETURNING status"
_SELECT_BY_KEY: Final = (
    "SELECT t.ticket_id, t.request_hash, t.created_at, r.run_id, r.status "
    "FROM tickets t JOIN runs r ON r.tenant_id = t.tenant_id AND r.ticket_id = t.ticket_id "
    "WHERE t.tenant_id = %s AND t.idempotency_key = %s"
)
_SELECT_TICKET: Final = (
    "SELECT t.ticket_id, r.run_id, r.status, t.created_at, r.started_at, r.finished_at, "
    "r.answer, r.failure_code "
    "FROM tickets t JOIN runs r ON r.tenant_id = t.tenant_id AND r.ticket_id = t.ticket_id "
    "WHERE t.tenant_id = %s AND t.ticket_id = %s"
)


class PgTicketRepository:
    def __init__(self, pool: DatabasePool) -> None:
        self._pool = pool

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
        ticket_id, run_id = new_ticket_id(), new_run_id()
        failed = False
        try:
            with self._pool.connection() as conn:
                with conn.transaction():
                    created = conn.execute(
                        _INSERT_TICKET,
                        (ticket_id, tenant, customer, text, order, key, request_hash),
                    ).fetchone()
                    if created is not None:
                        run = conn.execute(_INSERT_RUN, (run_id, tenant, ticket_id)).fetchone()
                        if run is None:  # unreachable for INSERT ... RETURNING; fail closed anyway
                            raise StorageFailure()
                        return CreateResult(
                            ticket_id=ticket_id,
                            run_id=run_id,
                            status=run["status"],
                            created_at=created["created_at"],
                            replayed=False,
                        )
                # Conflict: the existing row is committed (the insert waited for any
                # in-flight transaction), so it and its run are visible to this statement.
                existing = conn.execute(_SELECT_BY_KEY, (tenant, key)).fetchone()
        except psycopg.Error:
            # OperationalError (outage, lock/statement timeout) was already converted to
            # StorageUnavailable by the pool; what remains is a non-availability failure.
            failed = True  # raised outside the handler so no driver error stays in __context__
        if failed:
            raise StorageFailure()

        if existing is None:
            # The committed row vanished between the conflict and the lookup (e.g. retention).
            # Fail closed; the client retries with the same key.
            raise StorageUnavailable()
        if not hmac.compare_digest(bytes(existing["request_hash"]), request_hash):
            raise IdempotencyConflict()
        return CreateResult(
            ticket_id=existing["ticket_id"],
            run_id=existing["run_id"],
            status=existing["status"],
            created_at=existing["created_at"],
            replayed=True,
        )

    def get_ticket(self, *, tenant_id: str, ticket_id: str) -> TicketView | None:
        tenant = _inputs.validate_tenant_id(tenant_id)
        if not is_ticket_id(ticket_id):
            return None  # indistinguishable from unknown; no database access
        failed = False
        row = None
        try:
            with self._pool.connection() as conn:
                row = conn.execute(_SELECT_TICKET, (tenant, ticket_id)).fetchone()
        except psycopg.Error:
            failed = True
        if failed:
            raise StorageFailure()
        if row is None:
            return None
        return TicketView(
            ticket_id=row["ticket_id"],
            run_id=row["run_id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            answer=row["answer"],
            failure_code=row["failure_code"],
        )
