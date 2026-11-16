"""Initial-schema invariants, enforced by PostgreSQL itself."""

import uuid

import psycopg
import pytest
from psycopg import errors as pgerr

pytestmark = pytest.mark.postgres

H = b"\x00" * 32


def tid():
    return "tkt_" + uuid.uuid4().hex


def rid():
    return "run_" + uuid.uuid4().hex


def add_ticket(c, *, tenant="acme", customer="cust-1", key=None, ticket=None):
    ticket = ticket or tid()
    c.execute(
        "INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, idempotency_key, request_hash) "
        "VALUES (%s,%s,%s,'hi',%s,%s)",
        (ticket, tenant, customer, key or uuid.uuid4().hex, H),
    )
    return ticket


def add_run(c, ticket, tenant="acme", **cols):
    run = rid()
    names = ["run_id", "tenant_id", "ticket_id", *cols]
    c.execute(
        f"INSERT INTO runs ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
        (run, tenant, ticket, *cols.values()),
    )
    return run


# ---- tenant identity ---------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "ACME", "-acme", "a_b", "a b", "a" * 64, "ac;me", "é"])
def test_ticket_tenant_must_be_a_slug(schema_pool, bad):
    with schema_pool.connection() as c, pytest.raises(pgerr.CheckViolation):
        add_ticket(c, tenant=bad)


@pytest.mark.parametrize("bad", ["abc", "tkt_XYZ", "tkt_" + "a" * 31, "run_" + "a" * 32])
def test_ticket_id_format(schema_pool, bad):
    with schema_pool.connection() as c, pytest.raises(pgerr.CheckViolation):
        add_ticket(c, ticket=bad)


def test_credential_tenant_prefix_and_hash_constraints(schema_pool):
    sql = "INSERT INTO api_credentials (credential_id, tenant_id, key_prefix, key_hash, pepper_id, scopes) VALUES (%s,%s,%s,%s,'p1',%s)"
    ok = (uuid.uuid4(), "acme", "abcdefghijkl", H, ["tickets:read"])
    with schema_pool.connection() as c:
        c.execute(sql, ok)
        for bad in (
            (uuid.uuid4(), "BAD TENANT", "mnopqrstuvwx", H, []),
            (uuid.uuid4(), "acme", "ABCDEFGHIJKL", H, []),  # prefix alphabet
            (uuid.uuid4(), "acme", "short", H, []),
            (uuid.uuid4(), "acme", "mnopqrstuvwx", b"\x00" * 31, []),  # hash length
        ):
            with pytest.raises(pgerr.CheckViolation):
                c.execute(sql, bad)
        with pytest.raises(pgerr.UniqueViolation):  # prefix unique
            c.execute(sql, (uuid.uuid4(), "other", "abcdefghijkl", H, []))


# ---- composite FK ------------------------------------------------------------------

def test_run_must_match_ticket_tenant(schema_pool):
    with schema_pool.connection() as c:
        t = add_ticket(c, tenant="acme")
        with pytest.raises(pgerr.ForeignKeyViolation):
            add_run(c, t, tenant="globex")  # right ticket, wrong tenant
        add_run(c, t, tenant="acme")  # control


def test_run_requires_existing_ticket_and_is_one_per_ticket(schema_pool):
    with schema_pool.connection() as c:
        with pytest.raises(pgerr.ForeignKeyViolation):
            add_run(c, tid())
        t = add_ticket(c)
        add_run(c, t)
        with pytest.raises(pgerr.UniqueViolation):
            add_run(c, t)


def test_ticket_with_run_cannot_be_deleted(schema_pool):
    with schema_pool.connection() as c:
        t = add_ticket(c)
        add_run(c, t)
        with pytest.raises(pgerr.ForeignKeyViolation):
            c.execute("DELETE FROM tickets WHERE ticket_id = %s", (t,))


# ---- idempotency -------------------------------------------------------------------

def test_idempotency_key_unique_within_tenant_only(schema_pool):
    with schema_pool.connection() as c:
        add_ticket(c, tenant="acme", key="key-1")
        add_ticket(c, tenant="globex", key="key-1")  # other tenant: independent
        with pytest.raises(pgerr.UniqueViolation):
            add_ticket(c, tenant="acme", key="key-1")


# ---- immutability ------------------------------------------------------------------

@pytest.mark.parametrize(
    "assignment, value",
    [("tenant_id", "globex"), ("customer_id", "cust-2"), ("ticket_id", "tkt_" + "b" * 32), ("message", "x"), ("idempotency_key", "k2")],
)
def test_ticket_rows_are_immutable(schema_pool, assignment, value):
    with schema_pool.connection() as c:
        t = add_ticket(c)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute(f"UPDATE tickets SET {assignment} = %s WHERE ticket_id = %s", (value, t))
        row = c.execute("SELECT tenant_id, customer_id FROM tickets WHERE ticket_id=%s", (t,)).fetchone()
        assert (row["tenant_id"], row["customer_id"]) == ("acme", "cust-1")


def test_run_binding_is_immutable(schema_pool):
    with schema_pool.connection() as c:
        t1, t2 = add_ticket(c), add_ticket(c)
        r = add_run(c, t1)
        for col, val in (("ticket_id", t2), ("run_id", rid()), ("tenant_id", "globex")):
            with pytest.raises((psycopg.errors.IntegrityConstraintViolation, pgerr.ForeignKeyViolation)):
                c.execute(f"UPDATE runs SET {col} = %s WHERE run_id = %s", (val, r))


# ---- run state machine / fencing ---------------------------------------------------

def test_run_status_and_field_checks(schema_pool):
    with schema_pool.connection() as c:
        def fresh():
            return add_ticket(c)

        with pytest.raises(pgerr.CheckViolation):
            add_run(c, fresh(), status="bogus")
        with pytest.raises(pgerr.CheckViolation):  # completed needs answer + finished_at
            add_run(c, fresh(), status="completed")
        with pytest.raises(pgerr.CheckViolation):  # failed needs failure_code
            add_run(c, fresh(), status="failed", finished_at="2026-01-01T00:00:00Z")
        with pytest.raises(pgerr.CheckViolation):  # running needs lease + attempt>=1
            add_run(c, fresh(), status="running")
        with pytest.raises(pgerr.CheckViolation):  # attempt <= max_attempts
            add_run(c, fresh(), attempt=2, max_attempts=1)
        with pytest.raises(pgerr.CheckViolation):  # failure_code format
            add_run(c, fresh(), failure_code="Bad Code!")
        add_run(c, fresh(), status="running", attempt=1, lease_owner="w1", lease_until="2030-01-01T00:00:00Z")


def test_terminal_runs_and_attempt_cannot_regress(schema_pool):
    with schema_pool.connection() as c:
        r = add_run(c, add_ticket(c), max_attempts=3)
        c.execute("UPDATE runs SET status='running', attempt=2, lease_owner='w', lease_until=now()+interval '1 min' WHERE run_id=%s", (r,))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute("UPDATE runs SET attempt=1 WHERE run_id=%s", (r,))
        c.execute("UPDATE runs SET status='completed', answer='ok', finished_at=now(), lease_owner=NULL, lease_until=NULL WHERE run_id=%s", (r,))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute("UPDATE runs SET status='accepted' WHERE run_id=%s", (r,))
