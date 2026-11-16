import socket

import pytest
from fastapi.testclient import TestClient

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Any outbound socket connection in service tests is a failure."""

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a service test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def app():
    return create_app(ServiceSettings())


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: observe the real 500 response.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
