"""Review M2 (owner decision): authentication concurrency limit 3 against a PostgreSQL pool of 4.

The gate is a *concurrency cap*, not a rate limiter and not DoS protection. These tests pin the
configuration contract (default, validation, ``limit < pool.max_size``, honest "unknown pool"
behaviour) and the overload behaviour (503 + Retry-After, never 500, never unauthenticated access,
``/healthz`` unaffected).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import httpx
import pytest
from fastapi import APIRouter, Depends

from supportops_service.app import create_app
from supportops_service.config import ServiceConfigurationError, ServiceSettings
from supportops_service.interfaces import Scope, TenantContext
from supportops_service.repositories.credentials import PostgresCredentialRepository
from supportops_service.security.credentials import generate_api_key
from supportops_service.security.dependencies import (
    DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS,
    AuthenticationGate,
    SecuredRoute,
    require_scope,
)

PEPPER = "m2-unit-pepper-0123456789abcdefghij"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


SETTINGS = ServiceSettings(pepper=PEPPER, pepper_id="p1")  # type: ignore[arg-type]


class SlowUnknownRepository:
    """Every lookup is slow and finds nothing (an unknown but well-formed key)."""

    def __init__(self, delay: float = 0.05, pool_max_size: int | None = None) -> None:
        self.delay = delay
        self.pool_max_size = pool_max_size  # mimics PostgresCredentialRepository.pool_max_size
        self._lock = threading.Lock()
        self.active = self.peak = 0

    def get_by_prefix(self, key_prefix: str):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            return None
        finally:
            with self._lock:
                self.active -= 1

    def touch_last_used(self, credential_id: str) -> None:  # pragma: no cover
        raise AssertionError("an unknown key must never reach touch_last_used")


def _router() -> APIRouter:
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.get("/read")
    def read(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
        return {"tenant_id": ctx.tenant_id}

    return router


def _app(repo, **kwargs):
    return create_app(SETTINGS, credentials=repo, v1_routers=[_router()], **kwargs)


# ------------------------------------------------------------------ configuration contract


def test_default_limit_is_three_and_below_the_default_pool_of_four():
    from supportops_service.db.pool import DatabaseSettings

    assert DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS == 3
    assert DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS < DatabaseSettings.__dataclass_fields__["max_size"].default


def test_limit_is_configurable():
    app = _app(SlowUnknownRepository(), max_concurrent_authentications=5)
    assert app.state.authentication_gate._max == 5


@pytest.mark.parametrize("limit,pool", [(4, 4), (5, 4), (3, 3), (1, 1)])
def test_limit_not_below_known_pool_size_is_rejected_at_startup(limit, pool):
    with pytest.raises(ServiceConfigurationError) as info:
        _app(SlowUnknownRepository(pool_max_size=pool), max_concurrent_authentications=limit)
    assert str(info.value) == "Invalid authentication gate configuration."


def test_explicit_pool_size_is_validated_too():
    with pytest.raises(ServiceConfigurationError):
        _app(SlowUnknownRepository(), max_concurrent_authentications=4, pool_max_size=4)
    assert _app(SlowUnknownRepository(), max_concurrent_authentications=3, pool_max_size=4)


def test_default_limit_is_rejected_with_a_pool_of_three():
    with pytest.raises(ServiceConfigurationError):
        _app(SlowUnknownRepository(), pool_max_size=3)  # default 3 is not < 3


@pytest.mark.parametrize("bad", [0, -1, True])
def test_nonsense_limits_are_rejected_at_startup(bad):
    with pytest.raises(ServiceConfigurationError):
        _app(SlowUnknownRepository(), max_concurrent_authentications=bad)


def test_known_pool_size_marks_isolation_as_verified():
    app = _app(SlowUnknownRepository(pool_max_size=4))
    assert app.state.authentication_gate.pool_isolation_verified is True


def test_unknown_pool_size_does_not_claim_isolation_and_warns(caplog):
    caplog.set_level(logging.WARNING)
    app = _app(SlowUnknownRepository(pool_max_size=None))
    assert app.state.authentication_gate.pool_isolation_verified is False
    assert "isolation not verified" in caplog.text


def test_pool_size_of_a_postgres_repository_comes_from_its_pool():
    class FakePool:
        max_size = 4

        def connection(self):  # pragma: no cover
            raise AssertionError

    class NoSize:
        def connection(self):  # pragma: no cover
            raise AssertionError

    assert PostgresCredentialRepository(FakePool()).pool_max_size == 4
    assert PostgresCredentialRepository(NoSize()).pool_max_size is None


def test_gate_rejects_limit_equal_to_pool_directly():
    with pytest.raises(ValueError):
        AuthenticationGate(max_concurrent=4, pool_max_size=4)
    assert AuthenticationGate(max_concurrent=3, pool_max_size=4).pool_isolation_verified


def test_configuration_errors_do_not_leak_values():
    with pytest.raises(ServiceConfigurationError) as info:
        _app(SlowUnknownRepository(pool_max_size=4), max_concurrent_authentications=4)
    assert PEPPER not in str(info.value) and "4" not in str(info.value)


# ------------------------------------------------------------------ overload behaviour


async def _flood(app, n: int):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        async def hit():
            return await client.get("/v1/read", headers=bearer(generate_api_key().render()))

        async def health():
            await asyncio.sleep(0.02)
            return await client.get("/healthz")

        *responses, healthz = await asyncio.gather(*(hit() for _ in range(n)), health())
        return responses, healthz


def test_150_concurrent_unknown_keys_never_500_never_authenticate_and_healthz_stays_up():
    repo = SlowUnknownRepository(delay=0.05, pool_max_size=4)
    app = _app(repo, auth_queue_wait_seconds=0.05)
    responses, healthz = asyncio.run(_flood(app, 150))

    codes = [r.status_code for r in responses]
    assert len(codes) == 150
    assert set(codes) <= {401, 503}, set(codes)  # no 200 (no bypass), no 500 (no crash)
    assert 503 in codes and 401 in codes  # the gate really saturated, and still served some
    assert repo.peak <= 3  # never more than the limit at the repository (= pool connections)
    for r in responses:
        body = r.json()
        if r.status_code == 503:
            assert r.headers["retry-after"] == "1"
            assert body["error"]["code"] == "service_unavailable"
        else:
            assert r.headers["www-authenticate"] == "Bearer"
            assert body["error"]["code"] == "unauthenticated"
    assert healthz.status_code == 200 and healthz.json() == {"status": "ok"}


def test_overload_responses_contain_no_credential_material():
    app = _app(SlowUnknownRepository(delay=0.05, pool_max_size=4), auth_queue_wait_seconds=0.0)
    keys = [generate_api_key() for _ in range(40)]

    async def run():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await asyncio.gather(
                *(client.get("/v1/read", headers=bearer(k.render())) for k in keys)
            )

    text = " ".join(r.text + str(dict(r.headers)) for r in asyncio.run(run()))
    for k in keys:
        assert k.secret not in text and k.prefix not in text and k.render() not in text
    assert PEPPER not in text


def test_saturation_does_not_open_a_bypass_for_protected_routes():
    """While the gate is full a request is refused (503); it is never served unauthenticated."""
    app = _app(SlowUnknownRepository(delay=0.2, pool_max_size=4), auth_queue_wait_seconds=0.0)
    responses, _ = asyncio.run(_flood(app, 30))
    assert all(r.status_code in (401, 503) for r in responses)
    assert not any("tenant_id" in r.text for r in responses)


def test_gate_works_on_two_successive_event_loops_with_default_limit():
    app = _app(SlowUnknownRepository(delay=0.05, pool_max_size=4), auth_queue_wait_seconds=0.01)
    for _ in range(2):  # two distinct event loops, same app and gate
        responses, healthz = asyncio.run(_flood(app, 40))
        assert {r.status_code for r in responses} <= {401, 503}
        assert healthz.status_code == 200
