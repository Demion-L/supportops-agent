"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Touches no dependency."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, str]:
    """Readiness (WP1): configuration is loaded. No database/worker is implied."""
    return {"status": "ready", "llm_mode": request.app.state.llm_mode.value}
