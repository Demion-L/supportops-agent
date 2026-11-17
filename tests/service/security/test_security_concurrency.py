"""Cross-request isolation under concurrency: no identity, scope or context bleeds between requests."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from conftest import bearer

REQUESTS_PER_TENANT = 60


async def _fire(app, calls):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.gather(*(client.request(*c[:2], **c[2]) for c in calls))


def test_interleaved_requests_never_cross_tenants_or_scopes(app, env):
    a = env.issue(tenant_id="tenant-a", credential_id="cred-a", scopes=frozenset({"tickets:read"}))
    b = env.issue(tenant_id="tenant-b", credential_id="cred-b", scopes=frozenset({"tickets:write"}))
    calls = []
    for i in range(REQUESTS_PER_TENANT):
        calls.append(("GET", "/v1/whoami", {"headers": bearer(a.key)}))
        calls.append(("GET", "/v1/whoami", {"headers": bearer(b.key)}))
        calls.append(("GET", f"/v1/read-probe/{i}", {"headers": bearer(b.key)}))  # b lacks read
        calls.append(("GET", "/v1/whoami", {}))  # anonymous in the middle of authenticated traffic
        calls.append(("POST", "/v1/secured-write-probe", {"headers": bearer(a.key), "content": b"{"}))
    responses = asyncio.run(_fire(app, calls))
    for (method, path, _), response in zip(calls, responses, strict=True):
        body = response.json()
        if path == "/v1/whoami" and response.status_code == 200:
            expected = "tenant-a" if body["credential_id"] == "cred-a" else "tenant-b"
            assert body["tenant_id"] == expected
            assert body["scopes"] == (["tickets:read"] if expected == "tenant-a" else ["tickets:write"])
        elif path.startswith("/v1/read-probe/"):
            assert response.status_code == 403
        elif path == "/v1/secured-write-probe":
            assert response.status_code == 403  # a has no write scope, decided before body parsing
        else:
            assert response.status_code == 401
            assert body["error"]["code"] == "unauthenticated"
    ok = [r for (_, p, _), r in zip(calls, responses, strict=True) if p == "/v1/whoami" and r.status_code == 200]
    assert len(ok) == 2 * REQUESTS_PER_TENANT


def test_revocation_applies_to_requests_started_after_it_under_load(app, env):
    issued = env.issue()
    first = asyncio.run(_fire(app, [("GET", "/v1/whoami", {"headers": bearer(issued.key)})] * 10))
    assert {r.status_code for r in first} == {200}
    from dataclasses import replace

    from conftest import NOW

    env.repo.add(replace(issued.record, revoked_at=NOW))
    after = asyncio.run(_fire(app, [("GET", "/v1/whoami", {"headers": bearer(issued.key)})] * 10))
    assert {r.status_code for r in after} == {401}


def test_verifier_is_safe_to_call_from_many_threads(env):
    issued = [env.issue(tenant_id=f"tenant-{i}", credential_id=f"cred-{i}") for i in range(4)]
    errors: list[str] = []
    barrier = threading.Barrier(16)

    def worker(n: int) -> None:
        barrier.wait()
        for _ in range(100):
            item = issued[n % 4]
            ctx = env.verifier.authenticate([f"Bearer {item.key}"])
            if ctx.tenant_id != f"tenant-{n % 4}" or ctx.credential_id != f"cred-{n % 4}":
                errors.append("identity mixed up")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and time.monotonic() - started < 30
    assert not any(t.is_alive() for t in threads)


@pytest.mark.parametrize("n", [1])
def test_concurrent_repository_failure_is_503_for_all_never_a_mixed_result(app, env, n):
    issued = env.issue()
    env.repo.fail_lookup = RuntimeError("down")
    responses = asyncio.run(_fire(app, [("GET", "/v1/whoami", {"headers": bearer(issued.key)})] * 40))
    assert {r.status_code for r in responses} == {503}
