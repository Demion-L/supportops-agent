"""Managed synchronous PostgreSQL connection pool (psycopg 3 + psycopg-pool).

Nothing connects at import or construction time; the pool is opened and closed
explicitly. Driver messages (which may contain host details) never leave this
module: failures surface as :class:`StorageUnavailable` with fixed text.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout
from pydantic import SecretStr

from supportops_service.config import ENV_PREFIX, ServiceConfigurationError
from supportops_service.db.errors import StorageUnavailable

logger = logging.getLogger("supportops_service.db")

DATABASE_URL_ENV: Final = f"{ENV_PREFIX}DATABASE_URL"


@dataclass(frozen=True)
class DatabaseSettings:
    """Pool settings. The DSN is a secret and never appears in ``repr``."""

    dsn: SecretStr = field(repr=False)
    min_size: int = 1
    max_size: int = 4
    acquire_timeout: float = 5.0  # wait for a pooled connection, seconds
    open_timeout: float = 10.0  # wait for the pool to become ready, seconds
    connect_timeout: int = 5  # libpq connect timeout, seconds
    statement_timeout_ms: int = 5000
    lock_timeout_ms: int = 2000
    idle_in_transaction_timeout_ms: int = 10000
    application_name: str = "supportops-service"

    def __post_init__(self) -> None:
        if not isinstance(self.dsn, SecretStr) or not self.dsn.get_secret_value().strip():
            raise ServiceConfigurationError("Database DSN is missing.")
        if not 1 <= self.min_size <= self.max_size:
            raise ServiceConfigurationError("Invalid pool size.")
        for name in (
            "acquire_timeout",
            "open_timeout",
            "connect_timeout",
            "statement_timeout_ms",
            "lock_timeout_ms",
            "idle_in_transaction_timeout_ms",
        ):
            if getattr(self, name) <= 0:
                raise ServiceConfigurationError(f"Invalid value for {name}.")
        if not self.application_name.isascii() or not self.application_name.strip():
            raise ServiceConfigurationError("Invalid application name.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabaseSettings:
        source = os.environ if env is None else env
        raw = source.get(DATABASE_URL_ENV)
        if raw is None or not raw.strip():
            raise ServiceConfigurationError(f"{DATABASE_URL_ENV} is required.")
        return cls(dsn=SecretStr(raw.strip()))


class DatabasePool:
    """Explicitly opened/closed pool of autocommit connections returning dict rows."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        options = (
            f"-c statement_timeout={settings.statement_timeout_ms} "
            f"-c lock_timeout={settings.lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={settings.idle_in_transaction_timeout_ms}"
        )
        # open=False: constructing the pool performs no I/O and starts no threads.
        self._pool = ConnectionPool(
            conninfo=settings.dsn.get_secret_value(),
            min_size=settings.min_size,
            max_size=settings.max_size,
            timeout=settings.acquire_timeout,
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "connect_timeout": settings.connect_timeout,
                "application_name": settings.application_name,
                "options": options,
            },
        )

    def __repr__(self) -> str:
        return f"<DatabasePool closed={self._pool.closed}>"

    @property
    def closed(self) -> bool:
        return self._pool.closed

    def open(self) -> None:
        """Open the pool and wait until it has a working connection."""
        try:
            self._pool.open(wait=True, timeout=self._settings.open_timeout)
        except (PoolTimeout, psycopg.Error, PoolClosed):
            self._pool.close()
            logger.warning("database pool failed to open")
            raise StorageUnavailable() from None

    def close(self) -> None:
        """Close the pool and stop its workers. Idempotent."""
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        """Borrow a connection. Acquisition failures and lost connections become
        :class:`StorageUnavailable`; other errors from the body propagate unchanged
        (constraint violations are the caller's to interpret)."""
        stack = ExitStack()
        try:
            conn = stack.enter_context(self._pool.connection(timeout=self._settings.acquire_timeout))
        except (PoolTimeout, PoolClosed, psycopg.OperationalError):
            raise StorageUnavailable() from None
        try:
            with stack:
                yield conn
        except psycopg.OperationalError:
            raise StorageUnavailable() from None

    def check(self) -> None:
        """Cheap liveness probe; raises :class:`StorageUnavailable`."""
        with self.connection() as conn:
            conn.execute("SELECT 1")
