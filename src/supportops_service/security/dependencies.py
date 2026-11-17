"""FastAPI dependencies: ``authenticate`` and ``require_scope`` (contract §3.4, §3.6).

Wiring (done by the application factory, owner: Beta)::

    install_security(app, verifier)          # once, in create_app

Routes then declare ``Depends(require_scope(Scope.TICKETS_WRITE))``.

Tenant and scopes come exclusively from the verified credential. Only the ``Authorization``
header is read: no other header, query parameter, path segment or body field can influence the
resulting ``TenantContext``.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from typing import Any, Final

from fastapi import Depends, FastAPI, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from supportops_service.interfaces import Scope, TenantContext
from supportops_service.security.credentials import CredentialVerifier, parse_authorization
from supportops_service.security.errors import (
    AuthenticationUnavailable,
    DenyReason,
    InsufficientScope,
    install_security_error_handlers,
)


_CONTEXT_KEY: Final = "supportops.verified_tenant_context"
_REQUIRED_SCOPE_ATTR: Final = "required_scope"


# MVP: 3 concurrent verifications against the default PostgreSQL pool of 4 (owner decision, M2):
# at least one connection stays free for non-authentication work.
DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS: Final = 3
DEFAULT_AUTH_QUEUE_WAIT_SECONDS: Final = 1.0


class AuthenticationGate:
    """Bounds concurrent credential verifications (each holds a pool thread and, with the
    PostgreSQL repository, a connection) so a flood of well-formed but unknown keys cannot
    exhaust the shared threadpool or connection pool. Saturation fails closed: after a short
    wait the request is refused with 503, never admitted unauthenticated. No Redis, no global
    rate limiter: this is a concurrency cap only.
    """

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS,
        queue_wait_seconds: float = DEFAULT_AUTH_QUEUE_WAIT_SECONDS,
        pool_max_size: int | None = None,
    ) -> None:
        if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) or max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if queue_wait_seconds < 0:
            raise ValueError("queue_wait_seconds must be >= 0")
        if pool_max_size is not None:
            if not isinstance(pool_max_size, int) or isinstance(pool_max_size, bool) or pool_max_size < 1:
                raise ValueError("pool_max_size must be >= 1")
            if max_concurrent >= pool_max_size:
                # Shared pool: authentication must never be able to take every connection.
                raise ValueError("max_concurrent must be < pool_max_size")
        self._max = max_concurrent
        # True only when the pool size was known and the limit was checked against it.
        # Unknown pool size => no isolation is claimed (the gate is then only a concurrency cap).
        self.pool_isolation_verified = pool_max_size is not None
        # One semaphore per running event loop: an asyncio.Semaphore binds to the loop that first
        # contends for it and raises RuntimeError on another loop (review M1).
        self._semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )
        self._wait = float(queue_wait_seconds)

    def _semaphore_for_loop(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = self._semaphores[loop] = asyncio.Semaphore(self._max)
        return semaphore

    async def authenticate(
        self, verifier: CredentialVerifier, authorization_values: list[str]
    ) -> TenantContext:
        semaphore = self._semaphore_for_loop()
        if semaphore.locked():
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=self._wait)
            except TimeoutError:
                raise AuthenticationUnavailable(DenyReason.OVERLOADED) from None
        else:
            await semaphore.acquire()  # free slot: completes without suspending
        try:
            return await run_in_threadpool(verifier.authenticate, authorization_values)
        finally:
            semaphore.release()


def install_security(
    app: FastAPI,
    verifier: CredentialVerifier,
    *,
    max_concurrent_authentications: int = DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS,
    auth_queue_wait_seconds: float = DEFAULT_AUTH_QUEUE_WAIT_SECONDS,
    pool_max_size: int | None = None,
) -> None:
    """Attach the verifier, the authentication gate and the security error handlers.

    ``pool_max_size`` is the size of the connection pool shared with other work, if known;
    the gate limit must then be strictly smaller (``ValueError`` otherwise).
    """
    gate = AuthenticationGate(max_concurrent_authentications, auth_queue_wait_seconds, pool_max_size)
    app.state.credential_verifier = verifier
    app.state.authentication_gate = gate
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


def scope_required_by(call: object) -> Scope | None:
    """The scope a ``require_scope(...)`` dependency demands, or ``None`` for any other callable."""
    scope = getattr(call, _REQUIRED_SCOPE_ATTR, None)
    return scope if isinstance(scope, Scope) else None


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
            gate = getattr(request.app.state, "authentication_gate", None)
            if not isinstance(gate, AuthenticationGate):
                raise AuthenticationUnavailable(DenyReason.NOT_CONFIGURED)
            context = await gate.authenticate(verifier, request.headers.getlist("authorization"))
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
