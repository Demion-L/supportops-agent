"""Scopes, tenant isolation, spoofing, check ordering."""

from __future__ import annotations

import pytest
from conftest import bearer

from supportops_service.security.contracts import Scope
from supportops_service.security.tenant_context import build_tenant_context, filter_scopes

RID = {"X-Request-ID": "fixed-request-id-2"}


# 6. insufficient scope -------------------------------------------------------------------------
def test_missing_scope_is_403_with_fixed_body_and_no_scope_listing(client, env):
    issued = env.issue(scopes=frozenset({"tickets:read"}))
    r = client.post("/v1/write-probe", json={"message": "hi"}, headers={**bearer(issued.key), **RID})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_scope"
    for needle in ("tickets", "trace", "scope:", "read", "write"):
        assert needle not in r.json()["error"]["message"].lower()
    assert "tickets:" not in r.text


def test_403_is_identical_for_existing_and_nonexistent_objects(client, env):
    issued = env.issue(scopes=frozenset({"tickets:write"}))  # lacks tickets:read
    a = client.get("/v1/read-probe/tkt_exists", headers={**bearer(issued.key), **RID})
    b = client.get("/v1/read-probe/tkt_missing_zzz", headers={**bearer(issued.key), **RID})
    assert a.status_code == b.status_code == 403
    assert a.content == b.content


def test_empty_scope_set_authenticates_but_can_do_nothing(client, env):
    issued = env.issue(scopes=frozenset())
    assert client.get("/v1/whoami", headers=bearer(issued.key)).status_code == 200
    assert client.get("/v1/read-probe/x", headers=bearer(issued.key)).status_code == 403


def test_unknown_scope_strings_grant_nothing(client, env):
    issued = env.issue(scopes=frozenset({"admin", "tickets:*", "TICKETS:READ", "trace:write", "*"}))
    r = client.get("/v1/whoami", headers=bearer(issued.key))
    assert r.status_code == 200 and r.json()["scopes"] == []
    assert client.get("/v1/read-probe/x", headers=bearer(issued.key)).status_code == 403


def test_trace_scope_is_separate(client, env):
    issued = env.issue(scopes=frozenset({"tickets:read", "tickets:write"}))
    assert client.get("/v1/trace-probe", headers=bearer(issued.key)).status_code == 403
    traced = env.issue(credential_id="cred-t", scopes=frozenset({"trace:read"}))
    assert client.get("/v1/trace-probe", headers=bearer(traced.key)).status_code == 200


def test_require_scope_rejects_non_scope_at_wiring_time():
    from supportops_service.security.dependencies import require_scope

    with pytest.raises(ValueError):
        require_scope("tickets:everything")  # type: ignore[arg-type]


# 11. tenant cannot be spoofed over HTTP --------------------------------------------------------------
def test_tenant_from_credential_ignores_headers_query_and_cookies(client, env):
    issued = env.issue(tenant_id="tenant-a")
    env.issue(tenant_id="tenant-b", credential_id="cred-b1")
    r = client.get(
        "/v1/whoami?tenant_id=tenant-b&tenant=tenant-b",
        headers={
            **bearer(issued.key),
            "X-Tenant-Id": "tenant-b",
            "X-Tenant": "tenant-b",
            "X-Forwarded-Tenant": "tenant-b",
            "Tenant-Id": "tenant-b",
        },
        cookies={"tenant_id": "tenant-b"},
    )
    assert r.status_code == 200 and r.json()["tenant_id"] == "tenant-a"


def test_tenant_in_body_is_rejected_and_never_reaches_handler(client, env):
    issued = env.issue(tenant_id="tenant-a")
    r = client.post(
        "/v1/write-probe",
        json={"message": "hi", "tenant_id": "tenant-b"},
        headers=bearer(issued.key),
    )
    assert r.status_code == 422
    assert "tenant-b" not in r.text


