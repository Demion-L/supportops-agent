"""create_app <-> Security Foundation wiring (Beta-owned file; delivered by Alpha's patch)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.testclient import TestClient

from supportops_service.app import create_app
from supportops_service.config import ServiceConfigurationError, ServiceSettings
from supportops_service.interfaces import Scope, TenantContext
from supportops_service.security.credentials import compute_key_hash, generate_api_key
from supportops_service.security.dependencies import SecuredRoute, require_scope
from supportops_service.security.route_audit import find_unsecured_routes

PEPPER = "wiring-test-pepper-0123456789abcdef"
SETTINGS = ServiceSettings(pepper=PEPPER, pepper_id="p1")  # type: ignore[arg-type]


class Repo:
    def __init__(self):
        self.records = {}
        self.touched = []

    def get_by_prefix(self, prefix):
        return self.records.get(prefix)

    def touch_last_used(self, credential_id):
        self.touched.append(credential_id)


def _issue(repo, scopes=("tickets:read",)):
    from supportops_service.interfaces import CredentialRecord

    key = generate_api_key()
    repo.records[key.prefix] = CredentialRecord(
        credential_id="cred-1", tenant_id="tenant-a", key_prefix=key.prefix,
        key_hash=compute_key_hash(PEPPER.encode(), key.prefix, key.secret),
        pepper_id="p1", scopes=frozenset(scopes),
    )
    return key.render()


def _router(*, route_class=SecuredRoute):
    router = APIRouter(prefix="/v1", route_class=route_class)

    @router.get("/things")
    def things(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"tenant_id": ctx.tenant_id}

    return router


def test_default_app_has_no_v1_surface_and_no_slash_redirects():
    app = create_app(ServiceSettings())
    assert app.router.redirect_slashes is False
    assert [r.path for r in app.routes if str(getattr(r, "path", "")).startswith("/v1")] == []
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ("/v1/things", "/v1/things/", "/healthz/"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 404  # never 307: no route-existence oracle


def test_protected_routes_without_credentials_are_a_startup_error():
    with pytest.raises(ServiceConfigurationError):
        create_app(SETTINGS, v1_routers=[_router()])


@pytest.mark.parametrize(
    "settings",
    [
        ServiceSettings(),
        ServiceSettings(pepper=PEPPER),  # type: ignore[arg-type]
        ServiceSettings(pepper_id="p1"),
        ServiceSettings(pepper="short", pepper_id="p1"),  # type: ignore[arg-type]
    ],
)
def test_authentication_without_a_valid_pepper_is_a_startup_error(settings):
    with pytest.raises(ServiceConfigurationError) as caught:
        create_app(settings, credentials=Repo(), v1_routers=[_router()])
    assert "short" not in str(caught.value) and PEPPER not in str(caught.value)


def test_unsecured_v1_router_is_refused_at_startup():
    with pytest.raises(ServiceConfigurationError):
        create_app(SETTINGS, credentials=Repo(), v1_routers=[_router(route_class=APIRouter().route_class)])


def test_websocket_under_v1_is_refused_at_startup():
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.websocket("/stream")
    async def stream(ws: WebSocket):  # pragma: no cover - never reached
        await ws.close()

    with pytest.raises(ServiceConfigurationError):
        create_app(SETTINGS, credentials=Repo(), v1_routers=[router])


def test_secured_v1_end_to_end():
    repo = Repo()
    reader = _issue(repo)
    writer = _issue(repo, scopes=("tickets:write",))
    app = create_app(SETTINGS, credentials=repo, v1_routers=[_router()])
    assert find_unsecured_routes(app) == []
    with TestClient(app, raise_server_exceptions=False) as client:
        anonymous = client.get("/v1/things")
        ok = client.get("/v1/things", headers={"Authorization": f"Bearer {reader}"})
        forbidden = client.get("/v1/things", headers={"Authorization": f"Bearer {writer}"})
        slash = client.get("/v1/things/", headers={"Authorization": f"Bearer {reader}"},
                           follow_redirects=False)
    assert anonymous.status_code == 401 and anonymous.headers["www-authenticate"] == "Bearer"
    assert anonymous.json()["error"]["code"] == "unauthenticated"
    assert ok.status_code == 200 and ok.json() == {"tenant_id": "tenant-a"}
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "insufficient_scope"
    assert slash.status_code == 404


def test_beta_and_alpha_401_403_bodies_use_the_same_codes():
    """A stray HTTPException(401/403) from Beta's generic handler matches Alpha's contract."""
    router = APIRouter(prefix="/open")

    @router.get("/401")
    def raise_401():
        raise HTTPException(status_code=401, detail="secret detail")

    @router.get("/403")
    def raise_403():
        raise HTTPException(status_code=403, detail="secret detail")

    app = create_app(SETTINGS)
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        r401, r403 = client.get("/open/401"), client.get("/open/403")
    assert r401.json()["error"]["code"] == "unauthenticated"
    assert r403.json()["error"]["code"] == "insufficient_scope"
    assert "secret" not in r401.text + r403.text


def test_settings_repr_never_contains_the_pepper():
    assert PEPPER not in repr(SETTINGS) and PEPPER not in str(SETTINGS)
    assert PEPPER not in repr(ServiceSettings.from_env({"SUPPORTOPS_PEPPER": PEPPER}))
