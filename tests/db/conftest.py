"""PostgreSQL integration harness.

Real PostgreSQL only (no SQLite, no mocks). ``HERMES_TEST_DATABASE_URL`` (legacy ``SUPPORTOPS_TEST_DATABASE_URL`` still accepted) must
point at a server where the user may CREATE DATABASE; every test gets its own
throwaway database. Without it, tests skip -- unless ``HERMES_REQUIRE_PG=1``
(CI), where absence or unreachability is a hard failure so nothing goes green
silently.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import psycopg.sql
import pytest
from psycopg.conninfo import make_conninfo
from pg_test_helpers import make_settings
from hermes_service.db.pool import DatabasePool

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
        pytest.fail(f"{REQUIRE_PG_ENV}=1 but PostgreSQL is not usable: {reason}", pytrace=False)
    pytest.skip(f"PostgreSQL not available: {reason}")


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = _env(TEST_DSN_ENV)
    if not dsn:
        _unavailable(f"{TEST_DSN_ENV} is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        _unavailable("server unreachable")  # never echo the driver message (DSN details)
    return dsn


@pytest.fixture
def database(admin_dsn) -> Iterator[str]:
    """DSN of a fresh, empty database, dropped afterwards."""
    yield from _fresh_database(admin_dsn)


@pytest.fixture(scope="module")
def module_database(admin_dsn) -> Iterator[str]:
    yield from _fresh_database(admin_dsn)


def _fresh_database(admin_dsn) -> Iterator[str]:
    name = f"supportops_t_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
    try:
        yield make_conninfo(admin_dsn, dbname=name)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


@pytest.fixture
def pool(database) -> Iterator[DatabasePool]:
    p = DatabasePool(make_settings(database, max_size=8))
    p.open()
    try:
        yield p
    finally:
        p.close()


@pytest.fixture
def migrated_pool(pool) -> DatabasePool:
    from hermes_service.db.migrate import migrate

    migrate(pool)
    return pool


@pytest.fixture(scope="module")
def _module_migrated_pool(module_database) -> Iterator[DatabasePool]:
    from hermes_service.db.migrate import migrate

    p = DatabasePool(make_settings(module_database, max_size=4))
    p.open()
    migrate(p)
    try:
        yield p
    finally:
        p.close()


@pytest.fixture
def schema_pool(_module_migrated_pool) -> Iterator[DatabasePool]:
    """Migrated schema shared per module; data wiped before each test (TRUNCATE skips row triggers)."""
    with _module_migrated_pool.connection() as c:
        c.execute("TRUNCATE runs, tickets, api_credentials")
    yield _module_migrated_pool