def test_tenant_isolation_between_two_credentials(client, env):
    a = env.issue(tenant_id="tenant-a", credential_id="cred-a")
    b = env.issue(tenant_id="tenant-b", credential_id="cred-b")
    assert client.get("/v1/whoami", headers=bearer(a.key)).json()["tenant_id"] == "tenant-a"
    assert client.get("/v1/whoami", headers=bearer(b.key)).json()["tenant_id"] == "tenant-b"


# 12. no scope escalation via request parameters ---------------------------------------------------
def test_scopes_cannot_be_raised_via_request_parameters(client, env):
    issued = env.issue(scopes=frozenset({"tickets:read"}))
    h = {
        **bearer(issued.key),
        "X-Scopes": "tickets:write,trace:read",
        "X-Scope": "tickets:write",
        "Scope": "tickets:write",
    }
    r = client.post("/v1/write-probe?scopes=tickets:write&scope=tickets:write", json={"message": "x"},
                    headers=h, cookies={"scopes": "tickets:write"})
    assert r.status_code == 403
    assert client.get("/v1/whoami?scopes=trace:read", headers=h).json()["scopes"] == ["tickets:read"]


# 13. ordering: authentication -> scope -> body validation ---------------------------------------------
SECURED = "/v1/secured-write-probe"  # route registered with SecuredRoute


def test_invalid_body_without_key_is_401_not_422(client):
    assert client.post(SECURED, json={"wrong": 1}).status_code == 401


def test_invalid_body_without_scope_is_403_not_422(client, env):
    issued = env.issue(scopes=frozenset({"tickets:read"}))
    assert client.post(SECURED, json={"wrong": 1}, headers=bearer(issued.key)).status_code == 403


def test_malformed_json_without_key_is_401_not_a_parse_error(client):
    r = client.post(SECURED, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_malformed_json_without_scope_is_403(client, env):
    issued = env.issue(scopes=frozenset({"tickets:read"}))
    r = client.post(
        SECURED,
        content=b"{not json",
        headers={"Content-Type": "application/json", **bearer(issued.key)},
    )
    assert r.status_code == 403


def test_secured_route_happy_path_authenticates_exactly_once(client, env):
    issued = env.issue(scopes=frozenset({"tickets:write"}))
    r = client.post(SECURED, json={"message": "hi"}, headers=bearer(issued.key))
    assert r.status_code == 200 and r.json() == {"tenant_id": "tenant-a", "echo": "hi"}
    assert len(env.repo.lookups) == 1 and env.repo.touched == ["cred-a1"]


def test_secured_route_validation_still_applies_after_auth(client, env):
    issued = env.issue(scopes=frozenset({"tickets:write"}))
    r = client.post(SECURED, json={"message": "x", "tenant_id": "tenant-b"}, headers=bearer(issued.key))
    assert r.status_code == 422 and "tenant-b" not in r.text


def test_client_cannot_inject_a_verified_context(client, env):
    """The cached-context shortcut is server-side only; client input cannot populate it."""
    issued = env.issue(tenant_id="tenant-a", scopes=frozenset({"tickets:read"}))
    r = client.get(
        "/v1/whoami",
        headers={**bearer(issued.key), "supportops.verified_tenant_context": "tenant-b"},
    )
    assert r.json()["tenant_id"] == "tenant-a"


def test_unsecured_route_keeps_safe_behavior_with_documented_error_ordering(client):
    """Without SecuredRoute FastAPI parses the body first; the decision stays fail-closed."""
    r = client.post("/v1/write-probe", json={"wrong": 1})
    assert r.status_code in (401, 422)  # 401 once the route uses SecuredRoute


# tenant context factory ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "A", "-a", "a_b", "a" * 64, "a b", "a\n", None, 5, "é"])
def test_invalid_tenant_ids_do_not_build_a_context(bad):
    from supportops_service.security.tenant_context import TenantContextError

    with pytest.raises(TenantContextError):
        build_tenant_context(tenant_id=bad, credential_id="cred-1", raw_scopes=[])


def test_filter_scopes_is_closed_set():
    assert filter_scopes(["tickets:read", "x", 3, None]) == frozenset({Scope.TICKETS_READ})
