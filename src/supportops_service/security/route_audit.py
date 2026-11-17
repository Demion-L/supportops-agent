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

import re
from collections.abc import Collection

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from supportops_service.security.dependencies import SecuredRoute, scope_required_by

PROTECTED_PREFIX = "/v1"


def _under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _segment_regex(segment: str) -> re.Pattern[str]:
    out, pos = "", 0
    for m in re.finditer(r"\{[^}:]+(:[^}]+)?\}", segment):
        out += re.escape(segment[pos : m.start()]) + (".+" if m.group(1) else "[^/]+")
        pos = m.end()
    return re.compile(out + re.escape(segment[pos:]))


def _can_serve_protected(path: str, prefix: str) -> bool:
    """True if a route outside the literal prefix could still receive ``prefix`` requests:
    root mounts/catch-alls, or a parameterised first segment that can equal the prefix segment."""
    if path in ("", "/"):
        return True
    if "{" not in path:
        return False
    first = path.lstrip("/").split("/", 1)[0]
    return "{" in first and _segment_regex(first).fullmatch(prefix.strip("/")) is not None


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
        if not isinstance(path, str):
            continue
        if not _under_prefix(path, prefix):
            if _can_serve_protected(path, prefix) and not (
                isinstance(ctx.original_route, APIRoute) and isinstance(ctx.original_route, SecuredRoute)
            ):
                offenders.append(f"{type(ctx.original_route).__name__} {path or '/'} (shadows {prefix})")
            continue
        if path in public_paths:
            continue
        original = ctx.original_route
        if isinstance(original, APIRoute) and isinstance(original, SecuredRoute):
            continue
        offenders.append(f"{type(original).__name__} {path}")
    # A mount's own path may be reported without a trailing slash; sort for stable output.
    return sorted(offenders)


def find_scopes_outside_route_signature(
    app: FastAPI, *, prefix: str = PROTECTED_PREFIX
) -> list[str]:
    """Return ``"<METHODS> <path>"`` for protected routes whose scope sits in ``dependencies=``.

    ``SecuredRoute`` reads scopes from the endpoint signature when the route is built. A scope
    given through ``include_router(dependencies=...)`` or ``@router.get(dependencies=[...])`` is
    still enforced by the dependency itself, but only *after* the body is parsed (malformed body
    -> 422 instead of 403). Contract rule: declare ``Depends(require_scope(...))`` on the route.
    Uses only public FastAPI attributes (``APIRoute.dependencies``, ``Depends.dependency``).
    """
    offenders: list[str] = []
    for ctx in iter_route_contexts(app.routes):
        # The context exposes the *effective* dependencies (including include_router-level ones);
        # ``original_route.dependencies`` is the pre-include list and would miss them.
        original = ctx.original_route
        path = ctx.path
        if not (isinstance(original, APIRoute) and isinstance(path, str)):
            continue
        if not _under_prefix(path, prefix):
            continue
        for dep in getattr(ctx, "dependencies", None) or original.dependencies:
            if scope_required_by(getattr(dep, "dependency", None)) is not None:
                methods = ",".join(sorted(original.methods or ()))
                offenders.append(f"{methods} {path}")
                break
    return sorted(offenders)
