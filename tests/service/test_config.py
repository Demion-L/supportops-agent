import pytest

from hermes_service.app import create_app
from hermes_service.config import ServiceConfigurationError, ServiceSettings
from hermes_service.llm_mode import LlmMode, resolve_llm_mode


def test_default_is_demo_mode():
    assert ServiceSettings().demo_mode is True
    assert ServiceSettings.from_env({}).demo_mode is True
    assert resolve_llm_mode(ServiceSettings()) is LlmMode.DEMO


def test_demo_mode_ignores_credentials():
    s = ServiceSettings.from_env({"HERMES_LLM_API_KEY": "sk-demo-should-not-load"})
    assert s.llm_api_key is None


def test_live_mode_without_key_fails_closed():
    s = ServiceSettings.from_env({"HERMES_DEMO_MODE": "false"})
    with pytest.raises(ServiceConfigurationError):
        resolve_llm_mode(s)
    with pytest.raises(ServiceConfigurationError):
        create_app(s)


def test_live_mode_with_blank_key_fails_closed():
    s = ServiceSettings.from_env({"HERMES_DEMO_MODE": "0", "HERMES_LLM_API_KEY": "  "})
    with pytest.raises(ServiceConfigurationError):
        create_app(s)


def test_live_mode_with_key_is_configured_without_client():
    s = ServiceSettings.from_env(
        {"HERMES_DEMO_MODE": "false", "HERMES_LLM_API_KEY": "sk-synthetic-test"}
    )
    assert resolve_llm_mode(s) is LlmMode.LIVE
    assert "sk-synthetic-test" not in repr(s)
    assert "sk-synthetic-test" not in str(s.model_dump())


def test_invalid_boolean_fails_closed_without_echo():
    with pytest.raises(ServiceConfigurationError) as info:
        ServiceSettings.from_env({"HERMES_DEMO_MODE": "SECRET-MARKER"})
    assert "SECRET-MARKER" not in str(info.value)


def test_live_error_message_has_no_secrets():
    s = ServiceSettings.from_env({"HERMES_DEMO_MODE": "false"})
    with pytest.raises(ServiceConfigurationError) as info:
        create_app(s)
    assert "Live mode requires" in str(info.value)
