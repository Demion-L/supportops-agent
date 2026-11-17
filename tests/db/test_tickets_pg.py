"""PostgreSQL-only guarantees for PgTicketRepository (need a real server).

T4 (blocking behaviour), T8 (crash rollback), T9 (no DSN leak), T11 restart.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from pg_test_helpers import make_settings
from supportops_service.db.errors import IdempotencyConflict, StorageFailure, StorageUnavailable
from supportops_service.db.pool import DatabasePool
from supportops_service.repositories import _inputs
from supportops_service.repositories import tickets as tickets_module
from supportops_service.repositories.tickets import PgTicketRepository

pytestmark = pytest.mark.postgres
A = "tenant-a"
KEY = "key-0000001"


def make(repo, key=KEY, message="hello", tenant=A):
    return repo.create_ticket(
        tenant_id=tenant, idempotency_key=key, customer_id="cust-1", message=message, order_id="ord-1"
    )


def counts(pool):
    with pool.connection() as c:
        return (
            c.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"],
            c.execute("SELECT count(*) AS n FROM runs").fetchone()["n"],
        )


@pytest.fixture
def repo(migrated_pool):
    return PgTicketRepository(migrated_pool)


def _wait_blocked(dsn, timeout=5.0):
    """Block until a repository backend is waiting on a lock (proves the wait path)."""
    deadline = time.time() + timeout
    with psycopg.connect(dsn, autocommit=True) as admin:
        while time.time() < deadline:
            n = admin.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name='supportops-service' "
                "AND wait_event_type='Lock' AND state='active'"
            ).fetchone()[0]
            if n:
                return
            time.sleep(0.02)
    raise AssertionError("repository call never blocked on the in-flight insert")


def _raw_conn(dsn):
    return psycopg.connect(dsn, autocommit=True)


def test_restart_new_instance_and_new_pool_see_same_data(database, migrated_pool):  # T11
    first = make(PgTicketRepository(migrated_pool))
    migrated_pool.close()
    fresh_pool = DatabasePool(make_settings(database))
    fresh_pool.open()
    try:
        fresh = PgTicketRepository(fresh_pool)
        view = fresh.get_ticket(tenant_id=A, ticket_id=first.ticket_id)
        assert view is not None and view.run_id == first.run_id
        again = make(fresh)
        assert again.replayed and again.ticket_id == first.ticket_id
        assert counts(fresh_pool) == (1, 1)
    finally:
        fresh_pool.close()


def test_run_is_created_with_defaults_and_composite_binding(repo, migrated_pool):
    r = make(repo)
    with migrated_pool.connection() as c:
        row = c.execute("SELECT * FROM runs WHERE run_id=%s", (r.run_id,)).fetchone()
    assert (row["tenant_id"], row["ticket_id"], row["status"], row["attempt"]) == (A, r.ticket_id, "accepted", 0)
    assert row["lease_owner"] is None and row["answer"] is None


def test_every_ticket_has_exactly_one_run_after_races(repo, migrated_pool):
    with ThreadPoolExecutor(20) as ex:
        list(ex.map(lambda i: make(repo, key="key-par-%04d" % (i % 5)), range(40)))
    with migrated_pool.connection() as c:
        orphans = c.execute(
            "SELECT count(*) AS n FROM tickets t LEFT JOIN runs r ON r.ticket_id=t.ticket_id WHERE r.run_id IS NULL"
        ).fetchone()["n"]
    assert orphans == 0 and counts(migrated_pool) == (5, 5)


def test_waiting_insert_returns_replay_when_holder_commits(database, migrated_pool, repo):
    holder = _raw_conn(database)
    holder.execute("BEGIN")
    tid, rid = "tkt_" + "a" * 32, "run_" + "a" * 32
    holder.execute(
        "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, idempotency_key, request_hash) "
        "VALUES (%s,%s,'cust-1','hello','ord-1',%s,%s)",
        (tid, A, KEY, _inputs.compute_request_hash("cust-1", "hello", "ord-1")),
    )
    holder.execute("INSERT INTO runs (run_id, tenant_id, ticket_id) VALUES (%s,%s,%s)", (rid, A, tid))
    out: list = []
    t = threading.Thread(target=lambda: out.append(make(repo)))
    t.start()
    _wait_blocked(database)
    assert t.is_alive(), "second create must block on the in-flight conflicting insert"
    holder.execute("COMMIT")
    t.join(5)
    holder.close()
    assert out[0].replayed and (out[0].ticket_id, out[0].run_id) == (tid, rid)
    assert counts(migrated_pool) == (1, 1)


def test_waiting_insert_with_other_payload_conflicts_when_holder_commits(database, migrated_pool, repo):
    holder = _raw_conn(database)
    holder.execute("BEGIN")
    tid, rid = "tkt_" + "b" * 32, "run_" + "b" * 32
    holder.execute(
        "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, idempotency_key, request_hash) "
        "VALUES (%s,%s,'cust-1','hello','ord-1',%s,%s)",
        (tid, A, KEY, _inputs.compute_request_hash("cust-1", "hello", "ord-1")),
    )
    holder.execute("INSERT INTO runs (run_id, tenant_id, ticket_id) VALUES (%s,%s,%s)", (rid, A, tid))
    out: list = []

    def work():
        try:
            make(repo, message="different")
        except Exception as e:  # noqa: BLE001
            out.append(e)

    t = threading.Thread(target=work)
    t.start()
    _wait_blocked(database)
    assert t.is_alive()
    holder.execute("COMMIT")
    t.join(5)
    holder.close()
    assert len(out) == 1 and isinstance(out[0], IdempotencyConflict)
    assert counts(migrated_pool) == (1, 1)


def test_waiting_insert_proceeds_when_holder_rolls_back(database, migrated_pool, repo):
    holder = _raw_conn(database)
    holder.execute("BEGIN")
    holder.execute(
        "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, idempotency_key, request_hash) "
        "VALUES (%s,%s,'c','m',NULL,%s,%s)",
        ("tkt_" + "c" * 32, A, KEY, b"\x00" * 32),
    )
    out: list = []
    t = threading.Thread(target=lambda: out.append(make(repo)))
    t.start()
    _wait_blocked(database)
    assert t.is_alive()
    assert t.is_alive()
    holder.execute("ROLLBACK")
    t.join(5)
    holder.close()
    assert not out[0].replayed and counts(migrated_pool) == (1, 1)


def test_lock_timeout_on_blocked_insert_is_503_class_and_retry_succeeds(database):
    pool = DatabasePool(make_settings(database, lock_timeout_ms=300))
    pool.open()
    try:
        from supportops_service.db.migrate import migrate

        migrate(pool)
        repo = PgTicketRepository(pool)
        holder = _raw_conn(database)
        holder.execute("BEGIN")
        holder.execute(
            "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, idempotency_key, request_hash) "
            "VALUES (%s,%s,'c','m',NULL,%s,%s)",
            ("tkt_" + "d" * 32, A, KEY, b"\x00" * 32),
        )
        with pytest.raises(StorageUnavailable) as exc:
            make(repo)
        assert str(exc.value) == "Storage is unavailable."
        holder.execute("ROLLBACK")
        holder.close()
        assert counts(pool) == (0, 0)
        assert not make(repo).replayed
    finally:
        pool.close()


def test_backend_killed_mid_transaction_leaves_no_rows(database, migrated_pool, repo):  # T8
    lock_conn = _raw_conn(database)
    lock_conn.execute("SELECT pg_advisory_lock(7777)")
    with migrated_pool.connection() as c:
        c.execute(
            "CREATE FUNCTION wp2_block() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN PERFORM pg_advisory_xact_lock(7777); RETURN NEW; END; $$; "
            "CREATE TRIGGER wp2_block BEFORE INSERT ON runs FOR EACH ROW EXECUTE FUNCTION wp2_block()"
        )
    out: list = []

    def work():
        try:
            out.append(make(repo))
        except Exception as e:  # noqa: BLE001
            out.append(e)

    t = threading.Thread(target=work)
    t.start()
    killed = 0
    deadline = time.time() + 4
    while time.time() < deadline and not killed:
        with _raw_conn(database) as admin:
            row = admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name='supportops-service' AND wait_event_type='Lock' AND state='active'"
            ).fetchall()
            killed = len(row)
        time.sleep(0.05)
    t.join(5)
    lock_conn.close()
    assert killed == 1
    assert isinstance(out[0], StorageUnavailable)
    with migrated_pool.connection() as c:
        c.execute("DROP TRIGGER wp2_block ON runs; DROP FUNCTION wp2_block()")
    assert counts(migrated_pool) == (0, 0)  # ticket INSERT was rolled back with the connection
    again = make(repo)
    assert not again.replayed and counts(migrated_pool) == (1, 1)


def test_commit_then_lost_response_replays_same_ids(repo, migrated_pool):
    first = make(repo)  # response "lost": client retries with the same key
    retry = make(repo)
    assert retry.replayed and (retry.ticket_id, retry.run_id) == (first.ticket_id, first.run_id)


def test_row_vanishing_between_conflict_and_lookup_fails_closed(repo, monkeypatch):
    make(repo)
    monkeypatch.setattr(tickets_module, "_SELECT_BY_KEY", tickets_module._SELECT_BY_KEY + " AND false")
    with pytest.raises(StorageUnavailable):
        make(repo)


# ---- error sanitisation (T9, SR) ------------------------------------------------------
def test_unreachable_database_never_leaks_dsn(caplog):
    marker = "S3CRET_PW_MARKER_" + uuid.uuid4().hex
    pool = DatabasePool(
        make_settings(f"postgresql://user:{marker}@127.0.0.1:1/db", connect_timeout=1, open_timeout=1.5)
    )
    repo = PgTicketRepository(pool)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(StorageUnavailable):
            pool.open()
        with pytest.raises(StorageUnavailable) as e1:
            make(repo)
        with pytest.raises(StorageUnavailable) as e2:
            repo.get_ticket(tenant_id=A, ticket_id="tkt_" + "0" * 32)
    for exc in (e1.value, e2.value):
        assert marker not in repr(exc) and marker not in str(exc) and exc.__cause__ is None
        assert exc.__context__ is None
    # Note: psycopg_pool itself logs host:port (WP1 behaviour, not the repository); the secret must never appear.
    assert marker not in caplog.text


def test_constraint_violation_is_sanitised(repo, migrated_pool, monkeypatch):
    marker = "PII_MARKER_" + uuid.uuid4().hex
    # Force a primary-key collision on ticket_id: the raw driver error would echo the key values.
    first = make(repo, message=marker)
    monkeypatch.setattr(tickets_module, "new_ticket_id", lambda: first.ticket_id)
    with pytest.raises(StorageFailure) as exc:
        make(repo, key="key-other-01", message=marker)
    assert marker not in str(exc.value) and first.ticket_id not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert counts(migrated_pool) == (1, 1)


def test_no_pii_in_logs(repo, caplog):
    marker = "PII_MARKER_" + uuid.uuid4().hex
    with caplog.at_level(logging.DEBUG):
        make(repo, message=marker)
        make(repo, message=marker)
        with pytest.raises(IdempotencyConflict):
            make(repo, message=marker + "x")
    assert marker not in caplog.text and KEY not in caplog.text


def test_sql_is_never_built_from_data(repo, migrated_pool):
    payload = "x'); DROP TABLE runs; --"
    r = make(repo, message=payload)
    assert counts(migrated_pool) == (1, 1)
    with migrated_pool.connection() as c:
        row = c.execute("SELECT message FROM tickets WHERE ticket_id=%s", (r.ticket_id,)).fetchone()
    assert row["message"] == payload
