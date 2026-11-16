"""Versioned SQL migration runner.

* Files: ``migrations/NNNN_name.sql``, versions contiguous from 1.
* History: ``schema_migrations(version, name, checksum, applied_at)``.
* Checksum: SHA-256 of the file (CRLF normalised). A changed applied file is refused.
* Serialisation: a transaction-level advisory lock; concurrent runners queue.
* The whole run is one transaction: either every pending migration applies or none.
* No auto-repair: inconsistencies raise and leave the schema untouched.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from supportops_service.db.errors import (
    MigrationChecksumMismatch,
    MigrationError,
    MigrationSetError,
)
from supportops_service.db.pool import DatabasePool

logger = logging.getLogger("supportops_service.db.migrate")

DEFAULT_MIGRATIONS_DIR: Final = Path(__file__).parent / "migrations"
# Arbitrary fixed 63-bit key for pg_advisory_xact_lock ("SPOPMIGR").
ADVISORY_LOCK_KEY: Final = 0x53504F504D494752
LOCK_WAIT_SECONDS: Final = 120

_FILE_RE: Final = re.compile(r"^(\d{4})_([a-z0-9][a-z0-9_]*)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def checksum_of(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """Read and validate migration files. Raises :class:`MigrationSetError`."""
    base = DEFAULT_MIGRATIONS_DIR if directory is None else directory
    found: dict[int, Migration] = {}
    for path in sorted(base.iterdir()):
        if path.is_dir() or path.name.startswith(".") or path.suffix != ".sql":
            continue
        match = _FILE_RE.match(path.name)
        if match is None:
            raise MigrationSetError("Migration file name is invalid.")
        version = int(match.group(1))
        if version in found:
            raise MigrationSetError(f"Duplicate migration version {version:04d}.")
        raw = path.read_bytes()
        found[version] = Migration(
            version=version,
            name=match.group(2),
            checksum=checksum_of(raw),
            sql=raw.decode("utf-8"),
        )
    ordered = [found[v] for v in sorted(found)]
    if [m.version for m in ordered] != list(range(1, len(ordered) + 1)):
        raise MigrationSetError("Migration versions must be contiguous from 0001.")
    return ordered


_BOOTSTRAP_SQL: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    int PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def migrate(pool: DatabasePool, directory: Path | None = None) -> list[int]:
    """Apply pending migrations; return the versions applied by *this* call."""
    migrations = load_migrations(directory)
    applied_now: list[int] = []
    with pool.connection() as conn:
        with conn.transaction():
            # Applies to this transaction only; migrations may be slow and the
            # per-connection pool timeouts are tuned for request-sized work.
            conn.execute("SELECT set_config('statement_timeout', '0', true)")
            conn.execute(
                "SELECT set_config('lock_timeout', %s, true)", (f"{LOCK_WAIT_SECONDS}s",)
            )
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            conn.execute(_BOOTSTRAP_SQL)

            history = {
                row["version"]: row
                for row in conn.execute(
                    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                )
            }
            by_version = {m.version: m for m in migrations}

            for version, row in history.items():
                known = by_version.get(version)
                if known is None:
                    raise MigrationSetError("Database has a migration unknown to this build.")
                if known.checksum != row["checksum"] or known.name != row["name"]:
                    raise MigrationChecksumMismatch(version)
            if sorted(history) != list(range(1, len(history) + 1)):
                raise MigrationSetError("Migration history has gaps.")

            for migration in migrations:
                if migration.version in history:
                    continue
                conn.execute(migration.sql)  # no params: simple protocol, multi-statement
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                applied_now.append(migration.version)
    if applied_now:
        logger.info("applied migrations: %s", applied_now)
    return applied_now


def current_version(pool: DatabasePool) -> int:
    """Highest applied version, 0 if none/uninitialised."""
    with pool.connection() as conn:
        exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS e").fetchone()
        if not exists or not exists["e"]:
            return 0
        row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
        return int(row["v"]) if row else 0


__all__ = [
    "ADVISORY_LOCK_KEY",
    "Migration",
    "MigrationError",
    "current_version",
    "load_migrations",
    "migrate",
]
