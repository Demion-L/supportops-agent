"""PostgresCredentialRepository + CredentialVerifier against a REAL PostgreSQL.

No mocks of the database. These tests are the PostgreSQL verification of the Security
Foundation; if they skip, the integration is NOT verified.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from supportops_service.interfaces import TenantContext
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

pytestmark = pytest.mark.postgres

PEPPER = b"pg-test-pepper-0123456789abcdef-A"
PEPPER_2 = b"pg-test-pepper-0123456789abcdef-B"
PEPPERS = PepperSet({"p1": PEPPER, "p2": PEPPER_2})
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now=NOW):
        self.current = now

    def now(self):
        return self.current


def insert_credential(pool, *, tenant="tenant-a", scopes=("tickets:read",), pepper_id="p1",
                      pepper=PEPPER, expires_at=None, revoked_at=None, secret_hash=None):
    key = generate_api_key()
    credential_id = str(uuid.uuid4())
    key_hash = secret_hash or compute_key_hash(pepper, key.prefix, key.secret)
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO api_credentials (credential_id, tenant_id, key_prefix, key_hash, "
            "pepper_id, scopes, expires_at, revoked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (credential_id, tenant, key.prefix, key_hash, pepper_id, list(scopes), expires_at,
             revoked_at),
        )
    return key.render(), credential_id, key.prefix


def last_used(pool, credential_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT last_used_at FROM api_credentials WHERE credential_id = %s", (credential_id,)
        ).fetchone()["last_used_at"]


@pytest.fixture
def repo(pool):
    return PostgresCredentialRepository(pool, touch_min_interval_seconds=0)


@pytest.fixture
def verifier(repo):
    return CredentialVerifier(repo, PEPPERS, Clock())


def bearer(token):
    return [f"Bearer {token}"]


def test_valid_credential_round_trips_types_and_identity(pool, repo, verifier):
    token, cred_id, prefix = insert_credential(pool, scopes=("tickets:read", "bogus:scope"))
    record = repo.get_by_prefix(prefix)
    assert record is not None
    assert type(record.key_hash) is bytes and len(record.key_hash) == 32
    assert record.credential_id == cred_id and record.tenant_id == "tenant-a"
    assert record.pepper_id == "p1" and record.expires_at is None and record.revoked_at is None
    ctx = verifier.authenticate(bearer(token))
    assert ctx.tenant_id == "tenant-a" and ctx.credential_id == cred_id
    assert ctx.scopes == frozenset({Scope.TICKETS_READ})  # unknown scope string dropped


def test_timestamps_are_timezone_aware(pool, repo):
    expires = NOW + timedelta(days=1)
    _, _, prefix = insert_credential(pool, expires_at=expires, revoked_at=NOW)
    record = repo.get_by_prefix(prefix)
    assert record.expires_at == expires and record.expires_at.utcoffset() is not None
    assert record.revoked_at == NOW


def test_wrong_secret_unknown_prefix_unknown_pepper_are_all_401(pool, verifier):
    from supportops_service.security.errors import Unauthenticated

    good, _, prefix = insert_credential(pool)
    wrong_secret = f"sok1.{prefix}.{'A' * 43}"
    unknown_prefix = generate_api_key().render()
    token, _, _ = insert_credential(pool, pepper_id="ghost")  # pepper not configured
    for bad in (wrong_secret, unknown_prefix, token):
        with pytest.raises(Unauthenticated):
            verifier.authenticate(bearer(bad))
    assert verifier.authenticate(bearer(good)).tenant_id == "tenant-a"


def test_revoked_and_expired_are_checked_on_every_request(pool, verifier):
    from supportops_service.security.errors import Unauthenticated

    token, cred_id, _ = insert_credential(pool, expires_at=NOW + timedelta(hours=1))
    assert verifier.authenticate(bearer(token))
    with pool.connection() as conn:
        conn.execute("UPDATE api_credentials SET revoked_at = now() WHERE credential_id = %s", (cred_id,))
    with pytest.raises(Unauthenticated):  # no cache: effective on the very next request
        verifier.authenticate(bearer(token))

    expired, _, _ = insert_credential(pool, expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(Unauthenticated):
        verifier.authenticate(bearer(expired))
    edge, _, _ = insert_credential(pool, expires_at=NOW)  # now >= expires_at -> expired
    with pytest.raises(Unauthenticated):
        verifier.authenticate(bearer(edge))


def test_hash_stored_as_bytes_is_the_only_secret_comparison(pool, verifier):
    from supportops_service.security.errors import Unauthenticated

    key = generate_api_key()
    # Row whose hash is the raw secret bytes instead of HMAC: must not authenticate.
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO api_credentials (credential_id, tenant_id, key_prefix, key_hash, "
            "pepper_id, scopes) VALUES (%s, 'tenant-a', %s, %s, 'p1', %s)",
            (str(uuid.uuid4()), key.prefix, key.secret.encode()[:32].ljust(32, b"0"), ["tickets:read"]),
        )
    with pytest.raises(Unauthenticated):
        verifier.authenticate(bearer(key.render()))


@pytest.mark.parametrize(
    "payload",
    ["' OR '1'='1", "abcdefghijk'; DROP TABLE api_credentials;--", "abcdefghijkl' OR 1=1 --"],
)
def test_sql_injection_through_the_prefix_is_inert(pool, repo, payload):
    insert_credential(pool)
    assert repo.get_by_prefix(payload) is None
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM api_credentials").fetchone()["n"] == 1


def test_touch_updates_only_after_success_and_is_throttled(pool):
    clock_verifier = lambda r: CredentialVerifier(r, PEPPERS, Clock())  # noqa: E731
    throttled = PostgresCredentialRepository(pool, touch_min_interval_seconds=3600)
    token, cred_id, _ = insert_credential(pool)
    assert last_used(pool, cred_id) is None
    from supportops_service.security.errors import Unauthenticated

    with pytest.raises(Unauthenticated):
        clock_verifier(throttled).authenticate(bearer(f"sok1.{generate_api_key().prefix}.{'A' * 43}"))
    assert last_used(pool, cred_id) is None  # failed attempts never touch
    clock_verifier(throttled).authenticate(bearer(token))
    first = last_used(pool, cred_id)
    assert first is not None
    clock_verifier(throttled).authenticate(bearer(token))
    assert last_used(pool, cred_id) == first  # inside the throttle window: no second write


def test_touch_is_throttled_but_resumes_after_the_window(pool):
    repo = PostgresCredentialRepository(pool, touch_min_interval_seconds=3600)
    _, cred_id, _ = insert_credential(pool)
    with pool.connection() as conn:
        conn.execute(
            "UPDATE api_credentials SET last_used_at = now() - interval '2 hours' WHERE credential_id = %s",
            (cred_id,),
        )
    old = last_used(pool, cred_id)
    repo.touch_last_used(cred_id)
    assert last_used(pool, cred_id) > old


def test_touch_failure_does_not_turn_into_an_error_for_a_verified_credential(pool, make_pool):
    token, _, _ = insert_credential(pool)
    victim = make_pool()  # separate pool we can break independently

    class TouchBreaksPool:
        """Real PG for the lookup; the pool is closed right after the lookup completes."""

        def __init__(self):
            self.calls = 0

        def connection(self):
            self.calls += 1
            if self.calls > 1:
                victim.close()
            return victim.connection()

    repo = PostgresCredentialRepository(TouchBreaksPool(), touch_min_interval_seconds=0)
    ctx = CredentialVerifier(repo, PEPPERS, Clock()).authenticate(bearer(token))
    assert ctx.tenant_id == "tenant-a"  # lookup + verification completed; touch failed silently


def test_closed_pool_is_a_store_error_with_fixed_text(pool, make_pool, caplog):
    caplog.set_level(logging.DEBUG)
    p = make_pool()
    repo = PostgresCredentialRepository(p)
    p.close()
    with pytest.raises(CredentialStoreError) as caught:
        repo.get_by_prefix("abcdefghijkl")
    assert caught.value.__context__ is None
    assert str(caught.value) == "Credential store is unavailable."
    assert "postgresql://" not in caplog.text and "StorageUnavailable" in caplog.text


def test_lock_timeout_is_a_store_error(pool, pg, make_pool, database):
    insert_credential(pool)
    fast = make_pool(lock_timeout_ms=300, acquire_timeout=2.0)
    repo = PostgresCredentialRepository(fast)
    blocker = pg.psycopg.connect(database, autocommit=False)
    try:
        blocker.execute("LOCK TABLE api_credentials IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(CredentialStoreError):
            repo.get_by_prefix("abcdefghijkl")
    finally:
        blocker.rollback()
        blocker.close()
    assert repo.get_by_prefix("abcdefghijkl") is None  # recovered, no stuck state


def test_statement_timeout_is_a_store_error(make_pool, monkeypatch):
    import supportops_service.repositories.credentials as mod

    fast = make_pool(statement_timeout_ms=200)
    repo = PostgresCredentialRepository(fast)
    # A statement that outlives statement_timeout, routed through the repository's own path.
    monkeypatch.setattr(mod, "_SELECT_BY_PREFIX", "SELECT pg_sleep(2), %s::text")
    with pytest.raises(CredentialStoreError) as caught:
        repo.get_by_prefix("abcdefghijkl")
    assert caught.value.__context__ is None and str(caught.value) == "Credential store is unavailable."


def _app(repo):
    app = FastAPI()
    install_security(app, CredentialVerifier(repo, PEPPERS, Clock()))

    @app.get("/v1/probe")
    def probe(ctx: TenantContext = Depends(require_scope(Scope.TICKETS_READ))):
        return {"tenant_id": ctx.tenant_id}

    return app


def test_http_503_when_postgres_is_down_and_no_leak(pool, make_pool):
    token, _, _ = insert_credential(pool)
    p = make_pool()
    app = _app(PostgresCredentialRepository(p))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        p.close()
        response = client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_unavailable"
    body = response.text.lower()
    for fragment in ("postgres", "psycopg", "api_credentials", "localhost", "connection"):
        assert fragment not in body
    assert token.split(".")[2].lower() not in body


def test_tenant_isolation_and_scopes_from_the_database_only(pool, repo):
    a, _, _ = insert_credential(pool, tenant="tenant-a", scopes=("tickets:read",))
    b, _, _ = insert_credential(pool, tenant="tenant-b", scopes=("tickets:write",))
    with TestClient(_app(repo), raise_server_exceptions=False) as client:
        ra = client.get("/v1/probe", headers={"Authorization": f"Bearer {a}", "X-Tenant-Id": "tenant-b"})
        rb = client.get("/v1/probe", headers={"Authorization": f"Bearer {b}"})
    assert ra.status_code == 200 and ra.json() == {"tenant_id": "tenant-a"}
    assert rb.status_code == 403  # tenant-b credential has no tickets:read


def test_concurrent_authentication_against_postgres(pool):
    repo = PostgresCredentialRepository(pool, touch_min_interval_seconds=0)
    verifier = CredentialVerifier(repo, PEPPERS, Clock())
    items = [(insert_credential(pool, tenant=f"tenant-{i}")[0], f"tenant-{i}") for i in range(4)]
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def worker(n):
        barrier.wait()
        for _ in range(25):
            token, tenant = items[n % 4]
            try:
                ctx = verifier.authenticate(bearer(token))
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
                continue
            if ctx.tenant_id != tenant:
                errors.append("mixed")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors and not any(t.is_alive() for t in threads)


def test_least_privilege_role_is_sufficient(pg, admin_dsn, database, pool):
    """The repository needs only SELECT + UPDATE(last_used_at); no INSERT/DELETE/other UPDATE."""
    role = f"sec_api_{uuid.uuid4().hex[:12]}"
    password = uuid.uuid4().hex
    token, cred_id, prefix = insert_credential(pool)
    with pg.psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(pg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            pg.sql.Identifier(role), pg.sql.Literal(password)))
    try:
        with pool.connection() as conn:
            conn.execute(pg.sql.SQL("GRANT SELECT, UPDATE (last_used_at) ON api_credentials TO {}").format(
                pg.sql.Identifier(role)))
            conn.execute(pg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(pg.sql.Identifier(role)))
        dsn = pg.make_conninfo(database, user=role, password=password)
        restricted = pg.DatabasePool(pg.DatabaseSettings(dsn=pg.SecretStr(dsn), max_size=2))
        restricted.open()
        try:
            repo = PostgresCredentialRepository(restricted, touch_min_interval_seconds=0)
            ctx = CredentialVerifier(repo, PEPPERS, Clock()).authenticate(bearer(token))
            assert ctx.credential_id == cred_id
            assert last_used(pool, cred_id) is not None
            with restricted.connection() as conn, pytest.raises(Exception):
                conn.execute("UPDATE api_credentials SET revoked_at = NULL")  # not granted
        finally:
            restricted.close()
    finally:
        # Privileges are per-database: revoke inside the test database, then drop the role.
        with pg.psycopg.connect(database, autocommit=True) as in_db:
            in_db.execute(pg.sql.SQL("DROP OWNED BY {}").format(pg.sql.Identifier(role)))
        with pg.psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(pg.sql.SQL("DROP ROLE IF EXISTS {}").format(pg.sql.Identifier(role)))
