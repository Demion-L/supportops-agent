"""Service configuration.

Settings are read explicitly via :meth:`ServiceSettings.from_env`; nothing is
read at import time. Invalid or incomplete configuration fails closed with a
:class:`ServiceConfigurationError` whose message never contains configuration
values.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, SecretStr

ENV_PREFIX: Final = "HERMES_"
# Pre-HERMES prefix, still honoured (deprecated) so existing deployments and secrets keep working.
LEGACY_ENV_PREFIX: Final = "SUPPORTOPS_"
logger = logging.getLogger("hermes_service.config")
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})


class ServiceConfigurationError(RuntimeError):
    """Raised when the service cannot be safely configured. Message is fixed text."""


def read_env(source: Mapping[str, str], name: str) -> str | None:
    """Read ``HERMES_<name>`` with a deprecated ``SUPPORTOPS_<name>`` fallback.

    Blank values count as unset. If both variables carry a value and the values differ the
    configuration is ambiguous and fails closed (important for secrets such as the pepper:
    silently picking one would invalidate or mis-sign credentials). Values are never echoed.
    """
    current = source.get(f"{ENV_PREFIX}{name}")
    legacy = source.get(f"{LEGACY_ENV_PREFIX}{name}")
    current = current if current is not None and current.strip() else None
    legacy = legacy if legacy is not None and legacy.strip() else None
    if legacy is not None:
        if current is not None and current.strip() != legacy.strip():
            raise ServiceConfigurationError(
                f"Conflicting values for {ENV_PREFIX}{name} and {LEGACY_ENV_PREFIX}{name}."
            )
        logger.warning(
            "Deprecated environment variable %s%s; use %s%s.",
            LEGACY_ENV_PREFIX, name, ENV_PREFIX, name,
        )
    return current if current is not None else legacy


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
    # API-key verifier secret (HMAC pepper). No defaults; never in repr; read only here.
    pepper: SecretStr | None = None
    pepper_id: str | None = None  # id of the ACTIVE pepper (used for new credentials)
    # Retained older peppers, verify-only: "id=value,id=value". Never in repr; read only here.
    previous_peppers: SecretStr | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceSettings:
        source = os.environ if env is None else env

        def get(name: str) -> str | None:
            return read_env(source, name)

        demo_mode = _parse_bool(get("DEMO_MODE"), default=True, name="DEMO_MODE")
        docs_enabled = _parse_bool(get("DOCS_ENABLED"), default=False, name="DOCS_ENABLED")
        environment = (get("ENVIRONMENT") or "local").strip() or "local"

        # The LLM key is read only in Live Mode; Demo never touches credentials.
        api_key: SecretStr | None = None
        if not demo_mode:
            raw_key = get("LLM_API_KEY")
            if raw_key and raw_key.strip():
                api_key = SecretStr(raw_key.strip())

        raw_pepper = get("PEPPER")
        pepper = SecretStr(raw_pepper.strip()) if raw_pepper and raw_pepper.strip() else None
        raw_pepper_id = get("PEPPER_ID")
        pepper_id = raw_pepper_id.strip() if raw_pepper_id and raw_pepper_id.strip() else None

        raw_previous = get("PREVIOUS_PEPPERS")
        previous = SecretStr(raw_previous.strip()) if raw_previous and raw_previous.strip() else None

        return cls(
            demo_mode=demo_mode,
            docs_enabled=docs_enabled,
            environment=environment,
            llm_api_key=api_key,
            pepper=pepper,
            pepper_id=pepper_id,
            previous_peppers=previous,
        )
