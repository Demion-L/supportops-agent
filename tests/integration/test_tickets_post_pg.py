"""``POST /v1/tickets`` (+ GET) end to end on real PostgreSQL: persistence, idempotency, isolation."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_alpha_beta_integration_pg import PEPPER, SETTINGS, bearer, issue
from test_tickets_http_pg import MISSING, shape

from supportops_service.app import create_app
from supportops_service.repositories.credentials import PostgresCredentialRepository
from supportops_service.repositories.tickets import PgTicketRepository
from supportops_service.routers.tickets import create_tickets_router

pytestmark = pytest.mark.postgres

BODY = {"customer_id": "shared-cust", "message": "Where is my order?", "order_id": "ord-9"}


@pytest.fixture
def app(pool):
    return create_app(SETTINGS, credentials=PostgresCredentialRepository(pool, touch_min_interval_seconds=0),
                      v1_routers=[create_tickets_router(PgTicketRepository(pool))])


def rw(pool, tenant="tenant-a"):
    return bearer(issue(pool, tenant=tenant, scopes=("tickets:write", "tickets:read"))[0])


async def _run(app, calls, limit=3):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    sem = asyncio.Semaphore(limit)  # <= AuthenticationGate limit (3 < pool of 4): nothing is shed

    async def one(c, method, path, headers, content):
        async with sem:
            return await c.request(method, path, headers=headers, content=content)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await asyncio.gather(*(one(c, *call) for call in calls))


def post(app, headers, body=BODY, key="pg-idem-0001", raw=None):
    h = {**headers, "Content-Type": "application/json", "Idempotency-Key": key}
    content = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode()  # raw UTF-8 on the wire
    return asyncio.run(_run(app, [("POST", "/v1/tickets", h, content)]))[0]


def get(app, headers, tid):
    return asyncio.run(_run(app, [("GET", f"/v1/tickets/{tid}", headers, None)]))[0]


def counts(pool):
    with pool.connection() as conn:
        return (conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"],
                conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"])


def test_create_persists_tenant_from_credential_and_get_round_trips(app, pool):
    h = rw(pool, "tenant-a")
    r = post(app, h)
    assert r.status_code == 202 and r.headers["location"] == f"/v1/tickets/{r.json()['ticket_id']}"
    with pool.connection() as conn:
        row = conn.execute("SELECT tenant_id, customer_id, message, order_id FROM tickets").fetchone()
        run = conn.execute("SELECT status, tenant_id FROM runs").fetchone()
    assert (row["tenant_id"], row["customer_id"], row["order_id"]) == ("tenant-a", "shared-cust", "ord-9")
    assert row["message"] == BODY["message"] and run["status"] == "accepted" and run["tenant_id"] == "tenant-a"
    g = get(app, h, r.json()["ticket_id"])
    assert g.status_code == 200 and g.json()["run_id"] == r.json()["run_id"]
    assert "shared-cust" not in g.text and "Where is my order" not in g.text


def test_tenant_in_body_is_422_and_nothing_is_written(app, pool):
    r = post(app, rw(pool), body={**BODY, "tenant_id": "tenant-b"})
    assert r.status_code == 422 and "tenant-b" not in r.text and counts(pool) == (0, 0)


def test_replay_and_conflict_on_postgres(app, pool):
    h = rw(pool)
    first = post(app, h)
    again = post(app, h)
    assert (first.status_code, again.status_code) == (202, 200) and again.json() == first.json()
    assert again.headers["idempotent-replayed"] == "true"
    conflict = post(app, h, body={**BODY, "message": "zz-first-body-marker"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_key_reuse"
    assert "zz-first-body-marker" not in conflict.text and "Where is my order" not in conflict.text
    assert counts(pool) == (1, 1)


def test_same_customer_id_and_same_key_in_two_tenants_never_cross(app, pool):
    ha, hb = rw(pool, "tenant-a"), rw(pool, "tenant-b")
    a, b = post(app, ha), post(app, hb)
    assert a.status_code == b.status_code == 202 and a.json()["ticket_id"] != b.json()["ticket_id"]
    assert counts(pool) == (2, 2)
    foreign_a, foreign_b = get(app, hb, a.json()["ticket_id"]), get(app, ha, b.json()["ticket_id"])
    assert foreign_a.status_code == foreign_b.status_code == 404
    assert shape(foreign_a) == shape(get(app, hb, MISSING))
    assert get(app, ha, a.json()["ticket_id"]).status_code == 200
    # tenant-b's conflicting body is a conflict in tenant-b only; tenant-a still replays cleanly
    assert post(app, hb, body={**BODY, "message": "other"}).status_code == 409
    assert post(app, ha).status_code == 200 and counts(pool) == (2, 2)


def test_twenty_concurrent_identical_creates_make_one_ticket_and_one_run(app, pool):
    h = {**rw(pool), "Content-Type": "application/json", "Idempotency-Key": "pg-idem-race"}
    rs = asyncio.run(_run(app, [("POST", "/v1/tickets", h, json.dumps(BODY).encode())] * 20))
    assert sorted(r.status_code for r in rs) == [200] * 19 + [202]
    assert len({r.json()["ticket_id"] for r in rs}) == 1 and counts(pool) == (1, 1)


def test_concurrent_same_key_different_bodies_have_exactly_one_winner(app, pool):
    h = {**rw(pool), "Content-Type": "application/json", "Idempotency-Key": "pg-idem-race2"}
    calls = [("POST", "/v1/tickets", h, json.dumps({**BODY, "message": f"m{i % 2}"}).encode()) for i in range(20)]
    rs = asyncio.run(_run(app, calls))
    codes = [r.status_code for r in rs]
    assert codes.count(202) == 1 and set(codes) <= {200, 202, 409} and counts(pool) == (1, 1)


@pytest.mark.parametrize("evil", ["x'); DROP TABLE tickets;--", "' OR '1'='1", "Robert'); DELETE FROM runs;--"])
def test_sql_metacharacters_in_body_and_key_are_data(app, pool, evil):
    h = rw(pool)
    r = post(app, h, body={"customer_id": evil, "message": evil, "order_id": evil})
    assert r.status_code == 202 and counts(pool) == (1, 1)
    with pool.connection() as conn:
        assert conn.execute("SELECT message FROM tickets").fetchone()["message"] == evil
    assert post(app, h, key="bad key; DROP TABLE tickets").status_code == 400


def test_unicode_and_boundary_sizes_round_trip(app, pool):
    h = rw(pool)
    body = {"customer_id": "κλιεντ-😀" * 10, "message": "ß" * 4000, "order_id": "o" * 100}
    assert post(app, h, body=body).status_code == 202
    with pool.connection() as conn:
        assert conn.execute("SELECT message FROM tickets").fetchone()["message"] == "ß" * 4000


def test_auth_and_scope_failures_write_nothing(app, pool):
    ro = bearer(issue(pool, scopes=("tickets:read",))[0])
    revoked = bearer(issue(pool, revoked=True)[0])
    r_ro, r_rev, r_unknown = post(app, ro), post(app, revoked), post(app, bearer("nope"))
    assert r_ro.status_code == 403 and r_rev.status_code == r_unknown.status_code == 401
    assert shape(r_rev) == shape(r_unknown) and counts(pool) == (0, 0)


def test_outage_on_create_is_503_and_leaks_nothing(app, pool, caplog):
    h = rw(pool)
    assert post(app, h).status_code == 202
    pool.close()
    caplog.set_level("DEBUG")
    r = post(app, h, key="pg-idem-after")
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
    text = r.text + caplog.text
    for needle in (PEPPER, "postgres", "psycopg", "password", "127.0.0.1", "api_credentials", "pg-idem-after"):
        assert needle not in text
