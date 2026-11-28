"""Real-PostgreSQL harness for the credential repository (Alpha).

Needs Beta's ``hermes_service.db`` (pool + migrations) and a server reachable through
``HERMES_TEST_DATABASE_URL`` (legacy ``SUPPORTOPS_TEST_DATABASE_URL`` still accepted) where the user may CREATE DATABASE / CREATE ROLE. Every test gets
a throwaway database. Without them the tests skip -- unless ``HERMES_REQUIRE_PG=1`` (CI /
Integration Gate), where absence is a hard FAILURE. A skipped run is NOT a verification.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

TEST_DSN_ENV = "HERMES_TEST_DATABASE_URL"
REQUIRE_PG_ENV = "HERMES_REQUIRE_PG"


def _env(name: str) -> str:
    """HERMES_* variable with the deprecated SUPPORTOPS_* fallback (test infrastructure)."""
    value = os.environ.get(name, "").strip()
    if not value and name.startswith("HERMES_"):
        value = os.environ.get("SUPPORTOPS_" + name[len("HERMES_"):], "").strip()
    return value


def _required() -> bool:
    return _env(REQUIRE_PG_ENV).lower() in {"1", "true", "yes", "on"}


def _unavailable(reason: str):
    if _required():
        pytest.fail(f"{REQUIRE_PG_ENV}=1 but PostgreSQL tests cannot run: {reason}", pytrace=False)
    pytest.skip(f"PostgreSQL not available: {reason}")


@pytest.fixture(scope="session")
def pg():
    """Imports are deferred so a tree without Beta's db package skips/fails explicitly."""
    try:
        import psycopg
        import psycopg.sql
        from psycopg.conninfo import make_conninfo
        from pydantic import SecretStr

        from hermes_service.db.migrate import migrate
        from hermes_service.db.pool import DatabasePool, DatabaseSettings
    except ImportError:
        _unavailable("psycopg or hermes_service.db (Beta persistence foundation) is missing")

    class _PG:
        pass

    _PG.psycopg, _PG.sql, _PG.make_conninfo = psycopg, psycopg.sql, make_conninfo
    _PG.SecretStr, _PG.migrate = SecretStr, migrate
    _PG.DatabasePool, _PG.DatabaseSettings = DatabasePool, DatabaseSettings
    return _PG


@pytest.fixture(scope="session")
def admin_dsn(pg) -> str:
    dsn = _env(TEST_DSN_ENV)
    if not dsn:
        _unavailable(f"{TEST_DSN_ENV} is not set")
    try:
        with pg.psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute("SELECT 1")
    except pg.psycopg.Error:
        _unavailable("server unreachable")  # never echo the driver message
    return dsn


@pytest.fixture
def database(pg, admin_dsn) -> Iterator[str]:
    name = f"supportops_sec_{uuid.uuid4().hex[:16]}"
    with pg.psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(pg.sql.SQL("CREATE DATABASE {}").format(pg.sql.Identifier(name)))
    try:
        yield pg.make_conninfo(admin_dsn, dbname=name)
    finally:
        with pg.psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                pg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pg.sql.Identifier(name))
            )


@pytest.fixture
def make_pool(pg, database):
    """Factory: ``make_pool(dsn=None, **settings)`` -> opened pool (closed on teardown)."""
    opened = []

    def factory(dsn: str | None = None, **settings):
        settings.setdefault("max_size", 8)
        pool = pg.DatabasePool(pg.DatabaseSettings(dsn=pg.SecretStr(dsn or database), **settings))
        pool.open()
        opened.append(pool)
        return pool

    yield factory
    for pool in opened:
        pool.close()


@pytest.fixture
def pool(make_pool, pg):
    p = make_pool()
    pg.migrate(p)
    return p
