"""Uniform fault-injection / inspection harness so one contract suite drives both the
in-memory fake and the PostgreSQL repository."""

from __future__ import annotations

from contextlib import contextmanager

from supportops_service.db.pool import DatabasePool
from supportops_service.repositories.fakes import InMemoryTicketRepository
from supportops_service.repositories.tickets import PgTicketRepository

RUN_FAIL_TRIGGER = """
CREATE FUNCTION wp2_fail_run_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'injected failure' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER wp2_fail_run_insert BEFORE INSERT ON runs
    FOR EACH ROW EXECUTE FUNCTION wp2_fail_run_insert();
"""
RUN_FAIL_TRIGGER_DROP = "DROP TRIGGER wp2_fail_run_insert ON runs; DROP FUNCTION wp2_fail_run_insert();"


class FakeHarness:
    name = "fake"

    def __init__(self) -> None:
        self.repo = InMemoryTicketRepository()

    def counts(self) -> tuple[int, int]:
        return self.repo._counts()

    @contextmanager
    def fail_between_inserts(self):
        self.repo._fail_between_inserts = True
        try:
            yield
        finally:
            self.repo._fail_between_inserts = False

    def make_unavailable(self) -> None:
        self.repo._unavailable = True

    def force_run_failed(self, tenant_id: str, ticket_id: str, code: str) -> None:
        self.repo._force_run_failed(tenant_id, ticket_id, code)


class PgHarness:
    name = "pg"

    def __init__(self, pool: DatabasePool) -> None:
        self.pool = pool
        self.repo = PgTicketRepository(pool)

    def counts(self) -> tuple[int, int]:
        with self.pool.connection() as c:
            t = c.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"]
            r = c.execute("SELECT count(*) AS n FROM runs").fetchone()["n"]
        return t, r

    @contextmanager
    def fail_between_inserts(self):
        with self.pool.connection() as c:
            c.execute(RUN_FAIL_TRIGGER)
        try:
            yield
        finally:
            with self.pool.connection() as c:
                c.execute(RUN_FAIL_TRIGGER_DROP)

    def make_unavailable(self) -> None:
        self.pool.close()

    def force_run_failed(self, tenant_id: str, ticket_id: str, code: str) -> None:
        with self.pool.connection() as c:
            c.execute(
                "UPDATE runs SET status='failed', failure_code=%s, finished_at=now() "
                "WHERE tenant_id=%s AND ticket_id=%s",
                (code, tenant_id, ticket_id),
            )
