"""Migration runner against real PostgreSQL."""

import threading

import psycopg
import pytest

from supportops_service.db.errors import MigrationChecksumMismatch, MigrationSetError
from supportops_service.db.migrate import current_version, migrate
from supportops_service.db.pool import DatabasePool

from pg_test_helpers import make_settings

pytestmark = pytest.mark.postgres


def _tables(pool):
    with pool.connection() as c:
        return {r["tablename"] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}


def test_apply_to_empty_database(pool):
    assert current_version(pool) == 0
    assert migrate(pool) == [1]
    assert {"schema_migrations", "api_credentials", "tickets", "runs"} <= _tables(pool)
    assert current_version(pool) == 1
    with pool.connection() as c:
        row = c.execute("SELECT version, name, checksum FROM schema_migrations").fetchone()
    assert (row["version"], row["name"]) == (1, "initial") and len(row["checksum"]) == 64


def test_reapply_is_a_noop(pool):
    migrate(pool)
    with pool.connection() as c:
        before = c.execute("SELECT version, checksum, applied_at FROM schema_migrations").fetchall()
    assert migrate(pool) == []
    with pool.connection() as c:
        after = c.execute("SELECT version, checksum, applied_at FROM schema_migrations").fetchall()
    assert before == after


def test_indexes_and_constraints_exist(migrated_pool):
    with migrated_pool.connection() as c:
        idx = {r["indexname"]: r["indexdef"] for r in c.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public'")}
    assert "WHERE (status = 'accepted'::text)" in idx["runs_claim_idx"]
    assert "WHERE (status = 'running'::text)" in idx["runs_lease_idx"]
    assert any("UNIQUE" in d and "tenant_id, idempotency_key" in d for d in idx.values())
    assert any("UNIQUE" in d and "tenant_id, ticket_id" in d for d in idx.values())
    assert any("UNIQUE" in d and "(ticket_id)" in d and "runs" in d for d in idx.values())
    assert any("UNIQUE" in d and "key_prefix" in d for d in idx.values())


def _write(tmp_path, name, sql):
    (tmp_path / name).write_text(sql)


def test_checksum_mismatch_refuses_and_changes_nothing(pool, tmp_path):
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t1 (id int);")
    migrate(pool, tmp_path)
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t1 (id int, extra int);")
    _write(tmp_path, "0002_u.sql", "CREATE TABLE t2 (id int);")
    with pytest.raises(MigrationChecksumMismatch) as exc:
        migrate(pool, tmp_path)
    assert exc.value.version == 1
    assert "t1" not in str(exc.value) and "extra" not in str(exc.value)
    assert "t2" not in _tables(pool)  # pending migration not applied past the mismatch
    assert current_version(pool) == 1


def test_database_ahead_of_code_is_refused(pool, tmp_path):
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (id int);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (id int);")
    migrate(pool, tmp_path)
    (tmp_path / "0002_b.sql").unlink()
    with pytest.raises(MigrationSetError):
        migrate(pool, tmp_path)


def test_history_gap_is_refused_not_repaired(pool, tmp_path):
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (id int);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (id int);")
    migrate(pool, tmp_path)
    with pool.connection() as c:
        c.execute("DELETE FROM schema_migrations WHERE version = 1")
    with pytest.raises(MigrationSetError):
        migrate(pool, tmp_path)


def test_failed_migration_rolls_back_everything(pool, tmp_path):
    _write(tmp_path, "0001_a.sql", "CREATE TABLE ok_table (id int);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE half (id int); SELECT 1/0;")
    with pytest.raises(psycopg.Error):
        migrate(pool, tmp_path)
    assert not ({"ok_table", "half"} & _tables(pool))
    assert current_version(pool) == 0


def test_concurrent_runners_are_serialised(database, tmp_path):
    # A slow migration forces real overlap; a second execution would fail on CREATE TABLE.
    _write(tmp_path, "0001_slow.sql", "SELECT pg_sleep(1.5); CREATE TABLE once (id int);")
    pools = [DatabasePool(make_settings(database)) for _ in range(4)]
    for p in pools:
        p.open()
    barrier = threading.Barrier(len(pools))
    results, errors = [], []

    def run(p):
        barrier.wait()
        try:
            results.append(migrate(p, tmp_path))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(p,)) for p in pools]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    try:
        assert not errors, errors
        assert sorted(results) == [[], [], [], [1]]  # exactly one runner applied it
        with pools[0].connection() as c:
            assert c.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1
    finally:
        [p.close() for p in pools]
