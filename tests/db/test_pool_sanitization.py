"""Driver errors never leak through the public storage errors (no database needed).

A synthetic secret marker is placed in the text of every simulated driver error; it must
not be reachable from ``str``/``repr``/``args``/``__cause__``/``__context__`` of what the
pool raises, nor from the unified HTTP error body.
"""

from __future__ import annotations

import logging

import psycopg
import pytest
from psycopg_pool import PoolClosed, PoolTimeout
from pydantic import SecretStr
from hermes_service.db.errors import StorageUnavailable
from hermes_service.db.pool import DatabasePool, DatabaseSettings

MARKER = "SYNTHETIC_SECRET_MARKER_7f3a9c"
FIXED = "Storage is unavailable."


def _pool() -> DatabasePool:
    return DatabasePool(DatabaseSettings(dsn=SecretStr("postgresql://user:pw@127.0.0.1:1/db")))


def _assert_sanitised(error: BaseException) -> None:
    assert type(error) is StorageUnavailable
    assert str(error) == FIXED and error.args == (FIXED,)
    assert MARKER not in repr(error) and MARKER not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True or error.__context__ is None


class _FailsOnEnter:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self):
        raise self._exc

    def __exit__(self, *exc_info) -> bool:
        return False


class _Healthy:
    def __enter__(self):
        return object()

    def __exit__(self, *exc_info) -> bool:
        return False


@pytest.mark.parametrize(
    "driver_error",
    [
        psycopg.OperationalError(MARKER),
        PoolTimeout(MARKER),
        PoolClosed(MARKER),
    ],
    ids=["operational", "pool-timeout", "pool-closed"],
)
def test_acquire_failure_is_sanitised(driver_error):
    pool = _pool()
    pool._pool.connection = lambda **_: _FailsOnEnter(driver_error)
    with pytest.raises(StorageUnavailable) as exc:
        with pool.connection():
            pytest.fail("body must not run")
    _assert_sanitised(exc.value)


def test_lost_connection_in_body_is_sanitised():
    pool = _pool()
    pool._pool.connection = lambda **_: _Healthy()
    with pytest.raises(StorageUnavailable) as exc:
        with pool.connection():
            raise psycopg.OperationalError(MARKER)
    _assert_sanitised(exc.value)


def test_open_failure_is_sanitised(caplog):
    pool = _pool()

    def boom(**_):
        raise psycopg.OperationalError(MARKER)

    pool._pool.open = boom
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(StorageUnavailable) as exc:
            pool.open()
    _assert_sanitised(exc.value)
    assert MARKER not in caplog.text


def test_non_availability_errors_still_propagate_unchanged():
    """Fail-closed semantics unchanged: only availability errors are converted; anything else
    (e.g. a constraint violation) reaches the caller, who must interpret it."""
    pool = _pool()
    pool._pool.connection = lambda **_: _Healthy()
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pool.connection():
            raise psycopg.errors.UniqueViolation(MARKER)


def test_database_error_is_never_a_success():
    pool = _pool()
    pool._pool.connection = lambda **_: _FailsOnEnter(psycopg.OperationalError(MARKER))
    with pytest.raises(StorageUnavailable):
        pool.check()


def test_http_layer_has_no_text_path_from_storage_error():
    """The unified handler never reads ``str(exc)``: even if a driver message were present it
    cannot reach the body. (No StorageUnavailable->503 handler exists yet: contract only.)"""
    from fastapi.testclient import TestClient
    from hermes_service.app import create_app
    from hermes_service.config import ServiceSettings

    app = create_app(ServiceSettings())

    @app.get("/boom")
    def boom():
        raise psycopg.OperationalError(MARKER)

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert MARKER not in response.text and MARKER not in str(response.headers)
