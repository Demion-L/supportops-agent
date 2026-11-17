"""Synthetic fixtures for the security suite. No real secrets, no network."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings
from supportops_service.interfaces import CredentialRecord, Scope, TenantContext
from supportops_service.security.credentials import (
    CredentialVerifier,
    PepperSet,
    compute_key_hash,
    generate_api_key,
)
from supportops_service.security.dependencies import (
    SecuredRoute,
    authenticate,
    install_security,
    require_scope,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PEPPER = b"synthetic-pepper-0123456789abcdef-A"  # 35 bytes, test-only
PEPPER_2 = b"synthetic-pepper-0123456789abcdef-B"


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class InMemoryCredentialRepository:
    """Test fake of ``CredentialRepository`` (the PG implementation is a later milestone)."""

    def __init__(self) -> None:
        self.records: dict[str, CredentialRecord] = {}
        self.touched: list[str] = []
        self.lookups: list[str] = []
        self.fail_lookup: Exception | None = None
        self.fail_touch: Exception | None = None

    def add(self, record: CredentialRecord) -> None:
        self.records[record.key_prefix] = record

    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None:
        self.lookups.append(key_prefix)
        if self.fail_lookup is not None:
            raise self.fail_lookup
        return self.records.get(key_prefix)

    def touch_last_used(self, credential_id: str) -> None:
        if self.fail_touch is not None:
            raise self.fail_touch
        self.touched.append(credential_id)


@dataclass
class Issued:
    key: str
    record: CredentialRecord


class Env:
    def __init__(self) -> None:
        self.repo = InMemoryCredentialRepository()
        self.clock = FixedClock()
        self.peppers = PepperSet({"p1": PEPPER, "p2": PEPPER_2})
        self.verifier = CredentialVerifier(self.repo, self.peppers, self.clock)

    def issue(
        self,
        tenant_id: str = "tenant-a",
        credential_id: str = "cred-a1",
        scopes: frozenset[str] = frozenset({"tickets:read", "tickets:write"}),
        pepper_id: str = "p1",
        **overrides: object,
    ) -> Issued:
        k = generate_api_key()
        pepper = PEPPER if pepper_id == "p1" else PEPPER_2
        record = CredentialRecord(
            credential_id=credential_id,
            tenant_id=tenant_id,
            key_prefix=k.prefix,
            key_hash=compute_key_hash(pepper, k.prefix, k.secret),
            pepper_id=pepper_id,
            scopes=scopes,
        )
        if overrides:
            record = replace(record, **overrides)  # type: ignore[arg-type]
        self.repo.add(record)
        return Issued(key=k.render(), record=record)


class WriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


@pytest.fixture
def env() -> Env:
    return Env()


@pytest.fixture
def app(env: Env) -> FastAPI:
    application = create_app(ServiceSettings())
    install_security(application, env.verifier)

    router = APIRouter(route_class=SecuredRoute)

    @application.get("/v1/whoami")
    def whoami(ctx: TenantContext = Depends(authenticate)) -> dict:
        return {
            "tenant_id": ctx.tenant_id,
            "credential_id": ctx.credential_id,
            "scopes": sorted(ctx.scopes),
        }

    @application.get("/v1/read-probe/{item_id}")
    def read_probe(item_id: str, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"tenant_id": ctx.tenant_id}

    @application.post("/v1/write-probe")
    def write_probe(
        body: WriteBody, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_WRITE))
    ):
        return {"tenant_id": ctx.tenant_id, "echo": body.message}

    @application.get("/v1/trace-probe")
    def trace_probe(ctx: TenantContext = Depends(require_scope(Scope.TRACE_READ))):
        return {"ok": True}

    @router.post("/v1/secured-write-probe")
    def secured_write(
        body: WriteBody, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_WRITE))
    ):
        return {"tenant_id": ctx.tenant_id, "echo": body.message}

    application.include_router(router)
    return application


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def security_logs(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    return caplog


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


__all__ = ["Env", "Issued", "bearer", "timedelta"]
