"""Unit tests of PostgresCredentialRepository with a fake pool (no database).

The real-PostgreSQL behaviour is covered by tests/security_pg (those tests are NOT replaced by
these). Here: SQL hygiene, row mapping, failure sanitization, and fail-closed HTTP behaviour.
"""

from __future__ import annotations

import ast
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from supportops_service.interfaces import TenantContext
from supportops_service.repositories import credentials as repo_module
from supportops_service.repositories.credentials import (
    CredentialStoreError,
    PostgresCredentialRepository,
)
from supportops_service.interfaces import Scope
from supportops_service.security.credentials import (
    CredentialVerifier,
    PepperSet,
    compute_key_hash,
    generate_api_key,
)
from supportops_service.security.dependencies import install_security, require_scope

from conftest import NOW, PEPPER, FixedClock, bearer

DSN_MARKER = "postgresql://svc:S3CRET-DSN-PW@db.internal:5432/supportops"


class FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, pool):
        self._pool = pool

    def execute(self, sql, params=None):
        self._pool.calls.append((sql, params))
        if self._pool.fail_with is not None:
            raise self._pool.fail_with
        if sql.lstrip().upper().startswith("UPDATE"):
            return FakeCursor(None)
        return FakeCursor(self._pool.rows.get(params[0]))


class FakePool:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.fail_with: Exception | None = None
        self.fail_acquire: Exception | None = None

    @contextmanager
    def connection(self):
        if self.fail_acquire is not None:
            raise self.fail_acquire
        yield FakeConn(self)


def _row(prefix: str, key_hash, **over) -> dict:
    row = {
        "credential_id": "11111111-1111-4111-8111-111111111111",
        "tenant_id": "tenant-a",
        "key_prefix": prefix,
        "key_hash": key_hash,
        "pepper_id": "p1",
        "scopes": ["tickets:read"],
        "expires_at": None,
        "revoked_at": None,
    }
    row.update(over)
    return row


@pytest.fixture
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def repo(pool: FakePool) -> PostgresCredentialRepository:
    return PostgresCredentialRepository(pool)


def test_lookup_binds_the_prefix_and_uses_the_constant_statement(repo, pool):
    key = generate_api_key()
    pool.rows[key.prefix] = _row(key.prefix, b"\x00" * 32)
    assert repo.get_by_prefix(key.prefix) is not None
    ((sql, params),) = pool.calls
    assert sql is repo_module._SELECT_BY_PREFIX
    assert params == (key.prefix,)
    assert "%s" in sql and key.prefix not in sql


@pytest.mark.parametrize(
    "prefix",
    [
        "' OR '1'='1",
        "abcdefghijk'; DROP TABLE api_credentials;--",
        "abcdefghijkl\x00",
        "ABCDEFGHIJKL",  # not the stored (lower-case) alphabet
        "abcdefghijk1",  # '1' is not base32
        "abcdefghijk",  # too short
        "abcdefghijklm",  # too long
        "",
        None,
        b"abcdefghijkl",
    ],
)
def test_malformed_prefix_never_reaches_the_database(repo, pool, prefix):
    assert repo.get_by_prefix(prefix) is None  # type: ignore[arg-type]
    assert pool.calls == []


def test_unknown_prefix_returns_none(repo):
    assert repo.get_by_prefix("abcdefghijkl") is None


def test_row_mapping_converts_postgres_types(repo, pool):
    prefix = "abcdefghijkl"
    expires = datetime(2027, 1, 1, tzinfo=UTC)
    pool.rows[prefix] = _row(
        prefix,
        memoryview(b"\x01" * 32),
        scopes=["tickets:read", "bogus:scope"],
        expires_at=expires,
    )
    record = repo.get_by_prefix(prefix)
    assert record is not None
    assert type(record.key_hash) is bytes and record.key_hash == b"\x01" * 32
    # Repository keeps raw strings; the closed Scope filter belongs to the security layer.
    assert record.scopes == frozenset({"tickets:read", "bogus:scope"})
    assert record.expires_at == expires and record.revoked_at is None
    assert "\x01" not in repr(record)  # key_hash has repr=False


@pytest.mark.parametrize("bad", [{"scopes": None}, {"scopes": "tickets:read"}, {"scopes": 7}])
def test_unexpected_row_shape_is_a_store_error_not_a_record(repo, pool, bad):
    prefix = "abcdefghijkl"
    pool.rows[prefix] = _row(prefix, b"\x00" * 32, **bad)
    with pytest.raises(CredentialStoreError):
        repo.get_by_prefix(prefix)


def test_missing_column_is_a_store_error(repo, pool):
    prefix = "abcdefghijkl"
    row = _row(prefix, b"\x00" * 32)
    del row["pepper_id"]
    pool.rows[prefix] = row
    with pytest.raises(CredentialStoreError):
        repo.get_by_prefix(prefix)


