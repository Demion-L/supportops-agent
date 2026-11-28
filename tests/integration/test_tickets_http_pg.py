"""``GET /v1/tickets/{ticket_id}`` end to end on real PostgreSQL (credentials + tickets, one pool)."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from test_alpha_beta_integration_pg import PEPPER, SETTINGS, bearer, issue

from hermes_service.app import create_app
from hermes_service.repositories.credentials import PostgresCredentialRepository
from hermes_service.repositories.tickets import PgTicketRepository
from hermes_service.routers.tickets import create_tickets_router

pytestmark = pytest.mark.postgres

MISSING = "tkt_" + "0" * 32


@pytest.fixture
def repo(pool):
    return PgTicketRepository(pool)


@pytest.fixture
def app(pool, repo):
    return create_app(SETTINGS, credentials=PostgresCredentialRepository(pool, touch_min_interval_seconds=0),
                      v1_routers=[create_tickets_router(repo)])


def make(repo, tenant="tenant-a", n=1):
    return repo.create_ticket(tenant_id=tenant, idempotency_key=f"pg-http-{tenant}-{n:04d}",
                              customer_id="cust-secret-pg", message="message-secret-pg",
                              order_id="order-secret-pg").ticket_id


async def _get_all(app, requests, limit=None):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    sem = asyncio.Semaphore(limit) if limit else None

    async def one(c, p, h):
        if sem is None:
            return await c.get(p, headers=h)
        async with sem:
            return await c.get(p, headers=h)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await asyncio.gather(*(one(c, p, h) for p, h in requests))


def get(app, path, headers=None):
    return asyncio.run(_get_all(app, [(path, headers or {})]))[0]


def shape(r):
    body = r.json()
    body["error"].pop("request_id", None)
    return r.status_code, body


def test_owner_reads_own_ticket_and_no_sensitive_field_leaves(app, repo, pool):
    tid = make(repo)
    key, cid = issue(pool, tenant="tenant-a")
    r = get(app, f"/v1/tickets/{tid}", bearer(key))
    assert r.status_code == 200 and set(r.json()) == {"ticket_id", "run_id", "status", "created_at"}
    assert r.json()["status"] == "accepted"
    for secret in ("cust-secret-pg", "message-secret-pg", "order-secret-pg", "tenant-a", cid, "key_hash"):
        assert secret not in r.text


def test_cross_tenant_unknown_and_malformed_are_the_same_404(app, repo, pool):
    foreign = make(repo, "tenant-b")
    key, _ = issue(pool, tenant="tenant-a")
    shapes = [shape(get(app, f"/v1/tickets/{t}", bearer(key))) for t in (foreign, MISSING, "garbage", "tkt_" + "A" * 32)]
    assert shapes[0][0] == 404 and all(s == shapes[0] for s in shapes)
    kb, _ = issue(pool, tenant="tenant-b")
    assert get(app, f"/v1/tickets/{foreign}", bearer(kb)).status_code == 200


def test_completed_and_failed_runs_are_projected(app, repo, pool):
    done, bad = make(repo, n=1), make(repo, n=2)
    with pool.connection() as conn:
        conn.execute("UPDATE runs SET status='completed', answer='the answer', finished_at=now() WHERE ticket_id=%s", (done,))
        conn.execute("UPDATE runs SET status='failed', failure_code='worker_lost', finished_at=now() WHERE ticket_id=%s", (bad,))
    key, _ = issue(pool)
    a, b = get(app, f"/v1/tickets/{done}", bearer(key)).json(), get(app, f"/v1/tickets/{bad}", bearer(key)).json()
    assert a["status"] == "completed" and a["answer"] == "the answer" and "failure" not in a
    assert b["status"] == "failed" and b["failure"] == {"code": "worker_lost"} and "answer" not in b


@pytest.mark.parametrize("evil", ["' OR '1'='1", "x'; DROP TABLE tickets;--", "tkt_" + "a" * 32 + "'; DELETE FROM tickets;--",
                                  "%27%3B%20DROP%20TABLE%20runs%3B--", "tkt_" + "a" * 31 + "%00"])
def test_sql_injection_attempts_are_404_and_change_nothing(app, repo, pool, evil):
    make(repo)
    key, _ = issue(pool)
    r = get(app, f"/v1/tickets/{evil}", bearer(key))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == 1


def test_revoked_scope_less_and_unknown_keys(app, repo, pool):
    tid = make(repo)
    revoked, _ = issue(pool, revoked=True)
    write_only, _ = issue(pool, scopes=("tickets:write",))
    r_rev, r_unknown = get(app, f"/v1/tickets/{tid}", bearer(revoked)), get(app, f"/v1/tickets/{tid}", bearer("x"))
    assert shape(r_rev) == shape(r_unknown) and r_rev.status_code == 401
    r403a = get(app, f"/v1/tickets/{tid}", bearer(write_only))
    r403b = get(app, f"/v1/tickets/{MISSING}", bearer(write_only))
    assert r403a.status_code == 403 and shape(r403a) == shape(r403b)


def test_concurrent_reads_across_tenants_while_creating_tickets(app, repo, pool):
    ids = {t: [make(repo, t, n) for n in range(1, 4)] for t in ("tenant-a", "tenant-b")}
    keys = {t: issue(pool, tenant=t)[0] for t in ids}
    errors: list[str] = []
    stop = threading.Event()

    def creator():
        n = 100
        while not stop.is_set():
            try:
                make(repo, "tenant-a", n)
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
            n += 1

    t = threading.Thread(target=creator)
    t.start()
    try:
        reqs, expect = [], []
        for _ in range(8):
            for me, other in (("tenant-a", "tenant-b"), ("tenant-b", "tenant-a")):
                for tid in ids[me]:
                    reqs.append((f"/v1/tickets/{tid}", bearer(keys[me]))); expect.append(200)
                for tid in ids[other]:
                    reqs.append((f"/v1/tickets/{tid}", bearer(keys[me]))); expect.append(404)
        # at most 3 requests in flight = the AuthenticationGate limit, so nothing is shed (see M2)
        responses = asyncio.run(_get_all(app, reqs, limit=3))
    finally:
        stop.set(); t.join(30)
    assert not errors, errors
    assert [r.status_code for r in responses] == expect  # own -> 200, foreign -> 404, never 500/503


def test_database_outage_is_503_without_leaking_connection_details(app, repo, pool, caplog):
    tid = make(repo)
    key, _ = issue(pool)
    assert get(app, f"/v1/tickets/{tid}", bearer(key)).status_code == 200
    pool.close()  # the same pool serves credentials too: authentication fails closed (503), never open
    caplog.set_level("DEBUG")
    r = get(app, f"/v1/tickets/{tid}", bearer(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
    text = r.text + caplog.text
    for needle in (PEPPER, key, "postgres", "psycopg", "password", "127.0.0.1", "api_credentials"):
        assert needle not in text
