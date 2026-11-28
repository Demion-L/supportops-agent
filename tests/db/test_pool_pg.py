"""Pool lifecycle, failure mapping and secret hygiene against real PostgreSQL."""

import logging
import time

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from hermes_service.db.errors import StorageUnavailable
from hermes_service.db.migrate import migrate
from hermes_service.db.pool import DatabasePool

from pg_test_helpers import make_settings

pytestmark = pytest.mark.postgres

MARKER = "PWD-MARKER-Zq81"


def _backends(admin_dsn, app):
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        return c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name = %s", (app,)).fetchone()[0]


def test_pool_serves_queries_with_configured_session(pool):
    with pool.connection() as c:
        row = c.execute(
            "SELECT current_setting('statement_timeout') s, current_setting('lock_timeout') l, "
            "current_setting('idle_in_transaction_session_timeout') i, current_setting('application_name') a"
        ).fetchone()
    assert row == {"s": "5s", "l": "2s", "i": "10s", "a": "hermes-service"}
    pool.check()


def test_pool_close_releases_all_connections(database, admin_dsn):
    app = "supportops-close-test"
    p = DatabasePool(make_settings(database, min_size=2, max_size=3, application_name=app))
    p.open()
    with p.connection() as c:
        c.execute("SELECT 1")
    assert _backends(admin_dsn, app) >= 2
    p.close()
    assert p.closed
    deadline = time.time() + 5
    while _backends(admin_dsn, app) and time.time() < deadline:
        time.sleep(0.1)
    assert _backends(admin_dsn, app) == 0
    with pytest.raises(StorageUnavailable):
        with p.connection():
            pass
    p.close()  # idempotent


def test_connection_returned_to_pool_after_body_error(pool):
    for _ in range(20):  # more iterations than max_size: a leak would exhaust the pool
        with pytest.raises(psycopg.errors.UndefinedTable):
            with pool.connection() as c:
                c.execute("SELECT * FROM no_such_table")
    pool.check()


def test_pool_exhaustion_is_storage_unavailable(database):
    p = DatabasePool(make_settings(database, min_size=1, max_size=1, acquire_timeout=0.3))
    p.open()
    try:
        with p.connection():
            with pytest.raises(StorageUnavailable):
                with p.connection():
                    pass
        p.check()  # recovers once released
    finally:
        p.close()


def test_statement_timeout_is_not_a_hang(database):
    p = DatabasePool(make_settings(database, statement_timeout_ms=200))
    p.open()
    try:
        started = time.time()
        with pytest.raises(StorageUnavailable):
            with p.connection() as c:
                c.execute("SELECT pg_sleep(5)")
        assert time.time() - started < 3
        p.check()
    finally:
        p.close()


def test_unreachable_server_fails_fast_with_fixed_error(database):
    dsn = make_conninfo(database, host="127.0.0.1", port=1)  # nothing listens on port 1
    p = DatabasePool(make_settings(dsn, open_timeout=2, connect_timeout=1))
    started = time.time()
    with pytest.raises(StorageUnavailable) as exc:
        p.open()
    assert time.time() - started < 8
    assert str(exc.value) == "Storage is unavailable."
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert p.closed


def test_bad_credentials_fail_closed_and_leak_nothing(database, caplog):
    caplog.set_level(logging.DEBUG)
    dsn = make_conninfo(database, password=MARKER)
    user = conninfo_to_dict(database).get("user", "")
    p = DatabasePool(make_settings(dsn, open_timeout=2))
    with pytest.raises(StorageUnavailable) as exc:
        p.open()
    blob = "\n".join([str(exc.value), repr(exc.value), repr(p), repr(p._settings), caplog.text])
    assert MARKER not in blob
    assert dsn not in blob
    if user:
        assert f"user={user}" not in blob  # no DSN fragments


def test_no_secrets_in_logs_during_normal_operation(database, caplog):
    caplog.set_level(logging.DEBUG)
    dsn = make_conninfo(database, application_name="x")  # real working DSN
    secret = conninfo_to_dict(dsn).get("password") or ""
    p = DatabasePool(make_settings(dsn))
    p.open()
    migrate(p)
    p.check()
    p.close()
    if secret:
        assert secret not in caplog.text
    assert dsn not in caplog.text
