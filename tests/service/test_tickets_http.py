"""HTTP tests for ``GET /v1/tickets/{ticket_id}`` on in-memory repositories (no database)."""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings
from supportops_service.db.errors import StorageFailure, StorageUnavailable
from supportops_service.interfaces import CredentialRecord
from supportops_service.repositories.fakes import InMemoryTicketRepository
from supportops_service.repositories.protocols import TicketView
from supportops_service.routers.tickets import create_tickets_router
from supportops_service.schemas import TicketResponse
from supportops_service.security.credentials import compute_key_hash, generate_api_key
from supportops_service.security.route_audit import find_unsecured_routes

PEPPER = "http-slice-pepper-0123456789abcdefgh"
SETTINGS = ServiceSettings(pepper=PEPPER, pepper_id="p1")  # type: ignore[arg-type]
MARKER = "SECRET-MARKER-7f3a"
MISSING = "tkt_" + "0" * 32


class CredRepo:
    def __init__(self) -> None:
        self.records: dict[str, CredentialRecord] = {}

    def add(self, record: CredentialRecord) -> None:
        self.records[record.key_prefix] = record

    def get_by_prefix(self, key_prefix: str):
        return self.records.get(key_prefix)

    def touch_last_used(self, credential_id: str) -> None:
        pass


@dataclass
class Issued:
    key: str
    credential_id: str


class Env:
    def __init__(self) -> None:
        self.creds = CredRepo()
        self.tickets = InMemoryTicketRepository()
        self.app = create_app(SETTINGS, credentials=self.creds, v1_routers=[create_tickets_router(self.tickets)])

    def issue(self, tenant="tenant-a", scopes=("tickets:read",), **overrides) -> Issued:
        k = generate_api_key()
        record = CredentialRecord(
            credential_id=f"cred-{tenant}-{k.prefix[:4]}", tenant_id=tenant, key_prefix=k.prefix,
            key_hash=compute_key_hash(PEPPER.encode(), k.prefix, k.secret), pepper_id="p1",
            scopes=frozenset(scopes),
        )
        record = replace(record, **overrides) if overrides else record
        self.creds.add(record)
        return Issued(k.render(), record.credential_id)

    def ticket(self, tenant="tenant-a", n=1) -> str:
        return self.tickets.create_ticket(
            tenant_id=tenant, idempotency_key=f"idem-key-{tenant}-{n}", customer_id="cust-secret-1",
            message="message-secret", order_id="order-secret").ticket_id

    def get(self, path, headers=None, cookies=None):
        async def run():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://t", cookies=cookies) as c:
                return await c.get(path, headers=headers)
        return asyncio.run(run())


@pytest.fixture
def env() -> Env:
    return Env()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def shape(r):  # response identity ignoring the per-request id
    body = r.json()
    body["error"].pop("request_id", None)
    return r.status_code, body, {k: v for k, v in r.headers.items() if k not in ("x-request-id", "content-length")}


# ---------------------------------------------------------------- happy path and projection


def test_owner_reads_own_ticket_with_closed_projection(env):
    tid, key = env.ticket(), env.issue().key
    r = env.get(f"/v1/tickets/{tid}", bearer(key))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ticket_id", "run_id", "status", "created_at"}  # nulls are omitted
    assert body["ticket_id"] == tid and body["status"] == "accepted" and body["run_id"].startswith("run_")
    assert r.headers["cache-control"] == "no-store"
    for secret in ("cust-secret-1", "message-secret", "order-secret", "tenant-a", "customer_id", "cred-"):
        assert secret not in r.text


def test_failed_run_exposes_only_the_closed_failure_code(env):
    tid, key = env.ticket(), env.issue().key
    env.tickets._force_run_failed("tenant-a", tid, "deadline_exceeded")
    body = env.get(f"/v1/tickets/{tid}", bearer(key)).json()
    assert body["status"] == "failed" and body["failure"] == {"code": "deadline_exceeded"}
    assert "answer" not in body and "finished_at" in body


