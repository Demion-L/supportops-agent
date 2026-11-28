"""Application factory. Importing this module has no side effects."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI

from hermes_service import __version__
from hermes_service.config import ServiceConfigurationError, ServiceSettings
from hermes_service.errors import install_error_handlers
from hermes_service.interfaces import Clock, CredentialRepository
from hermes_service.llm_mode import resolve_llm_mode
from hermes_service.middleware import RequestContextMiddleware
from hermes_service.routers import health
from hermes_service.security.credentials import CredentialVerifier, PepperSet
from hermes_service.security.dependencies import (
    DEFAULT_AUTH_QUEUE_WAIT_SECONDS,
    DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS,
    install_security,
)
from hermes_service.security.route_audit import (
    find_scopes_outside_route_signature,
    find_unsecured_routes,
)

logger = logging.getLogger("hermes_service.app")


def create_app(
    settings: ServiceSettings | None = None,
    *,
    credentials: CredentialRepository | None = None,
    clock: Clock | None = None,
    v1_routers: Sequence[APIRouter] = (),
    max_concurrent_authentications: int = DEFAULT_MAX_CONCURRENT_AUTHENTICATIONS,
    auth_queue_wait_seconds: float = DEFAULT_AUTH_QUEUE_WAIT_SECONDS,
    pool_max_size: int | None = None,
) -> FastAPI:
    """Build the app.

    ``settings=None`` reads the environment (once, here, not at import time).
    Raises ``ServiceConfigurationError`` if Live Mode is not fully configured.
    No SupportAgent, LLM client or network connection is created.

    ``/v1`` routers are mounted only together with a credential repository and a pepper, and
    only if every ``/v1`` route uses ``SecuredRoute``. Anything else is a startup error:
    the service never runs in an "open" mode.

    ``max_concurrent_authentications`` (default 3) must be smaller than the size of a shared
    connection pool. The pool size comes from ``pool_max_size`` or, if omitted, from the
    credential repository; when neither is known the limit is only a concurrency cap and
    resource isolation is **not** claimed (``app.state.authentication_gate.pool_isolation_verified``).
    """
    settings = settings if settings is not None else ServiceSettings.from_env()
    llm_mode = resolve_llm_mode(settings)  # fail-closed before anything is built

    if v1_routers and credentials is None:
        raise ServiceConfigurationError("Protected routes require a credential repository.")

    docs = settings.docs_enabled
    app = FastAPI(
        title="HERMES Service API",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        # No 307 redirects: they would reveal route existence before authentication.
        redirect_slashes=False,
    )
    app.state.settings = settings
    app.state.llm_mode = llm_mode

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)

    if credentials is not None:
        pepper, pepper_id = settings.pepper, settings.pepper_id
        if pepper is None or pepper_id is None:
            raise ServiceConfigurationError("Authentication requires a pepper and a pepper id.")
        try:
            previous = settings.previous_peppers
            peppers = PepperSet.from_config(
                active_id=pepper_id,
                active_value=pepper.get_secret_value(),
                previous=previous.get_secret_value() if previous is not None else None,
            )
        except ValueError:
            # Fixed text: never echo the pepper or its id.
            raise ServiceConfigurationError("Invalid pepper configuration.") from None
        known_pool_size = (
            pool_max_size if pool_max_size is not None else getattr(credentials, "pool_max_size", None)
        )
        try:
            install_security(
                app,
                CredentialVerifier(credentials, peppers, clock),
                max_concurrent_authentications=max_concurrent_authentications,
                auth_queue_wait_seconds=auth_queue_wait_seconds,
                pool_max_size=known_pool_size,
            )
        except ValueError:
            raise ServiceConfigurationError("Invalid authentication gate configuration.") from None
        if not app.state.authentication_gate.pool_isolation_verified:
            logger.warning(
                "authentication gate: connection pool size unknown; resource isolation not verified"
            )
        for router in v1_routers:
            app.include_router(router)

    # Startup guard: every /v1 route (and any WebSocket/Mount under /v1) must be secured.
    if find_unsecured_routes(app):
        raise ServiceConfigurationError("Unsecured /v1 route detected.")
    # Scopes must be declared on the route itself (contract §3.6), not via router-level dependencies.
    if find_scopes_outside_route_signature(app):
        raise ServiceConfigurationError("Route-level scope declaration required.")
    return app
