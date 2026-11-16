from pydantic import SecretStr

from supportops_service.db.pool import DatabaseSettings


def make_settings(dsn: str, **overrides) -> DatabaseSettings:
    return DatabaseSettings(dsn=SecretStr(dsn), **overrides)