def test_completed_view_serialises_answer_and_never_internal_fields():
    now = datetime.now(UTC)
    view = TicketView("tkt_" + "a" * 32, "run_" + "b" * 32, "completed", now, now, now, "final answer", None)
    out = TicketResponse.from_view(view).model_dump(exclude_none=True, mode="json")
    assert out["answer"] == "final answer" and "failure" not in out
    assert not {"customer_id", "tenant_id", "message", "order_id", "credential_id"} & set(out)


def test_response_schema_has_no_identity_or_credential_fields():
    assert not {"customer_id", "tenant_id", "message", "order_id", "credential_id", "key_prefix"} & set(
        TicketResponse.model_fields)


# ---------------------------------------------------------------- cross-tenant and indistinguishability


def test_foreign_unknown_and_malformed_ids_are_indistinguishable_404(env):
    foreign, key = env.ticket(tenant="tenant-b"), env.issue(tenant="tenant-a").key
    own = env.ticket(tenant="tenant-a")
    assert env.get(f"/v1/tickets/{own}", bearer(key)).status_code == 200
    shapes = [shape(env.get(f"/v1/tickets/{tid}", bearer(key)))
              for tid in (foreign, MISSING, "not-a-ticket", "tkt_" + "G" * 32, "tkt_" + "a" * 33)]
    assert shapes[0][0] == 404 and shapes[0][1]["error"]["code"] == "not_found"
    assert all(s == shapes[0] for s in shapes)


def test_two_tenants_each_see_only_their_own_ticket(env):
    a, b = env.ticket("tenant-a"), env.ticket("tenant-b")
    ka, kb = env.issue("tenant-a").key, env.issue("tenant-b").key
    assert env.get(f"/v1/tickets/{a}", bearer(ka)).status_code == 200
    assert env.get(f"/v1/tickets/{b}", bearer(ka)).status_code == 404
    assert env.get(f"/v1/tickets/{b}", bearer(kb)).status_code == 200
    assert env.get(f"/v1/tickets/{a}", bearer(kb)).status_code == 404


def test_tenant_cannot_be_spoofed_through_header_query_or_cookie(env):
    foreign, key = env.ticket(tenant="tenant-b"), env.issue(tenant="tenant-a").key
    r = env.get(f"/v1/tickets/{foreign}?tenant_id=tenant-b&tenant=tenant-b",
                {**bearer(key), "X-Tenant-Id": "tenant-b", "X-Forwarded-Tenant": "tenant-b"},
                cookies={"tenant_id": "tenant-b"})
    assert r.status_code == 404


