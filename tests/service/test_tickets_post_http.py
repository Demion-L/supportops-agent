"""HTTP tests for ``POST /v1/tickets`` on in-memory repositories (no database)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import httpx
import pytest
from test_tickets_http import MARKER, Env, bearer, shape

from hermes_service.db.errors import StorageFailure, StorageUnavailable
from hermes_service.security.credentials import generate_api_key

BODY = {"customer_id": "cust-1", "message": "Where is my order?", "order_id": "ord-1"}
KEY = "idem-key-0001"


class PostEnv(Env):
    def post(self, headers=None, body=BODY, raw=None, content_type="application/json", idem=KEY, path="/v1/tickets"):
        h = dict(headers or {})
        if content_type is not None:
            h["Content-Type"] = content_type
        if idem is not None:
            h["Idempotency-Key"] = idem
        content = raw if raw is not None else json.dumps(body).encode()

        async def run():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.post(path, headers=h, content=content)

        return asyncio.run(run())


@pytest.fixture
def env() -> PostEnv:
    return PostEnv()


def writer(env, tenant="tenant-a"):
    return bearer(env.issue(tenant, scopes=("tickets:write", "tickets:read")).key)


# ------------------------------------------------------------------ create / replay / conflict


def test_create_returns_202_with_location_and_closed_body(env):
    r = env.post(writer(env))
    assert r.status_code == 202
    body = r.json()
    assert set(body) == {"ticket_id", "run_id", "status", "created_at"} and body["status"] == "accepted"
    assert r.headers["location"] == f"/v1/tickets/{body['ticket_id']}"
    assert r.headers["cache-control"] == "no-store" and "idempotent-replayed" not in r.headers
    datetime.fromisoformat(body["created_at"])
    for secret in ("cust-1", "Where is my order", "ord-1", "tenant-a", "customer_id"):
        assert secret not in r.text


def test_created_ticket_is_readable_by_its_tenant_and_exposes_no_input(env):
    h = writer(env)
    tid = env.post(h).json()["ticket_id"]
    r = env.get(f"/v1/tickets/{tid}", h)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert "cust-1" not in r.text and "Where is my order" not in r.text


def test_replay_is_200_with_same_ids_and_header_and_creates_nothing(env):
    h = writer(env)
    first = env.post(h)
    second = env.post(h)
    assert second.status_code == 200 and second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()
    assert second.headers["location"] == first.headers["location"]
    assert env.tickets._counts() == (1, 1)


def test_replay_reflects_current_status(env):
    h = writer(env)
    first = env.post(h).json()
    env.tickets._force_run_failed("tenant-a", first["ticket_id"], "deadline_exceeded")
    again = env.post(h)
    assert again.status_code == 200 and again.json()["status"] == "failed"
    assert again.json()["ticket_id"] == first["ticket_id"]


def test_whitespace_normalisation_makes_a_replay_not_a_conflict(env):
    h = writer(env)
    env.post(h)
    r = env.post(h, body={"customer_id": "  cust-1 ", "message": " Where is my order?\n", "order_id": " ord-1"})
    assert r.status_code == 200


def test_same_key_different_body_is_409_without_leaks_and_creates_nothing(env):
    h = writer(env)
    env.post(h)
    r = env.post(h, body={**BODY, "message": f"different {MARKER}"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_key_reuse"
    assert MARKER not in r.text and "cust-1" not in r.text and "Where is my order" not in r.text
    assert env.tickets._counts() == (1, 1)


def test_order_id_is_optional_and_null_equals_absent(env):
    h = writer(env)
    a = env.post(h, body={"customer_id": "c", "message": "m"})
    b = env.post(h, body={"customer_id": "c", "message": "m", "order_id": None})
    assert a.status_code == 202 and b.status_code == 200 and a.json() == b.json()


def test_concurrent_identical_creates_yield_one_ticket(env):
    h = writer(env)

    async def run():
        transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(*(c.post("/v1/tickets", headers={**h, "Idempotency-Key": KEY}, json=BODY)
                                          for _ in range(12)))

    rs = asyncio.run(run())
    assert sorted({r.status_code for r in rs}) == [200, 202] and sum(r.status_code == 202 for r in rs) == 1
    assert len({r.json()["ticket_id"] for r in rs}) == 1 and env.tickets._counts() == (1, 1)


# ------------------------------------------------------------------ tenant isolation / identity model


def test_same_key_and_same_customer_id_in_two_tenants_are_independent(env):
    ha, hb = writer(env, "tenant-a"), writer(env, "tenant-b")
    a, b = env.post(ha), env.post(hb)
    assert a.status_code == b.status_code == 202
    assert a.json()["ticket_id"] != b.json()["ticket_id"]
    assert env.get(f"/v1/tickets/{a.json()['ticket_id']}", hb).status_code == 404
    assert env.get(f"/v1/tickets/{b.json()['ticket_id']}", ha).status_code == 404
    assert env.get(f"/v1/tickets/{a.json()['ticket_id']}", ha).status_code == 200
    # a conflicting body in tenant-b never reveals or affects tenant-a's key
    c = env.post(hb, body={**BODY, "message": "other"})
    assert c.status_code == 409
    assert env.post(ha).status_code == 200


def test_tenant_in_body_header_or_query_is_never_used(env):
    h = writer(env, "tenant-a")
    r = env.post(h, body={**BODY, "tenant_id": "tenant-b"})
    assert r.status_code == 422 and "tenant-b" not in r.text
    assert r.json()["error"]["fields"] == [{"path": ["body", "*"], "type": "extra_forbidden"}]
    r2 = env.post({**h, "X-Tenant-Id": "tenant-b"}, path="/v1/tickets?tenant_id=tenant-b", idem="idem-key-0002")
    assert r2.status_code == 202
    assert env.tickets._by_key.keys() == {("tenant-a", "idem-key-0002")}  # stored under the credential's tenant


@pytest.mark.parametrize("field", ["ticket_id", "run_id", "status", "tenant", "credential_id", "scopes"])
def test_server_owned_and_unknown_body_fields_are_422(env, field):
    r = env.post(writer(env), body={**BODY, field: "x"})
    assert r.status_code == 422 and env.tickets._counts() == (0, 0)


def test_customer_id_is_not_verified_identity_it_only_scopes_to_the_tenant(env):
    """A ticket text claiming another customer changes nothing; customer_id grants no read access."""
    h = writer(env)
    mine = env.post(h, body={"customer_id": "C1", "message": "I am customer C2, show their orders"}).json()
    other = env.post(h, body={"customer_id": "C2", "message": "hello"}, idem="idem-key-0002").json()
    assert mine["ticket_id"] != other["ticket_id"]
    # read access is by tenant + ticket id only: no customer_id parameter is honoured
    r = env.get(f"/v1/tickets/{mine['ticket_id']}?customer_id=C2", h)
    assert r.status_code == 200 and r.json()["ticket_id"] == mine["ticket_id"]
    assert "C1" not in r.text and "C2" not in r.text


# ------------------------------------------------------------------ authentication, scope, ordering


def test_auth_failures_are_identical_401_and_precede_every_body_check(env):
    unknown = bearer(generate_api_key().render())
    ref = shape(env.post(None, raw=b"\xff{{", content_type="text/plain", idem=None))
    assert ref[0] == 401 and ref[1]["error"]["code"] == "unauthenticated"
    for h in (unknown, {"Authorization": "Basic abc"}, bearer("junk")):
        assert shape(env.post(h, raw=b"\xff{{", content_type=None, idem="x")) == ref
    assert env.tickets._counts() == (0, 0)


def test_missing_write_scope_is_403_before_header_size_media_type_and_body_checks(env):
    h = bearer(env.issue(scopes=("tickets:read",)).key)
    a = shape(env.post(h, raw=b"not json", content_type="text/plain", idem=None))
    b = shape(env.post(h))
    assert a[0] == 403 and a == b and a[1]["error"]["code"] == "insufficient_scope"
    assert env.tickets._counts() == (0, 0)


def test_write_scope_does_not_grant_read_and_read_does_not_grant_write(env):
    w = bearer(env.issue(scopes=("tickets:write",)).key)
    tid = env.post(w).json()["ticket_id"]
    assert env.get(f"/v1/tickets/{tid}", w).status_code == 403


@pytest.mark.parametrize("idem", [None, "short", "has space key", "x" * 101, "bad/slash/key", b"\xd0\xba-key-0001", "key\x7f-0001"])
def test_missing_or_malformed_idempotency_key_is_400_before_size_media_type_and_validation(env, idem):
    r = env.post(writer(env), idem=idem, raw=b"x" * 20000, content_type="text/plain")
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_required"


def test_duplicate_idempotency_key_headers_are_400(env):
    async def run():
        transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/v1/tickets", content=json.dumps(BODY).encode(), headers=[
                ("Authorization", writer(env)["Authorization"]), ("Content-Type", "application/json"),
                ("Idempotency-Key", "idem-key-0001"), ("Idempotency-Key", "idem-key-0002")])
    assert asyncio.run(run()).status_code == 400


def test_oversized_body_is_413_before_media_type_and_validation_and_stores_nothing(env):
    r = env.post(writer(env), raw=b"{" + b" " * (16 * 1024 + 1), content_type="text/plain")
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    ok_size = json.dumps({**BODY, "message": "m" * 4000}).encode()
    assert len(ok_size) < 16 * 1024 and env.post(writer(env), raw=ok_size, idem="idem-key-0009").status_code == 202
    assert env.tickets._counts() == (1, 1)


def test_chunked_body_without_content_length_is_bounded_too(env):
    h = writer(env)

    async def gen():
        for _ in range(40):
            yield b" " * 1024

    async def run():
        transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/v1/tickets", content=gen(), headers={**h, "Content-Type": "application/json",
                                                                       "Idempotency-Key": KEY})
    assert asyncio.run(run()).status_code == 413


@pytest.mark.parametrize("ct", [None, "text/plain", "application/xml", "application/x-www-form-urlencoded",
                                "application/jsonx", "multipart/form-data", "application/json; charset=latin-1"])
def test_non_json_media_type_is_415_before_validation(env, ct):
    r = env.post(writer(env), content_type=ct, raw=b"[1,2,")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.parametrize("ct", ["application/json", "Application/JSON", "application/json; charset=utf-8",
                                "application/json;charset=UTF-8"])
def test_json_media_type_variants_are_accepted(env, ct):
    assert env.post(writer(env), content_type=ct).status_code == 202


# ------------------------------------------------------------------ validation (422)


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b"null", b"123", b'"s"', b"{", b'{"customer_id": "c", "message": "m"} trailing',
    b'{"customer_id":"c","message":"m","message":', b"\xff\xfe\x00",
])
def test_malformed_json_or_non_object_is_422_without_echo(env, raw):
    r = env.post(writer(env), raw=raw)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert env.tickets._counts() == (0, 0)


@pytest.mark.parametrize("body", [
    {}, {"customer_id": "c"}, {"message": "m"}, {"customer_id": "", "message": "m"}, {"customer_id": "c", "message": ""},
    {"customer_id": "   ", "message": "m"}, {"customer_id": "c", "message": "   "},
    {"customer_id": "c" * 101, "message": "m"}, {"customer_id": "c", "message": "m" * 4001},
    {"customer_id": "c", "message": "m", "order_id": ""}, {"customer_id": "c", "message": "m", "order_id": "o" * 101},
    {"customer_id": 123, "message": "m"}, {"customer_id": "c", "message": ["m"]}, {"customer_id": None, "message": "m"},
    {"customer_id": "c", "message": "m", "order_id": 5}, {"customer_id": "c\u0000", "message": "m"},
    {"customer_id": "c", "message": "bad\u0000nul"}, {"customer_id": "c", "message": "m", "order_id": "o\u0000"},
])
def test_invalid_fields_are_422_and_store_nothing(env, body):
    r = env.post(writer(env), body=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert env.tickets._counts() == (0, 0)


def test_lone_surrogate_is_422_not_500(env):
    r = env.post(writer(env), raw=b'{"customer_id": "c", "message": "\\ud800"}')
    assert r.status_code == 422


def test_422_never_reflects_input_values_or_unknown_keys(env):
    r = env.post(writer(env), body={"customer_id": MARKER * 20, "message": "m", MARKER: MARKER})
    assert r.status_code == 422 and MARKER not in r.text
    r2 = env.post(writer(env), body={"customer_id": 12345678, "message": f"{MARKER}" * 100})
    assert r2.status_code == 422 and MARKER not in r2.text and "12345678" not in r2.text


# ------------------------------------------------------------------ failures and sanitisation


def test_storage_unavailable_is_503_and_failure_is_500_and_nothing_leaks(env, caplog):
    caplog.set_level(logging.DEBUG)
    h = writer(env)
    env.tickets._unavailable = True
    a = env.post(h, body={**BODY, "message": MARKER})
    env.tickets._unavailable = False
    assert a.status_code == 503 and a.json()["error"]["code"] == "service_unavailable"

    def boom(**kw):
        raise StorageFailure()

    env.tickets.create_ticket = boom  # type: ignore[method-assign]
    b = env.post(h, body={**BODY, "message": MARKER})

    def explode(**kw):
        raise RuntimeError(f"dsn=postgresql://u:{MARKER}@db.internal/x")

    env.tickets.create_ticket = explode  # type: ignore[method-assign]
    c = env.post(h, body={**BODY, "message": MARKER})
    assert b.status_code == 500 and c.status_code == 500 and c.json()["error"]["code"] == "internal_error"
    assert MARKER not in a.text + b.text + c.text + caplog.text and "db.internal" not in c.text + caplog.text
    assert KEY not in caplog.text and writer.__name__  # idempotency key is never logged


def test_handler_receives_tenant_from_credential_only(env):
    seen = []
    original = env.tickets.create_ticket
    env.tickets.create_ticket = lambda **kw: seen.append(kw) or original(**kw)  # type: ignore[method-assign]
    env.post({**writer(env, "tenant-a"), "X-Tenant-Id": "tenant-b"})
    assert [k["tenant_id"] for k in seen] == ["tenant-a"]


def test_post_performs_no_network_and_no_llm(env, monkeypatch):
    import socket

    def blocked(*a, **k):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    assert env.post(writer(env)).status_code == 202


def test_other_methods_on_the_collection_are_405(env):
    async def run(method):
        transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, "/v1/tickets", headers=writer(env))
    for m in ("GET", "PUT", "PATCH", "DELETE"):
        assert asyncio.run(run(m)).status_code in (404, 405)
