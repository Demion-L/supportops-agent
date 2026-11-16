"""INTEGRATION-GATE-01: Alpha authentication + Beta ticket repository + create_app, on real PostgreSQL.

I1-I8 from the gate brief. No HTTP ticket endpoints exist yet, so I5/I6 run at repository level;
the small test-only routers below exist ONLY in these tests (they are never part of the app).
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import httpx
import pytest
from fastapi import APIRouter, Depends

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings
from supportops_service.db.errors import IdempotencyConflict, StorageFailure, StorageUnavailable
from supportops_service.interfaces import Scope, TenantContext
from supportops_service.repositories.credentials import PostgresCredentialRepository
from supportops_service.repositories.tickets import PgTicketRepository
from supportops_service.security.credentials import compute_key_hash, generate_api_key
from supportops_service.security.dependencies import SecuredRoute, require_scope

pytestmark = pytest.mark.postgres

PEPPER = "integration-pepper-0123456789abcdef"
SETTINGS = ServiceSettings(pepper=PEPPER, pepper_id="p1")  # type: ignore[arg-type]


def issue(pool, *, tenant="tenant-a", scopes=("tickets:read",), revoked=False):
    key = generate_api_key()
    cid = str(uuid.uuid4())
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO api_credentials (credential_id, tenant_id, key_prefix, key_hash, pepper_id,"
            " scopes, revoked_at) VALUES (%s,%s,%s,%s,'p1',%s, CASE WHEN %s THEN now() END)",
            (cid, tenant, key.prefix, compute_key_hash(PEPPER.encode(), key.prefix, key.secret),
             list(scopes), revoked),
        )
    return key.render(), cid


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def make_router(tickets: PgTicketRepository | None = None):
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.get("/it/whoami")
    def whoami(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"tenant_id": ctx.tenant_id, "credential_id": ctx.credential_id,
                "scopes": sorted(s.value for s in ctx.scopes)}

    @router.get("/it/raise/{kind}")
    def raiser(kind: str, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        raise {"unavail": StorageUnavailable, "conflict": IdempotencyConflict,
               "failure": StorageFailure}[kind]()

    return router


async def call(app, requests):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.gather(*(client.request(m, p, headers=h) for m, p, h in requests))


@pytest.fixture
def app(pool):
    return create_app(SETTINGS, credentials=PostgresCredentialRepository(pool, touch_min_interval_seconds=0),
                      v1_routers=[make_router()])


def one(app, path, headers=None, method="GET"):
    return asyncio.run(call(app, [(method, path, headers or {})]))[0]


def test_i1_valid_credential_yields_tenant_context_from_postgres(pool, app):
    key, cid = issue(pool, tenant="tenant-a", scopes=("tickets:read", "bogus:scope"))
    r = one(app, "/v1/it/whoami", bearer(key))
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "tenant-a", "credential_id": cid, "scopes": ["tickets:read"]}


def test_i2_revoked_credential_is_401_including_revocation_after_first_use(pool, app):
    key, cid = issue(pool)
    assert one(app, "/v1/it/whoami", bearer(key)).status_code == 200
    with pool.connection() as conn:
        conn.execute("UPDATE api_credentials SET revoked_at = now() WHERE credential_id = %s", (cid,))
    r = one(app, "/v1/it/whoami", bearer(key))
    revoked_at_birth, _ = issue(pool, revoked=True)
    r2 = one(app, "/v1/it/whoami", bearer(revoked_at_birth))
    for resp in (r, r2):
        assert resp.status_code == 401 and resp.headers["www-authenticate"] == "Bearer"
        assert resp.json()["error"]["code"] == "unauthenticated"
    assert r.content.split(b"request_id")[0] == r2.content.split(b"request_id")[0]  # uniform body


def test_i3_insufficient_scope_is_403(pool, app):
    key, _ = issue(pool, scopes=("tickets:write",))
    r = one(app, "/v1/it/whoami", bearer(key))
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"


def test_i4_unavailable_postgres_is_503_without_leaks(pool, make_pool):
    dead = make_pool()
    key, _ = issue(pool)
    dead.close()
    app = create_app(SETTINGS, credentials=PostgresCredentialRepository(dead), v1_routers=[make_router()])
    r = one(app, "/v1/it/whoami", bearer(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
    assert "retry-after" not in r.headers  # not a gate saturation
    for secret in ("postgresql", "psycopg", "integ_test_pw", "127.0.0.1", key):
        assert secret not in r.text


def test_error_mapping_for_repository_exceptions(pool, app):
    key, _ = issue(pool)
    got = {k: one(app, f"/v1/it/raise/{k}", bearer(key)) for k in ("unavail", "conflict", "failure")}
    assert got["unavail"].status_code == 503 and "retry-after" not in got["unavail"].headers
    assert got["unavail"].json()["error"]["code"] == "service_unavailable"
    assert got["conflict"].status_code == 409
    assert got["conflict"].json()["error"]["code"] == "idempotency_key_reuse"
    assert got["failure"].status_code == 500 and got["failure"].json()["error"]["code"] == "internal_error"
    for r in got.values():
        assert "Storage" not in r.text and "Idempotency key was" not in r.text.replace(
            "Idempotency key was already used with a different request.", "")


def test_i5_i6_two_tenants_same_idempotency_key_and_replay(pool):
    repo = PgTicketRepository(pool)
    kw = dict(idempotency_key="same-key-1", customer_id="c1", message="hello", order_id=None)
    a = repo.create_ticket(tenant_id="tenant-a", **kw)
    b = repo.create_ticket(tenant_id="tenant-b", **kw)
    assert a.ticket_id != b.ticket_id and not a.replayed and not b.replayed
    assert repo.get_ticket(tenant_id="tenant-a", ticket_id=b.ticket_id) is None
    assert repo.get_ticket(tenant_id="tenant-b", ticket_id=a.ticket_id) is None
    again = repo.create_ticket(tenant_id="tenant-a", **kw)
    assert again.replayed and again.ticket_id == a.ticket_id
    with pytest.raises(IdempotencyConflict):
        repo.create_ticket(tenant_id="tenant-a", **{**kw, "message": "different"})
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == 2


def test_i5_tenant_comes_from_the_authenticated_credential_into_the_repository(pool):
    """Credential -> TenantContext -> repository: the tenant cannot be chosen by the caller."""
    repo = PgTicketRepository(pool)
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.get("/it/mk")
    def mk(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"ticket_id": repo.create_ticket(tenant_id=ctx.tenant_id, idempotency_key="key-0001",
                                                 customer_id="c", message="m", order_id=None).ticket_id}

    app = create_app(SETTINGS, credentials=PostgresCredentialRepository(pool), v1_routers=[router])
    ka, _ = issue(pool, tenant="tenant-a")
    kb, _ = issue(pool, tenant="tenant-b")
    ra = asyncio.run(call(app, [("GET", "/v1/it/mk?tenant_id=tenant-b", {**bearer(ka), "X-Tenant-Id": "tenant-b"})]))[0]
    rb = one(app, "/v1/it/mk", bearer(kb))
    assert repo.get_ticket(tenant_id="tenant-a", ticket_id=ra.json()["ticket_id"]) is not None
    assert repo.get_ticket(tenant_id="tenant-b", ticket_id=ra.json()["ticket_id"]) is None
    assert repo.get_ticket(tenant_id="tenant-b", ticket_id=rb.json()["ticket_id"]) is not None


def test_concurrent_creation_with_one_key_creates_one_ticket(pool):
    repo = PgTicketRepository(pool)
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            results.append(repo.create_ticket(tenant_id="tenant-a", idempotency_key="race-0001",
                                              customer_id="c", message="m", order_id=None))
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors and len({r.ticket_id for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] == 1


def test_i7_authentication_gate_bounds_load_and_healthz_stays_up(pool):
    inner = PostgresCredentialRepository(pool, touch_min_interval_seconds=0)
    release, entered = threading.Event(), threading.Event()

    class Slow:
        def get_by_prefix(self, prefix):
            entered.set()
            release.wait(10)
            return inner.get_by_prefix(prefix)

        def touch_last_used(self, cid):
            inner.touch_last_used(cid)

    app = create_app(SETTINGS, credentials=Slow(), v1_routers=[make_router()],
                     max_concurrent_authentications=1, auth_queue_wait_seconds=0.05)
    key, _ = issue(pool)

    async def scenario():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.get("/v1/it/whoami", headers=bearer(key)))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            shed = await client.get("/v1/it/whoami", headers=bearer(key))
            health = await client.get("/healthz")
            release.set()
            return await first, shed, health

    ok, shed, health = asyncio.run(scenario())
    assert ok.status_code == 200
    assert shed.status_code == 503 and shed.headers["retry-after"] == "1"
    assert shed.json()["error"]["code"] == "service_unavailable"
    assert health.status_code == 200 and health.json() == {"status": "ok"}


def test_i8_security_errors_never_disclose_credentials_or_internals(pool, app, caplog):
    key, _ = issue(pool)
    tampered = key[:-3] + ("aaa" if not key.endswith("aaa") else "bbb")
    caplog.set_level("DEBUG")
    rs = [one(app, "/v1/it/whoami", bearer(tampered)), one(app, "/v1/it/whoami", bearer("junk")),
          one(app, "/v1/it/whoami"), one(app, "/v1/it/whoami", {"Authorization": "Basic abc"})]
    assert {r.status_code for r in rs} == {401}
    assert len({r.content.split(b"request_id")[0] for r in rs}) == 1
    secret_part = key.split("_")[-1]
    text = " ".join(r.text for r in rs) + caplog.text
    for needle in (tampered, key, secret_part, PEPPER, "Traceback", "psycopg", "api_credentials"):
        assert needle not in text
