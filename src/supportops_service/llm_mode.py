"""LLM mode selection (WP1: flag and validation only).

No LLM client is created here, in either mode. Demo Mode guarantees no
network clients and no credentials. Live Mode is validated fail-closed and is
not invoked in WP1.
"""

from __future__ import annotations

from enum import StrEnum

from supportops_service.config import ServiceConfigurationError, ServiceSettings


class LlmMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


def resolve_llm_mode(settings: ServiceSettings) -> LlmMode:
    """Return the effective mode, or raise if Live Mode is not fully configured."""
    if settings.demo_mode:
        return LlmMode.DEMO
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        raise ServiceConfigurationError(
            "Live mode requires explicit LLM configuration; refusing to start."
        )
    return LlmMode.LIVE
