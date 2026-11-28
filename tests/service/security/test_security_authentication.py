"""Authentication: credential parsing, verification, uniform 401, fail-closed behavior."""

from __future__ import annotations

import hmac
from datetime import timedelta

import pytest
from conftest import NOW, PEPPER, bearer

from hermes_service.security import credentials as credentials_module
from hermes_service.security.credentials import (
    PepperSet,
    compute_key_hash,
    generate_api_key,
    parse_authorization,
)
from hermes_service.security.errors import Unauthenticated

FIXED_RID = {"X-Request-ID": "fixed-request-id-1"}


def _get(client, headers=None, path="/v1/whoami"):
    return client.get(path, headers={**FIXED_RID, **(headers or {})})


# 1. valid credential ------------------------------------------------------------------------
def test_valid_credential_resolves_tenant_and_scopes(client, env):
    issued = env.issue(tenant_id="tenant-a", credential_id="cred-a1")
    r = _get(client, bearer(issued.key))
    assert r.status_code == 200
    assert r.json() == {
        "tenant_id": "tenant-a",
        "credential_id": "cred-a1",
        "scopes": ["tickets:read", "tickets:write"],
    }
    assert env.repo.touched == ["cred-a1"]


def test_bearer_scheme_is_case_insensitive(client, env):
    issued = env.issue()
    assert _get(client, {"Authorization": f"bEaReR {issued.key}"}).status_code == 200


# 2-8. every failure is one byte-identical 401 --------------------------------------------------
def _failure_cases(env):
    good = env.issue(credential_id="cred-good")
    k_other = generate_api_key()
    wrong_secret = f"sok1.{good.record.key_prefix}.{generate_api_key().secret}"
    unknown_prefix = k_other.render()
    revoked = env.issue(credential_id="cred-rev", revoked_at=NOW - timedelta(seconds=1))
    expired = env.issue(credential_id="cred-exp", expires_at=NOW - timedelta(seconds=1))
    unknown_pepper = env.issue(credential_id="cred-pp", pepper_id="p1")
    env.repo.records[unknown_pepper.record.key_prefix] = _with(unknown_pepper.record, pepper_id="gone")
    return {
        "wrong_secret": {"Authorization": f"Bearer {wrong_secret}"},
        "unknown_prefix": {"Authorization": f"Bearer {unknown_prefix}"},
        "revoked": bearer(revoked.key),
        "expired": bearer(expired.key),
        "unknown_pepper_id": bearer(unknown_pepper.key),
        "missing_header": {},
        "wrong_scheme": {"Authorization": f"Basic {good.key}"},
        "token_without_scheme": {"Authorization": good.key},
        "empty_token": {"Authorization": "Bearer "},
        "bearer_only": {"Authorization": "Bearer"},
        "double_space": {"Authorization": f"Bearer  {good.key}"},
        "trailing_space": {"Authorization": f"Bearer {good.key} "},
        "garbage": {"Authorization": "Bearer not-a-key"},
        "wrong_version": {"Authorization": f"Bearer {good.key.replace('sok1', 'sok2', 1)}"},
        "uppercase_prefix": {"Authorization": f"Bearer sok1.{good.record.key_prefix.upper()}.{'A'*43}"},
        "oversized": {"Authorization": "Bearer " + "A" * 4096},
    }


def _with(record, **kw):
    from dataclasses import replace

    return replace(record, **kw)


def test_all_authentication_failures_are_byte_identical(client, env):
    cases = _failure_cases(env)
    responses = {name: _get(client, h) for name, h in cases.items()}
    for name, r in responses.items():
        assert r.status_code == 401, name
    reference = responses["wrong_secret"]
    for name, r in responses.items():
        assert r.content == reference.content, name
        assert dict(r.headers) == dict(reference.headers), name
    body = reference.json()["error"]
    assert body["code"] == "unauthenticated"
    assert reference.headers["www-authenticate"] == "Bearer"


def test_duplicate_authorization_headers_are_rejected(client, env):
    issued = env.issue()
    r = client.get(
        "/v1/whoami",
        headers=[(b"authorization", f"Bearer {issued.key}".encode())] * 2,  # type: ignore[arg-type]
    )
    assert r.status_code == 401


def test_key_in_query_or_cookie_is_not_accepted(client, env):
    issued = env.issue()
    assert client.get("/v1/whoami", params={"api_key": issued.key}).status_code == 401
    assert client.get("/v1/whoami", cookies={"api_key": issued.key}).status_code == 401
    assert client.get("/v1/whoami", headers={"X-API-Key": issued.key}).status_code == 401


# 4/5. revoked / expired — behavior --------------------------------------------------------------
def test_revocation_is_effective_immediately_without_cache(client, env):
    issued = env.issue()
    assert _get(client, bearer(issued.key)).status_code == 200
    env.repo.records[issued.record.key_prefix] = _with(issued.record, revoked_at=NOW)
    assert _get(client, bearer(issued.key)).status_code == 401


def test_expiry_boundary_and_injected_clock(client, env):
    issued = env.issue(expires_at=NOW + timedelta(seconds=10))
    assert _get(client, bearer(issued.key)).status_code == 200
    env.clock.current = NOW + timedelta(seconds=10)  # now >= expires_at -> expired
    assert _get(client, bearer(issued.key)).status_code == 401


def test_naive_expires_at_fails_closed(client, env):
    issued = env.issue(expires_at=NOW.replace(tzinfo=None) + timedelta(days=1))
    assert _get(client, bearer(issued.key)).status_code == 401


# 9. repository unavailable ----------------------------------------------------------------------
def test_repository_failure_is_503_not_401_not_allow(client, env, security_logs):
    issued = env.issue()
    env.repo.fail_lookup = RuntimeError("postgres://user:hunter2@db/prod SECRET-MARKER-DB")
    r = _get(client, bearer(issued.key))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"
    assert "SECRET-MARKER-DB" not in r.text
    assert "SECRET-MARKER-DB" not in security_logs.text