def test_scope_cannot_be_raised_through_request_parameters(env):
    tid, key = env.ticket(), env.issue(scopes=("tickets:write",)).key
    r = env.get(f"/v1/tickets/{tid}?scope=tickets:read&scopes=tickets:read",
                {**bearer(key), "X-Scopes": "tickets:read", "X-Scope": "tickets:read"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"


# ---------------------------------------------------------------- authentication and authorisation


def _reference_401(env):
    return shape(env.get(f"/v1/tickets/{MISSING}"))


def test_missing_malformed_unknown_revoked_expired_credentials_give_identical_401(env):
    tid = env.ticket()
    revoked = env.issue(revoked_at=datetime(2020, 1, 1, tzinfo=UTC)).key
    expired = env.issue(expires_at=datetime.now(UTC) - timedelta(days=1)).key
    unknown = generate_api_key().render()
    good = env.issue().key
    cases = [None, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer  x"},
             {"Authorization": "bearer " + good[:-1] + "!"}, bearer("junk"), bearer(unknown), bearer(revoked),
             bearer(expired), bearer(good[:-3] + "AAA")]
    shapes = [shape(env.get(f"/v1/tickets/{tid}", h)) for h in cases]
    assert shapes[0][0] == 401 and shapes[0][1]["error"]["code"] == "unauthenticated"
    assert shapes[0][2]["www-authenticate"] == "Bearer"
    assert all(s == shapes[0] for s in shapes)
    assert env.get(f"/v1/tickets/{tid}", bearer(good)).status_code == 200


def test_key_in_query_or_cookie_is_not_accepted(env):
    tid, key = env.ticket(), env.issue().key
    assert env.get(f"/v1/tickets/{tid}?api_key={key}&access_token={key}").status_code == 401
    assert env.get(f"/v1/tickets/{tid}", cookies={"Authorization": f"Bearer {key}"}).status_code == 401


def test_missing_scope_is_403_before_lookup_and_identical_for_existing_and_missing_ids(env):
    tid, key = env.ticket(), env.issue(scopes=("tickets:write",)).key
    calls = []
    original = env.tickets.get_ticket
    env.tickets.get_ticket = lambda **kw: calls.append(kw) or original(**kw)  # type: ignore[method-assign]
    a = shape(env.get(f"/v1/tickets/{tid}", bearer(key)))
    b = shape(env.get(f"/v1/tickets/{MISSING}", bearer(key)))
    assert a[0] == 403 and a == b
    assert calls == []  # authorisation happened before the repository was touched


def test_trace_scope_does_not_grant_ticket_read(env):
    key = env.issue(scopes=("trace:read",)).key
    assert env.get(f"/v1/tickets/{env.ticket()}", bearer(key)).status_code == 403


def test_the_route_cannot_be_reached_without_secured_route():
    env = Env()
    assert find_unsecured_routes(env.app) == []
    routes = create_tickets_router(InMemoryTicketRepository()).routes
    assert routes and all(type(r).__name__ == "SecuredRoute" for r in routes)
    assert {(r.path, tuple(sorted(r.methods))) for r in routes} == {
        ("/v1/tickets/{ticket_id}", ("GET",)), ("/v1/tickets", ("POST",))}


# ---------------------------------------------------------------- injection and sanitisation


@pytest.mark.parametrize("evil", ["' OR '1'='1", "x'; DROP TABLE tickets;--", "%27%20OR%201=1--",
                                  "tkt_" + "a" * 32 + "'--", "tkt_" + "a" * 32 + "%0a", "../../etc/passwd"])
def test_sql_injection_style_ids_are_plain_404(env, evil):
    env.ticket()
    key = env.issue().key
    r = env.get(f"/v1/tickets/{evil}", bearer(key))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_storage_unavailable_is_503_and_failure_is_500_without_leaks(env, caplog):
    tid, key = env.ticket(), env.issue().key
    caplog.set_level(logging.DEBUG)
    env.tickets._unavailable = True
    r = env.get(f"/v1/tickets/{tid}", bearer(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
    env.tickets._unavailable = False

    def boom(**kw):
        raise RuntimeError(f"password={MARKER} host=db.internal")

    env.tickets.get_ticket = boom  # type: ignore[method-assign]
    r2 = env.get(f"/v1/tickets/{tid}", bearer(key))
    assert r2.status_code == 500 and r2.json()["error"]["code"] == "internal_error"

    def failure(**kw):
        raise StorageFailure()

    env.tickets.get_ticket = failure  # type: ignore[method-assign]
    assert env.get(f"/v1/tickets/{tid}", bearer(key)).status_code == 500
    assert MARKER not in r.text + r2.text + caplog.text and "db.internal" not in r2.text + caplog.text
    assert key not in caplog.text


def test_unavailable_error_type_is_the_documented_one():
    assert issubclass(StorageUnavailable, Exception)


# ---------------------------------------------------------------- static boundaries


def test_router_module_imports_no_agent_driver_or_sql():
    src = pathlib.Path("src/supportops_service/routers/tickets.py").read_text()
    imported = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module else a.name.split(".")[0]
                for n in ast.walk(ast.parse(src)) for a in (getattr(n, "names", None) or [None])
                if isinstance(n, (ast.Import, ast.ImportFrom)) and (a is not None or isinstance(n, ast.ImportFrom))}
    assert not imported & {"supportops_agent", "psycopg", "psycopg_pool", "openai", "httpx", "requests"}
    assert "SELECT" not in src and "INSERT" not in src
