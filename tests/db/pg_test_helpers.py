from pydantic import SecretStr

from hermes_service.db.pool import DatabaseSettings


def make_settings(dsn: str, **overrides) -> DatabaseSettings:
    return DatabaseSettings(dsn=SecretStr(dsn), **overrides)
