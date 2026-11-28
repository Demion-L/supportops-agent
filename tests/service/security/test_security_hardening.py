"""Alpha security hardening: F1 (auth resource exhaustion), F2 (touch_last_used), F4 (scope declaration)."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
from conftest import NOW, FixedClock, bearer
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from hermes_service.app import create_app
from hermes_service.config import ServiceSettings
from hermes_service.interfaces import Scope, TenantContext
from hermes_service.repositories.credentials import PostgresCredentialRepository
from hermes_service.security.credentials import (
    CredentialVerifier,
    PepperSet,
    generate_api_key,
)
from hermes_service.security.dependencies import (
    AuthenticationGate,
    SecuredRoute,
    install_security,
    require_scope,
)
from hermes_service.security.route_audit import find_scopes_outside_route_signature

# ---------------------------------------------------------------- F1: resource exhaustion


class SlowCountingRepository:
    """Stands in for a pool-bound PostgreSQL lookup: slow, and records peak concurrency."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = 0

    def get_by_prefix(self, key_prefix: str):
        with self._lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            return None
        finally:
            with self._lock:
                self.active -= 1

    def touch_last_used(self, credential_id: str) -> None:  # pragma: no cover - never reached
        raise AssertionError


def _flood_app(repo, *, limit: int, wait: float) -> FastAPI:
    from conftest import PEPPER

    app = create_app(ServiceSettings())
    install_security(
        app,
        CredentialVerifier(repo, PepperSet({"p1": PEPPER}), FixedClock(NOW)),
        max_concurrent_authentications=limit,
        auth_queue_wait_seconds=wait,
    )
    router = APIRouter(route_class=SecuredRoute)

    @router.get("/v1/read")
    def read(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
        return {"ok": True}

    app.include_router(router)

    @app.get("/healthz-sync")
    def health() -> dict:  # a sync route that needs a threadpool thread
        return {"ok": True}

    return app


async def _flood(app: FastAPI, n: int):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:

        async def one():
            return await client.get("/v1/read", headers=bearer(generate_api_key().render()))

        health_started = time.monotonic()
        results = await asyncio.gather(*(one() for _ in range(n)), _health(client))
        return results[:-1], results[-1], health_started


async def _health(client):
    await asyncio.sleep(0.05)  # arrives in the middle of the flood
    t = time.monotonic()
    r = await client.get("/healthz-sync")
    return r.status_code, time.monotonic() - t


def test_flood_of_plausible_unknown_keys_is_bounded_and_fails_closed():
    repo = SlowCountingRepository(delay=0.05)
    limit = 4
    app = _flood_app(repo, limit=limit, wait=0.05)
    responses, (health_status, health_latency), _ = asyncio.run(_flood(app, 300))

    assert repo.peak <= limit, f"store saw {repo.peak} concurrent lookups, cap is {limit}"
    codes = {r.status_code for r in responses}
    assert codes <= {401, 503}, codes  # never 200: saturation must not admit anyone
    assert 503 in codes, "the flood must have saturated the gate (test would prove nothing)"
    for r in responses:
        body = r.json()
        assert body["error"]["code"] in {"unauthenticated", "service_unavailable"}
        assert "overload" not in r.text.lower() and "pool" not in r.text.lower()
        if r.status_code == 401:
            assert r.headers["www-authenticate"] == "Bearer"
    # The flood must not starve unrelated sync routes of threadpool threads.
    assert health_status == 200 and health_latency < 1.0, health_latency


def test_valid_key_still_authenticates_right_after_the_flood(env):
    from conftest import PEPPER

    issued = env.issue()
    repo = env.repo
    app = create_app(ServiceSettings())
    install_security(
        app,
        CredentialVerifier(repo, PepperSet({"p1": PEPPER}), FixedClock(NOW)),
        max_concurrent_authentications=2,
        auth_queue_wait_seconds=0.0,
    )
    router = APIRouter(route_class=SecuredRoute)

    @router.get("/v1/read")
    def read(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
        return {"tenant_id": ctx.tenant_id}

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/v1/read", headers=bearer(issued.key)).json() == {"tenant_id": "tenant-a"}


def test_gate_releases_its_slot_on_denial_and_on_exception():
    class Boom:
        def authenticate(self, values):
            raise RuntimeError("secret-dsn")

    async def run():
        gate = AuthenticationGate(max_concurrent=1, queue_wait_seconds=0.2)
        for _ in range(5):  # a leaked slot would turn the 2nd call into a 503
            with pytest.raises(RuntimeError):
                await gate.authenticate(Boom(), ["x"])  # type: ignore[arg-type]

    asyncio.run(run())


def test_gate_rejects_nonsense_limits():
    with pytest.raises(ValueError):
        AuthenticationGate(max_concurrent=0)
    with pytest.raises(ValueError):
        AuthenticationGate(max_concurrent=1, queue_wait_seconds=-1)


def test_missing_gate_denies_instead_of_skipping(env):
    app = create_app(ServiceSettings())
    install_security(app, env.verifier)
    del app.state.authentication_gate  # simulate broken wiring
    router = APIRouter(route_class=SecuredRoute)

    @router.get("/v1/read")
    def read(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
        return {}

    app.include_router(router)
    issued = env.issue()
    r = TestClient(app, raise_server_exceptions=False).get("/v1/read", headers=bearer(issued.key))
    assert r.status_code == 503


# ------------------------------------------------------------------- F2: touch_last_used


class RecordingPool:
    """Counts connection checkouts, their nesting and their release, even on failure."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.open = 0
        self.max_open = 0
        self.entered = 0
        self.exited = 0
        self.rows: dict[str, object] = {}
        self.executed: list[str] = []

    @contextmanager
    def connection(self):
        self.open += 1
        self.entered += 1
        self.max_open = max(self.max_open, self.open)
        try:
            yield self
        finally:
            self.open -= 1
            self.exited += 1

    def execute(self, sql, params=()):
        self.executed.append(sql.split()[0])
        if self.fail_on and sql.startswith(self.fail_on):
            raise ConnectionError("postgresql://user:pw@host/db")
        return self

    def fetchone(self):
        return None


def test_touch_failure_releases_the_connection_and_never_nests_checkouts():
    pool = RecordingPool(fail_on="UPDATE")
    repo = PostgresCredentialRepository(pool)
    for _ in range(20):
        with pytest.raises(Exception):
            repo.touch_last_used("00000000-0000-0000-0000-000000000001")
    assert pool.entered == pool.exited == 20 and pool.open == 0 and pool.max_open == 1


def test_lookup_failure_releases_the_connection():
    pool = RecordingPool(fail_on="SELECT")
    repo = PostgresCredentialRepository(pool)
    for _ in range(20):
        with pytest.raises(Exception):
            repo.get_by_prefix("aaaaaaaaaaaa")
    assert pool.entered == pool.exited == 20 and pool.open == 0


def test_touch_never_changes_the_authentication_result(env):
    env.repo.fail_touch = RuntimeError("boom")
    issued = env.issue()
    assert env.verifier.authenticate([f"Bearer {issued.key}"]).tenant_id == "tenant-a"


def test_touch_is_rate_limited_in_sql_and_only_after_success():
    from hermes_service.repositories import credentials as mod

    sql = mod._TOUCH_LAST_USED
    assert "last_used_at < now() - make_interval" in sql and "last_used_at IS NULL" in sql
    assert mod.DEFAULT_TOUCH_MIN_INTERVAL_SECONDS >= 30


# ----------------------------------------------------------- F4: scope declaration


def _app_with(register) -> FastAPI:
    app = FastAPI()
    register(app)
    return app


def test_audit_flags_scopes_declared_via_include_router_dependencies():
    def register(app):
        router = APIRouter(prefix="/v1", route_class=SecuredRoute)

        @router.get("/x")
        def x() -> dict:
            return {}

        app.include_router(router, dependencies=[Depends(require_scope(Scope.TICKETS_READ))])

    assert find_scopes_outside_route_signature(_app_with(register)) == ["GET /v1/x"]


def test_audit_flags_scopes_declared_via_router_and_decorator_dependencies():
    def register(app):
        router = APIRouter(
            prefix="/v1", route_class=SecuredRoute, dependencies=[Depends(require_scope(Scope.TICKETS_READ))]
        )

        @router.get("/y")
        def y() -> dict:
            return {}

        @router.post("/z", dependencies=[Depends(require_scope(Scope.TICKETS_WRITE))])
        def z() -> dict:
            return {}

        app.include_router(router)

    assert find_scopes_outside_route_signature(_app_with(register)) == ["GET /v1/y", "POST /v1/z"]


def test_audit_accepts_scopes_in_the_endpoint_signature_and_ignores_unprotected_paths():
    def register(app):
        router = APIRouter(prefix="/v1", route_class=SecuredRoute)

        @router.get("/ok")
        def ok(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
            return {}

        app.include_router(router)
        app.include_router(
            APIRouter(dependencies=[Depends(require_scope(Scope.TICKETS_READ))]),  # empty, harmless
        )

        @app.get("/healthz", dependencies=[Depends(require_scope(Scope.TICKETS_READ))])
        def health() -> dict:  # outside /v1: not this audit's concern
            return {}

    assert find_scopes_outside_route_signature(_app_with(register)) == []


def test_real_application_uses_no_router_level_scope_dependencies(app):
    """Applied to the fixture application that mirrors Beta's create_app wiring."""
    assert find_scopes_outside_route_signature(app) == []
