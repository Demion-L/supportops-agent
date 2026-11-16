"""Audit that every protected ``/v1`` route is registered through ``SecuredRoute``.

``SecuredRoute`` is the only mechanism that guarantees "authenticate (401) -> scope (403) ->
body" ordering. A route registered on a plain ``APIRouter`` stays protected only if its author
remembered a dependency, and then answers a malformed body with 422 before 401/403. This module
turns "forgot the route class" into a failing test instead of a silent regression.

Pure inspection: no I/O, no environment, no request handling. Intended for tests (Alpha's and
Beta's ``create_app`` tests) and optionally for an application startup assertion.

Policies may differ per route (different scopes); this audit only checks the *mechanism*, never
which scope a route demands.
"""

from __future__ import annotations

from collections.abc import Collection

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from supportops_service.security.dependencies import SecuredRoute

PROTECTED_PREFIX = "/v1"


def _under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def find_unsecured_routes(
    app: FastAPI,
    *,
    prefix: str = PROTECTED_PREFIX,
    public_paths: Collection[str] = (),
) -> list[str]:
    """Return ``"<kind> <path>"`` for every route under ``prefix`` not using ``SecuredRoute``.

    Anything under the prefix that is not an HTTP ``APIRoute`` (WebSocket routes, mounted
    sub-applications, static files) is reported too: ``SecuredRoute`` cannot protect it.
    ``public_paths`` is an explicit, reviewed allow-list of exact effective paths.
    """
    offenders: list[str] = []
    for ctx in iter_route_contexts(app.routes):
        path = ctx.path
        if not isinstance(path, str) or not _under_prefix(path, prefix):
            continue
        if path in public_paths:
            continue
        original = ctx.original_route
        if isinstance(original, APIRoute) and isinstance(original, SecuredRoute):
            continue
        offenders.append(f"{type(original).__name__} {path}")
    # A mount's own path may be reported without a trailing slash; sort for stable output.
    return sorted(offenders)
