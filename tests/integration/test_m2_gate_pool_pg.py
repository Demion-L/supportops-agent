"""Review M2 on real PostgreSQL: gate limit 3 against a pool of exactly 4 connections.

Auth flood (well-formed, unknown keys) runs while tickets are created through the repository on
the SAME pool. The gate keeps one connection free, so ticket creation keeps working. This is a
concurrency cap, not DoS protection (no rate limit exists in the MVP).
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from test_alpha_beta_integration_pg import PEPPER, SETTINGS, bearer, issue, make_router

from supportops_service.app import create_app
from supportops_service.config import ServiceConfigurationError
from supportops_service.repositories.credentials import PostgresCredentialRepository
from supportops_service.repositories.tickets import PgTicketRepository
from supportops_service.security.credentials import generate_api_key

pytestmark = pytest.mark.postgres


@pytest.fixture
def pool4(make_pool, pg):
    p = make_pool(max_size=4, min_size=1, acquire_timeout=10.0)
    pg.migrate(p)
    return p


def test_default_gate_with_real_pool_of_four_is_valid_and_verified(pool4):
    app = create_app(SETTINGS, credentials=PostgresCredentialRepository(pool4), v1_routers=[make_router()])
    gate = app.state.authentication_gate
    assert gate._max == 3 and gate.pool_isolation_verified is True


def test_limit_equal_to_real_pool_size_is_rejected_at_startup(pool4):
    with pytest.raises(ServiceConfigurationError):
        create_app(SETTINGS, credentials=PostgresCredentialRepository(pool4),
                   v1_routers=[make_router()], max_concurrent_authentications=4)


async def _flood(app, n, healthz_delay=0.05):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        async def hit():
            return await client.get("/v1/it/whoami", headers=bearer(generate_api_key().render()))

        async def health():
            await asyncio.sleep(healthz_delay)
            return await client.get("/healthz")

        *responses, healthz = await asyncio.gather(*(hit() for _ in range(n)), health())
        return responses, healthz


def test_auth_flood_does_not_starve_ticket_creation_on_the_shared_pool(pool4):
    repo = PgTicketRepository(pool4)
    app = create_app(SETTINGS, credentials=PostgresCredentialRepository(pool4), v1_routers=[make_router()],
                     auth_queue_wait_seconds=0.2)

    created, errors = [], []
    start = threading.Barrier(9)  # 8 creators + the flood thread

    def creator(i: int) -> None:
        start.wait()
        try:
            for j in range(5):
                created.append(repo.create_ticket(tenant_id="tenant-a", idempotency_key=f"m2-{i}-{j:04d}",
                                                  customer_id="c", message="m", order_id=None).ticket_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    out: dict = {}

    def flooder() -> None:
        start.wait()
        out["responses"], out["healthz"] = asyncio.run(_flood(app, 150))

    threads = [threading.Thread(target=creator, args=(i,)) for i in range(8)] + [threading.Thread(target=flooder)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]

    assert not errors, errors  # ticket creation was never starved of connections
    assert len(set(created)) == 40
    with pool4.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] == 40

    codes = [r.status_code for r in out["responses"]]
    assert len(codes) == 150 and set(codes) <= {401, 503}  # no 500, no 200 (no gate bypass)
    assert all(r.headers["retry-after"] == "1" for r in out["responses"] if r.status_code == 503)
    assert out["healthz"].status_code == 200
    text = " ".join(r.text for r in out["responses"])
    assert PEPPER not in text and "postgres" not in text.lower() and "psycopg" not in text


def test_valid_key_works_after_the_flood_and_loops_can_change(pool4):
    key, _ = issue(pool4)
    app = create_app(SETTINGS, credentials=PostgresCredentialRepository(pool4, touch_min_interval_seconds=0),
                     v1_routers=[make_router()], auth_queue_wait_seconds=0.05)
    for _ in range(2):  # two distinct event loops
        responses, healthz = asyncio.run(_flood(app, 60))
        assert {r.status_code for r in responses} <= {401, 503} and healthz.status_code == 200

        async def ok():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.get("/v1/it/whoami", headers=bearer(key))

        assert asyncio.run(ok()).status_code == 200
