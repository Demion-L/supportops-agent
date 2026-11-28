"""Migration file discovery/validation. No PG needed."""

import pytest

from hermes_service.db.errors import MigrationSetError
from hermes_service.db.migrate import checksum_of, load_migrations


def test_packaged_migrations_load_and_are_contiguous():
    migs = load_migrations()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))
    assert migs[0].name == "initial"
    assert len(migs[0].checksum) == 64


def test_checksum_ignores_line_endings_only():
    assert checksum_of(b"a\r\nb") == checksum_of(b"a\nb")
    assert checksum_of(b"a\nb") != checksum_of(b"a\nc")


def test_gap_is_rejected(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0003_c.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationSetError):
        load_migrations(tmp_path)


def test_duplicate_version_is_rejected(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationSetError):
        load_migrations(tmp_path)


def test_bad_file_name_is_rejected(tmp_path):
    (tmp_path / "1_add.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationSetError):
        load_migrations(tmp_path)