@pytest.mark.parametrize("where", ["execute", "acquire"])
def test_driver_failures_are_sanitized(repo, pool, caplog, where):
    caplog.set_level(logging.DEBUG)
    boom = RuntimeError(f"connection to {DSN_MARKER} failed: SELECT key_hash FROM api_credentials")
    if where == "execute":
        pool.fail_with = boom
    else:
        pool.fail_acquire = boom
    with pytest.raises(CredentialStoreError) as caught:
        repo.get_by_prefix("abcdefghijkl")
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.__suppress_context__ or exc.__context__ is None
    assert DSN_MARKER not in str(exc) and "SELECT" not in str(exc) and "S3CRET" not in str(exc)
    assert "S3CRET" not in caplog.text and "api_credentials" not in caplog.text
    assert "RuntimeError" in caplog.text  # class name only is logged


def test_touch_failure_is_sanitized_too(repo, pool, caplog):
    caplog.set_level(logging.DEBUG)
    pool.fail_with = RuntimeError(DSN_MARKER)
    with pytest.raises(CredentialStoreError) as caught:
        repo.touch_last_used("11111111-1111-4111-8111-111111111111")
    assert caught.value.__context__ is None
    assert "S3CRET" not in caplog.text and "S3CRET" not in str(caught.value)


def test_touch_binds_parameters_and_is_throttled_in_sql(pool):
    repo = PostgresCredentialRepository(pool, touch_min_interval_seconds=30)
    repo.touch_last_used("11111111-1111-4111-8111-111111111111")
    ((sql, params),) = pool.calls
    assert sql is repo_module._TOUCH_LAST_USED
    assert params == ("11111111-1111-4111-8111-111111111111", 30.0)
    assert "last_used_at < now()" in sql


def test_negative_throttle_is_rejected_at_construction(pool):
    with pytest.raises(ValueError):
        PostgresCredentialRepository(pool, touch_min_interval_seconds=-1)


def test_repr_exposes_nothing(repo):
    assert repr(repo) == "PostgresCredentialRepository()"


def test_sql_is_constant_and_parametrized_only():
    """No f-string / %-format / .format / concatenation feeds ``execute`` (static check)."""
    tree = ast.parse(Path(repo_module.__file__).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)], "f-string in module"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute":
            first = node.args[0]
            assert isinstance(first, ast.Name) and first.id.startswith("_"), "SQL must be a constant"
    assert "%s" in repo_module._SELECT_BY_PREFIX and "%s" in repo_module._TOUCH_LAST_USED


# ---- end-to-end through the real verifier and FastAPI -------------------------------------------


def _client_for(repo: PostgresCredentialRepository):
    from fastapi import FastAPI

    app = FastAPI()
    install_security(app, CredentialVerifier(repo, PepperSet({"p1": PEPPER}), FixedClock(NOW)))

    @app.get("/v1/probe")
    def probe(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"tenant_id": ctx.tenant_id}

    return TestClient(app, raise_server_exceptions=False)


def _issue(pool: FakePool, **over) -> str:
    key = generate_api_key()
    pool.rows[key.prefix] = _row(
        key.prefix, compute_key_hash(PEPPER, key.prefix, key.secret), **over
    )
    return key.render()


def test_http_valid_credential_resolves_and_touches_once_after_verification(pool, repo):
    token = _issue(pool)
    response = _client_for(repo).get("/v1/probe", headers=bearer(token))
    assert response.status_code == 200 and response.json() == {"tenant_id": "tenant-a"}
    kinds = [sql.split()[0] for sql, _ in pool.calls]
    assert kinds == ["SELECT", "UPDATE"]  # touch strictly after the lookup, once


def test_http_failed_checks_never_touch_last_used(pool, repo):
    client = _client_for(repo)
    revoked = _issue(pool, revoked_at=datetime(2026, 1, 1, tzinfo=UTC))
    expired = _issue(pool, expires_at=datetime(2026, 1, 1, tzinfo=UTC))
    bad_pepper = _issue(pool, pepper_id="unknown")
    wrong_secret = generate_api_key()
    pool.rows[wrong_secret.prefix] = _row(wrong_secret.prefix, b"\x07" * 32)
    for token in (revoked, expired, bad_pepper, wrong_secret.render()):
        assert client.get("/v1/probe", headers=bearer(token)).status_code == 401
    assert all(sql.startswith("SELECT") for sql, _ in pool.calls)


def test_http_touch_failure_does_not_change_an_already_made_decision(pool, repo):
    token = _issue(pool)
    client = _client_for(repo)
    original = FakeConn.execute

    def failing_touch(self, sql, params=None):
        if sql.startswith("UPDATE"):
            raise RuntimeError(DSN_MARKER)
        return original(self, sql, params)

    FakeConn.execute = failing_touch  # type: ignore[method-assign]
    try:
        response = client.get("/v1/probe", headers=bearer(token))
    finally:
        FakeConn.execute = original  # type: ignore[method-assign]
    assert response.status_code == 200
    assert "S3CRET" not in response.text


@pytest.mark.parametrize("where", ["execute", "acquire"])
def test_http_store_failure_is_503_and_leaks_nothing(pool, repo, where):
    token = _issue(pool)
    boom = RuntimeError(DSN_MARKER)
    if where == "execute":
        pool.fail_with = boom
    else:
        pool.fail_acquire = boom
    response = _client_for(repo).get("/v1/probe", headers=bearer(token))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "S3CRET" not in response.text and "db.internal" not in response.text
    assert token.split(".")[2] not in response.text
