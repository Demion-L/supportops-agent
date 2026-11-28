"""create_app <-> pepper rotation wiring and scope-declaration startup guard (Beta-owned; Alpha patch 0004)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from hermes_service.app import create_app
from hermes_service.config import ServiceConfigurationError, ServiceSettings
from hermes_service.interfaces import CredentialRecord, Scope, TenantContext
from hermes_service.security.credentials import compute_key_hash, generate_api_key
from hermes_service.security.dependencies import SecuredRoute, require_scope

OLD = "old-pepper-0123456789abcdef-ABCDEFG"
NEW = "new-pepper-0123456789abcdef-HIJKLMN"


class Repo:
    def __init__(self):
        self.records = {}

    def get_by_prefix(self, prefix):
        return self.records.get(prefix)

    def touch_last_used(self, credential_id):
        pass


def _issue(repo, pepper_id, pepper):
    key = generate_api_key()
    repo.records[key.prefix] = CredentialRecord(
        credential_id=f"c-{pepper_id}", tenant_id="tenant-a", key_prefix=key.prefix,
        key_hash=compute_key_hash(pepper.encode(), key.prefix, key.secret),
        pepper_id=pepper_id, scopes=frozenset({"tickets:read"}),
    )
    return key.render()


def _router():
    router = APIRouter(prefix="/v1", route_class=SecuredRoute)

    @router.get("/things")
    def things(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"credential_id": ctx.credential_id}

    return router


def _get(app, key):
    with TestClient(app, raise_server_exceptions=False) as client:
        return client.get("/v1/things", headers={"Authorization": f"Bearer {key}"})


def test_old_and_new_pepper_coexist_and_a_dropped_pepper_fails_closed():
    repo = Repo()
    old_key, new_key = _issue(repo, "p1", OLD), _issue(repo, "p2", NEW)
    both = ServiceSettings(pepper=NEW, pepper_id="p2", previous_peppers=f"p1={OLD}")  # type: ignore[arg-type]
    app = create_app(both, credentials=repo, v1_routers=[_router()])
    assert _get(app, old_key).json() == {"credential_id": "c-p1"}
    assert _get(app, new_key).json() == {"credential_id": "c-p2"}

    retired = ServiceSettings(pepper=NEW, pepper_id="p2")  # type: ignore[arg-type]
    app = create_app(retired, credentials=repo, v1_routers=[_router()])
    assert _get(app, old_key).status_code == 401
    assert _get(app, new_key).status_code == 200


@pytest.mark.parametrize("previous", ["p1", f"p2={OLD}", f"p1={OLD},p1={OLD}", "p1=short", "=x"])
def test_malformed_previous_peppers_are_a_startup_error_without_echo(previous):
    settings = ServiceSettings(pepper=NEW, pepper_id="p2", previous_peppers=previous)  # type: ignore[arg-type]
    with pytest.raises(ServiceConfigurationError) as caught:
        create_app(settings, credentials=Repo(), v1_routers=[_router()])
    assert NEW not in str(caught.value) and OLD not in str(caught.value) and "short" not in str(caught.value)


def test_settings_repr_hides_previous_peppers():
    settings = ServiceSettings(pepper=NEW, pepper_id="p2", previous_peppers=f"p1={OLD}")  # type: ignore[arg-type]
    assert OLD not in repr(settings) and NEW not in repr(settings)


def test_router_level_scope_dependencies_are_a_startup_error():
    router = APIRouter(prefix="/v1", route_class=SecuredRoute, dependencies=[Depends(require_scope(Scope.TICKETS_READ))])

    @router.get("/things")
    def things():
        return {}

    settings = ServiceSettings(pepper=NEW, pepper_id="p2")  # type: ignore[arg-type]
    with pytest.raises(ServiceConfigurationError):
        create_app(settings, credentials=Repo(), v1_routers=[router])
