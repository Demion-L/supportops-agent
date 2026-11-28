"""No connection at import or construction; configuration fails closed. No PG needed."""

import importlib
import socket
import subprocess
import sys

import pytest
from pydantic import SecretStr

from hermes_service.config import ServiceConfigurationError
from hermes_service.db.pool import DatabasePool, DatabaseSettings


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def test_import_modules_does_not_connect():
    import hermes_service.db as db
    import hermes_service.db.migrate as migrate
    import hermes_service.db.pool as pool

    for m in (db, migrate, pool):
        importlib.reload(m)


def test_import_in_clean_interpreter_has_no_threads_or_sockets():
    code = (
        "import socket, threading\n"
        "def boom(*a, **k): raise SystemExit('connect at import')\n"
        "socket.socket.connect = boom\n"
        "before = threading.active_count()\n"
        "import hermes_service.db.pool, hermes_service.db.migrate\n"
        "assert threading.active_count() == before\n"
    )
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_constructing_pool_is_inert_and_closed_is_safe():
    pool = DatabasePool(DatabaseSettings(dsn=SecretStr("postgresql://u:p@127.0.0.1:1/x")))
    assert pool.closed
    pool.close()
    pool.close()  # idempotent


def test_settings_require_dsn():
    with pytest.raises(ServiceConfigurationError):
        DatabaseSettings.from_env({})
    with pytest.raises(ServiceConfigurationError):
        DatabaseSettings.from_env({"HERMES_DATABASE_URL": "   "})


@pytest.mark.parametrize(
    "overrides",
    [{"min_size": 0}, {"min_size": 5, "max_size": 2}, {"acquire_timeout": 0}, {"statement_timeout_ms": -1}],
)
def test_settings_reject_invalid_values_without_echoing_dsn(overrides):
    secret = "postgresql://user:SECRET-MARKER-123@h/db"
    with pytest.raises(ServiceConfigurationError) as exc:
        DatabaseSettings(dsn=SecretStr(secret), **overrides)
    assert "SECRET-MARKER-123" not in str(exc.value)


def test_settings_repr_hides_dsn():
    s = DatabaseSettings(dsn=SecretStr("postgresql://user:SECRET-MARKER-123@h/db"))
    assert "SECRET-MARKER-123" not in repr(s)
    assert "SECRET-MARKER-123" not in str(s)