def test_touch_failure_does_not_change_the_decision(client, env):
    issued = env.issue()
    env.repo.fail_touch = RuntimeError("write failed")
    assert _get(client, bearer(issued.key)).status_code == 200


def test_unconfigured_security_denies(env):
    from fastapi.testclient import TestClient

    from hermes_service.app import create_app
    from hermes_service.config import ServiceSettings
    from hermes_service.security.dependencies import authenticate

    app = create_app(ServiceSettings())
    from fastapi import Depends

    @app.get("/v1/x")
    def x(ctx=Depends(authenticate)):
        return {"leak": True}

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/v1/x", headers=bearer(env.issue().key))
    assert r.status_code != 200 and "leak" not in r.text


# malformed repository data ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "Tenant_A"},
        {"tenant_id": "../x"},
        {"tenant_id": ""},
        {"credential_id": "bad id\n"},
        {"key_hash": b"short"},
        {"key_hash": "not-bytes"},
        {"scopes": 7},
        {"scopes": [["unhashable"]]},
    ],
)
def test_malformed_record_fails_closed(client, env, override):
    issued = env.issue(**override)
    assert _get(client, bearer(issued.key)).status_code == 401


def test_repository_returning_a_different_prefix_is_rejected(client, env):
    a = env.issue(credential_id="cred-a")
    b = env.issue(credential_id="cred-b")
    env.repo.records[a.record.key_prefix] = b.record  # lookup by a's prefix yields b's record
    assert _get(client, bearer(a.key)).status_code == 401
    assert _get(client, bearer(b.key)).status_code == 200


def test_second_pepper_id_is_honoured(client, env):
    issued = env.issue(pepper_id="p2")
    assert _get(client, bearer(issued.key)).status_code == 200


# timing-related (structural, not wall-clock) -----------------------------------------------------
def test_unknown_prefix_still_performs_hmac_and_constant_time_compare(env, monkeypatch):
    calls = {"hmac": 0, "compare": 0}
    real_hash, real_cmp = credentials_module.compute_key_hash, hmac.compare_digest

    def counting_hash(*a, **k):
        calls["hmac"] += 1
        return real_hash(*a, **k)

    def counting_cmp(a, b):
        calls["compare"] += 1
        return real_cmp(a, b)

    monkeypatch.setattr(credentials_module, "compute_key_hash", counting_hash)
    monkeypatch.setattr(credentials_module.hmac, "compare_digest", counting_cmp)

    good = env.issue()
    wrong = f"sok1.{good.record.key_prefix}.{generate_api_key().secret}"
    unknown = generate_api_key().render()

    for key in (wrong, unknown):
        calls.update(hmac=0, compare=0)
        with pytest.raises(Unauthenticated):
            env.verifier.authenticate([f"Bearer {key}"])
        assert calls == {"hmac": 1, "compare": 1}


# unit-level helpers -----------------------------------------------------------------------------
def test_generated_keys_match_the_contract_format():
    seen = set()
    for _ in range(50):
        k = generate_api_key()
        assert len(k.prefix) == 12 and len(k.secret) == 43
        parse_authorization([f"Bearer {k.render()}"])
        seen.add(k.prefix)
    assert len(seen) == 50


def test_hash_is_hmac_sha256_and_depends_on_pepper_prefix_and_secret():
    h = compute_key_hash(PEPPER, "aaaaaaaaaaaa", "s" * 43)
    assert len(h) == 32
    assert h != compute_key_hash(PEPPER + b"x", "aaaaaaaaaaaa", "s" * 43)
    assert h != compute_key_hash(PEPPER, "aaaaaaaaaaab", "s" * 43)
    assert h != compute_key_hash(PEPPER, "aaaaaaaaaaaa", "t" * 43)


def test_weak_pepper_rejected_at_construction():
    with pytest.raises(ValueError):
        PepperSet({"p1": b"short"})
    with pytest.raises(ValueError):
        PepperSet({})
    with pytest.raises(ValueError):
        PepperSet({"BAD ID": PEPPER})


# review follow-ups ----------------------------------------------------------------------------
def test_odd_datetimes_deny_instead_of_500(client, env):
    from datetime import datetime, timedelta, tzinfo

    class NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

    odd = env.issue(expires_at=datetime(2030, 1, 1, tzinfo=NoOffset()))
    assert _get(client, bearer(odd.key)).status_code == 401
    ok = env.issue(credential_id="c2", expires_at=NOW + timedelta(days=1))
    env.clock.current = NOW.replace(tzinfo=None)  # naive clock
    assert _get(client, bearer(ok.key)).status_code == 401


def test_repository_exception_is_not_chained(env):
    from hermes_service.security.errors import AuthenticationUnavailable

    env.repo.fail_lookup = RuntimeError("postgres://u:pw@db SECRET-DSN")
    with pytest.raises(AuthenticationUnavailable) as info:
        env.verifier.authenticate([f"Bearer {generate_api_key().render()}"])
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_shared_scope_state_cannot_authenticate_later_requests(app, env):
    """Servers sharing scope['state'] across requests must not leak a verified context."""
    from fastapi.testclient import TestClient

    shared = {}

    class SharedState:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["state"] = shared
            await self.inner(scope, receive, send)

    issued = env.issue(scopes=frozenset({"tickets:write"}))
    wrapped = SharedState(app)
    with TestClient(wrapped, raise_server_exceptions=False) as c:
        assert c.post("/v1/secured-write-probe", json={"message": "x"}, headers=bearer(issued.key)).status_code == 200
        assert c.get("/v1/whoami").status_code == 401
