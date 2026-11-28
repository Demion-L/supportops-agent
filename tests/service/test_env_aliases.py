"""HERMES_* environment variables with deprecated SUPPORTOPS_* fallback (E2-Phase 01).

Contract: new prefix wins/accepted; legacy prefix still works (deprecated); both set with
different values -> fail closed without echoing any value; blank counts as unset.
"""

from __future__ import annotations

import logging

import pytest

from hermes_service.config import ServiceConfigurationError, ServiceSettings
from hermes_service.db.pool import DatabaseSettings

PEPPER_A = "pepper-value-A-0123456789abcdef0123456789abcdef"
PEPPER_B = "pepper-value-B-0123456789abcdef0123456789abcdef"


def test_hermes_prefix_is_read():
    s = ServiceSettings.from_env({"HERMES_PEPPER": PEPPER_A, "HERMES_PEPPER_ID": "p1"})
    assert s.pepper.get_secret_value() == PEPPER_A
    assert s.pepper_id == "p1"


def test_legacy_prefix_still_works_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="hermes_service.config"):
        s = ServiceSettings.from_env({"SUPPORTOPS_PEPPER": PEPPER_A, "SUPPORTOPS_DEMO_MODE": "false"})
    assert s.pepper.get_secret_value() == PEPPER_A
    assert s.demo_mode is False
    text = caplog.text
    assert "SUPPORTOPS_PEPPER" in text and "HERMES_PEPPER" in text
    assert PEPPER_A not in text


def test_same_value_under_both_prefixes_is_accepted():
    s = ServiceSettings.from_env({"HERMES_PEPPER": PEPPER_A, "SUPPORTOPS_PEPPER": PEPPER_A})
    assert s.pepper.get_secret_value() == PEPPER_A


def test_conflicting_values_fail_closed_without_leaking():
    with pytest.raises(ServiceConfigurationError) as exc:
        ServiceSettings.from_env({"HERMES_PEPPER": PEPPER_A, "SUPPORTOPS_PEPPER": PEPPER_B})
    msg = str(exc.value)
    assert "HERMES_PEPPER" in msg and "SUPPORTOPS_PEPPER" in msg
    assert PEPPER_A not in msg and PEPPER_B not in msg


def test_conflict_applies_to_every_setting():
    with pytest.raises(ServiceConfigurationError):
        ServiceSettings.from_env({"HERMES_DEMO_MODE": "true", "SUPPORTOPS_DEMO_MODE": "false"})
    with pytest.raises(ServiceConfigurationError):
        DatabaseSettings.from_env(
            {"HERMES_DATABASE_URL": "postgresql://a/x", "SUPPORTOPS_DATABASE_URL": "postgresql://b/x"}
        )


def test_blank_value_counts_as_unset():
    s = ServiceSettings.from_env({"HERMES_PEPPER": "  ", "SUPPORTOPS_PEPPER": PEPPER_A})
    assert s.pepper.get_secret_value() == PEPPER_A


def test_database_url_accepts_both_prefixes():
    assert DatabaseSettings.from_env({"HERMES_DATABASE_URL": "postgresql://h/x"}).dsn.get_secret_value() == "postgresql://h/x"
    assert DatabaseSettings.from_env({"SUPPORTOPS_DATABASE_URL": "postgresql://l/x"}).dsn.get_secret_value() == "postgresql://l/x"


def test_missing_database_url_names_the_new_variable():
    with pytest.raises(ServiceConfigurationError, match="HERMES_DATABASE_URL is required"):
        DatabaseSettings.from_env({})
