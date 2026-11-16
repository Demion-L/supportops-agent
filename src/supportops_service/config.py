"""Service configuration.

Settings are read explicitly via :meth:`ServiceSettings.from_env`; nothing is
read at import time. Invalid or incomplete configuration fails closed with a
:class:`ServiceConfigurationError` whose message never contains configuration
values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, SecretStr

ENV_PREFIX: Final = "SUPPORTOPS_"
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})


class ServiceConfigurationError(RuntimeError):
    """Raised when the service cannot be safely configured. Message is fixed text."""


def _parse_bool(raw: str | None, *, default: bool, name: str) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    # Never echo the offending value.
    raise ServiceConfigurationError(f"Invalid boolean for {ENV_PREFIX}{name}.")


class ServiceSettings(BaseModel):
    """Immutable service settings. Demo Mode is the default."""

    model_config = ConfigDict(frozen=True)

    demo_mode: bool = True
    docs_enabled: bool = False
    environment: str = "local"
    # Only populated in Live Mode; excluded from repr by SecretStr.
    llm_api_key: SecretStr | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceSettings:
        source = os.environ if env is None else env

        def get(name: str) -> str | None:
            return source.get(f"{ENV_PREFIX}{name}")

        demo_mode = _parse_bool(get("DEMO_MODE"), default=True, name="DEMO_MODE")
        docs_enabled = _parse_bool(get("DOCS_ENABLED"), default=False, name="DOCS_ENABLED")
        environment = (get("ENVIRONMENT") or "local").strip() or "local"

        # The LLM key is read only in Live Mode; Demo never touches credentials.
        api_key: SecretStr | None = None
        if not demo_mode:
            raw_key = get("LLM_API_KEY")
            if raw_key and raw_key.strip():
                api_key = SecretStr(raw_key.strip())

        return cls(
            demo_mode=demo_mode,
            docs_enabled=docs_enabled,
            environment=environment,
            llm_api_key=api_key,
        )
