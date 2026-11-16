"""FastAPI dependencies: ``authenticate`` and ``require_scope`` (contract §3.4, §3.6).

Wiring (done by the application factory, owner: Beta)::

    install_security(app, verifier)          # once, in create_app

Routes then declare ``Depends(require_scope(Scope.TICKETS_WRITE))``.

Tenant and scopes come exclusively from the verified credential. Only the ``Authorization``
header is read: no other header, query parameter, path segment or body field can influence the
resulting ``TenantContext``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from fastapi import Depends, FastAPI, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from supportops_service.interfaces import TenantContext
from supportops_service.security.contracts import Scope
from supportops_service.security.credentials import CredentialVerifier, parse_authorization
from supportops_service.security.errors import (
    AuthenticationUnavailable,
    DenyReason,
    InsufficientScope,
    install_security_error_handlers,
)


_CONTEXT_KEY: Final = "supportops.verified_tenant_context"
_REQUIRED_SCOPE_ATTR: Final = "required_scope"


def install_security(app: FastAPI, verifier: CredentialVerifier) -> None:
    """Attach the verifier and the security error handlers to an application."""
    app.state.credential_verifier = verifier
    install_security_error_handlers(app)


def get_verifier(request: Request) -> CredentialVerifier:
    verifier = getattr(request.app.state, "credential_verifier", None)
    if verifier is None:
        # Authentication wired incorrectly must deny, never skip.
        raise AuthenticationUnavailable(DenyReason.NOT_CONFIGURED)
    return verifier


def authenticate(
    request: Request, verifier: CredentialVerifier = Depends(get_verifier)
) -> TenantContext:
    """Resolve the caller's ``TenantContext`` or raise the uniform 401 (or 503)."""
    # Per-request ASGI scope key (NOT scope["state"], which some servers may share across
    # requests). Set only server-side by SecuredRoute, never from client input.
    verified = request.scope.get(_CONTEXT_KEY)
    if isinstance(verified, TenantContext):
        return verified
    return verifier.authenticate(request.headers.getlist("authorization"))


def require_scope(scope: Scope) -> Callable[..., TenantContext]:
    """Dependency factory: authenticated (401) and holding ``scope`` (403)."""
    required = Scope(scope)  # a non-Scope value fails at wiring time, not per request

    def dependency(context: TenantContext = Depends(authenticate)) -> TenantContext:
        if required not in context.scopes:
            raise InsufficientScope(
                DenyReason.SCOPE,
                credential_id=context.credential_id,
                tenant_id=context.tenant_id,
            )
        return context

    setattr(dependency, _REQUIRED_SCOPE_ATTR, required)
    return dependency


def _required_scopes(dependant: Dependant) -> frozenset[Scope]:
    found: set[Scope] = set()
    for sub in dependant.dependencies:
        scope = getattr(sub.call, _REQUIRED_SCOPE_ATTR, None)
        if isinstance(scope, Scope):
            found.add(scope)
        found |= _required_scopes(sub)
    return frozenset(found)


class SecuredRoute(APIRoute):
    """Route class that authenticates and checks scopes BEFORE the request body is read.

    FastAPI parses (and fails on) the body before running dependencies, so without this a
    malformed body from an unauthenticated caller would get 422 instead of 401 (invariant I-13),
    and an authenticated caller without scope would get 422 instead of 403 (AC-5).

    Usage: ``APIRouter(prefix="/v1", route_class=SecuredRoute)``. Dependencies still enforce the
    same rules, so a route not using this class stays safe and only differs in error ordering.
    """

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()
        required = _required_scopes(self.dependant)

        async def handler(request: Request) -> Response:
            verifier = get_verifier(request)
            # Cheap format check on the event loop: junk never occupies a pool thread.
            parse_authorization(request.headers.getlist("authorization"))
            context = await run_in_threadpool(
                verifier.authenticate, request.headers.getlist("authorization")
            )
            for scope in required:
                if scope not in context.scopes:
                    raise InsufficientScope(
                        DenyReason.SCOPE,
                        credential_id=context.credential_id,
                        tenant_id=context.tenant_id,
                    )
            request.scope[_CONTEXT_KEY] = context
            return await original(request)

        return handler
