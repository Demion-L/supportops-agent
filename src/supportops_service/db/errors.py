"""Storage errors. Messages are fixed text: never a DSN, SQL, or driver message."""

from __future__ import annotations


class StorageUnavailable(RuntimeError):
    """The database cannot be reached or did not answer in time."""

    def __init__(self) -> None:
        super().__init__("Storage is unavailable.")


class MigrationError(RuntimeError):
    """A migration run was refused. The schema is never auto-repaired."""


class MigrationChecksumMismatch(MigrationError):
    """An already-applied migration file differs from what was applied."""

    def __init__(self, version: int) -> None:
        super().__init__(f"Applied migration {version:04d} has been modified.")
        self.version = version


class MigrationSetError(MigrationError):
    """Migration files or history are inconsistent (gap, duplicate, unknown version)."""
