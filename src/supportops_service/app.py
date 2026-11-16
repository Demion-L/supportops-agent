"""Application factory. Importing this module has no side effects."""

from __future__ import annotations

from fastapi import FastAPI

from supportops_service import __version__
from supportops_service.config import ServiceSettings
from supportops_service.errors import install_error_handlers
from supportops_service.llm_mode import resolve_llm_mode
from supportops_service.middleware import RequestContextMiddleware
from supportops_service.routers import health


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    """Build the app.

    ``settings=None`` reads the environment (once, here, not at import time).
    Raises ``ServiceConfigurationError`` if Live Mode is not fully configured.
    No SupportAgent, LLM client or network connection is created.
    """
    settings = settings if settings is not None else ServiceSettings.from_env()
    llm_mode = resolve_llm_mode(settings)  # fail-closed before anything is built

    docs = settings.docs_enabled
    app = FastAPI(
        title="SupportOps Service API",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.llm_mode = llm_mode

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    return app
