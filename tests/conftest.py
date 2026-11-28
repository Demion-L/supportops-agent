import pytest

from supportops_agent.business import mock_data


@pytest.fixture(autouse=True)
def clean_case_storage():
    """Every test starts and ends with empty in-memory support cases."""
    mock_data.CASES.clear()
    yield
    mock_data.CASES.clear()
