"""Every protected /v1 route must use SecuredRoute (API-004 integration contract, SecuredRoute)."""

from __future__ import annotations

import pytest

from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient

from conftest import WriteBody, bearer
from supportops_service.app import create_app
from supportops_service.config import ServiceSettings
from supportops_service.interfaces import TenantContext
from supportops_service.interfaces import Scope
from supportops_service.security.dependencies import (
    SecuredRoute,
    install_security,
    require_scope,
)
from supportops_service.security.route_audit import find_unsecured_routes


def test_real_application_has_no_unsecured_v1_route():
    """Integration guard: fails as soon as ``create_app`` registers a bare /v1 route."""
    assert find_unsecured_routes(create_app(ServiceSettings())) == []


def test_audit_detects_plain_route_decorator_router_and_prefix():
    app = FastAPI()

    @app.get("/v1/direct")
    def direct() -> dict:
        return {}

    plain = APIRouter(prefix="/v1")

    @plain.post("/included")
    def included() -> dict:
        return {}

    app.include_router(plain)

    nested_inner = APIRouter()

    @nested_inner.get("/deep")
    def deep() -> dict:
        return {}

    nested_outer = APIRouter(prefix="/v1")
    nested_outer.include_router(nested_inner, prefix="/nested")
    app.include_router(nested_outer)

    assert find_unsecured_routes(app) == [
        "APIRoute /v1/direct",
        "APIRoute /v1/included",
        "APIRoute /v1/nested/deep",
    ]


def test_audit_detects_a_prefix_applied_at_include_time():
    app = FastAPI()
    router = APIRouter()

    @router.get("/late")
    def late() -> dict:
        return {}

    app.include_router(router, prefix="/v1")
    assert find_unsecured_routes(app) == ["APIRoute /v1/late"]


def test_audit_detects_websocket_and_mount_under_v1():
    app = FastAPI()

    @app.websocket("/v1/stream")
    async def stream(ws: WebSocket) -> None:  # noqa: ARG001
        return None

    app.mount("/v1/static", FastAPI())
    found = find_unsecured_routes(app)
    assert any(item.endswith("/v1/stream") for item in found)
    assert any(item.endswith("/v1/static") for item in found)


def test_audit_accepts_secured_routes_with_different_scope_policies():
    app = FastAPI()
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.get("/reads")
    def reads(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))) -> dict:
        return {}

    @router.post("/writes")
    def writes(
        body: WriteBody, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_WRITE))
    ) -> dict:
        return {}

    @router.get("/traces")
    def traces(ctx: TenantContext = Depends(require_scope(Scope.TRACE_READ))) -> dict:
        return {}

    app.include_router(router)
    assert find_unsecured_routes(app) == []


def test_audit_ignores_routes_outside_the_protected_prefix_and_lookalikes():
    app = FastAPI()

    @app.get("/health")
    def health() -> dict:
        return {}

    @app.get("/v10/lookalike")  # not under /v1/, must not be confused with it
    def lookalike() -> dict:
        return {}

    assert find_unsecured_routes(app) == []


def test_public_paths_require_an_explicit_exact_allow_list_entry():
    app = FastAPI()

    @app.get("/v1/ping")
    def ping() -> dict:
        return {}

    @app.get("/v1/secret-ish")
    def secret_ish() -> dict:
        return {}

    assert find_unsecured_routes(app, public_paths={"/v1/ping"}) == ["APIRoute /v1/secret-ish"]
    assert find_unsecured_routes(app, public_paths={"/v1"}) != []  # no prefix wildcard


def test_secured_route_survives_include_router_and_orders_errors(env):
    """Behavioural proof that what the audit accepts really gives 401 before 422."""
    app = create_app(ServiceSettings())
    install_security(app, env.verifier)
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.post("/items")
    def create(
        body: WriteBody, ctx: TenantContext = Depends(require_scope(Scope.TICKETS_WRITE))
    ) -> dict:
        return {"tenant_id": ctx.tenant_id}

    app.include_router(router)
    assert find_unsecured_routes(app) == []
    client = TestClient(app, raise_server_exceptions=False)
    bad = {"content": "{broken", "headers": {"content-type": "application/json"}}
    r = client.post("/v1/items", **bad)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    reader = env.issue(scopes=frozenset({"tickets:read"}))
    assert client.post(
        "/v1/items", content="{broken", headers={**bearer(reader.key), "content-type": "application/json"}
    ).status_code == 403


def test_router_level_scope_dependencies_still_enforce_scope_after_body_parsing(env):
    """Documented limitation (FastAPI 0.163): scopes declared via ``include_router(dependencies=)``
    are not visible to SecuredRoute when it is built, so they are enforced by the dependency
    (403 with a valid body) but a *malformed* body from a credential lacking the scope yields
    422. Authentication (401) is unaffected. Declare scopes on the route itself to get the
    contract ordering. If a FastAPI upgrade changes this, update the contract note.
    """
    app = create_app(ServiceSettings())
    install_security(app, env.verifier)
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.post("/included-scope")
    def handler(body: WriteBody) -> dict:
        return {"ok": True}

    app.include_router(router, dependencies=[Depends(require_scope(Scope.TICKETS_WRITE))])
    client = TestClient(app, raise_server_exceptions=False)
    reader = env.issue(scopes=frozenset({"tickets:read"}))
    json_headers = {**bearer(reader.key), "content-type": "application/json"}

    assert client.post("/v1/included-scope", content="{broken", headers={"content-type": "application/json"}).status_code == 401
    assert client.post("/v1/included-scope", json={"message": "x"}, headers=bearer(reader.key)).status_code == 403
    assert client.post("/v1/included-scope", content="{broken", headers=json_headers).status_code in {403, 422}


def test_audit_detects_parameterised_catch_all_and_root_mount_shadowing():
    """Review F3: routes outside the literal /v1 that can still serve /v1/... are reported."""
    from fastapi import FastAPI
    from fastapi.routing import APIRouter

    from supportops_service.security.route_audit import find_unsecured_routes

    def build(register):
        app = FastAPI()
        register(app)
        return find_unsecured_routes(app)

    assert build(lambda a: a.get("/{tenant}/secret")(lambda: 1))
    assert build(lambda a: a.get("/{p:path}")(lambda: 1))
    assert build(lambda a: a.get("/v{n}/x")(lambda: 1))
    sub = FastAPI()
    assert build(lambda a: a.mount("/", sub))
    # harmless routes stay clean
    assert build(lambda a: a.get("/healthz")(lambda: 1)) == []
    assert build(lambda a: a.get("/items/{item_id}")(lambda: 1)) == []
    assert build(lambda a: a.include_router(APIRouter())) == []


def test_hostile_record_object_is_denied_not_500(env):
    from conftest import bearer  # noqa: F401
    from supportops_service.security.errors import Unauthenticated

    issued = env.issue()

    class Hostile:
        @property
        def credential_id(self):
            raise RuntimeError("secret-dsn")

    env.repo.records[issued.record.key_prefix] = Hostile()  # type: ignore[assignment]
    with pytest.raises(Unauthenticated):
        env.verifier.authenticate([f"Bearer {issued.key}"])
