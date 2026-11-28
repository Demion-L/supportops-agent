import subprocess
import sys
import textwrap

from fastapi import FastAPI

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings

_NO_NET = textwrap.dedent(
    """
    import socket, sys
    def blocked(*a, **k):
        raise AssertionError("network")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    import supportops_service.app
    import supportops_service
    {extra}
    """
)


def _run(extra: str, env=None):
    return subprocess.run(
        [sys.executable, "-I", "-c", _NO_NET.format(extra=extra)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_import_has_no_network_or_env_side_effects():
    result = _run("")
    assert result.returncode == 0, result.stderr


def test_import_and_create_app_do_not_load_agent_or_llm_client():
    result = _run(
        "from supportops_service.app import create_app\n"
        "create_app()\n"
        "assert 'supportops_agent' not in sys.modules\n"
        "assert 'openai' not in sys.modules\n"
    )
    assert result.returncode == 0, result.stderr


def test_create_app_returns_fastapi_instance(app):
    assert isinstance(app, FastAPI)


def test_create_app_instances_are_independent():
    a, b = create_app(ServiceSettings()), create_app(ServiceSettings())
    assert a is not b
    assert a.router is not b.router


def test_docs_disabled_by_default(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_docs_can_be_enabled_explicitly():
    from fastapi.testclient import TestClient

    app = create_app(ServiceSettings(docs_enabled=True))
    assert TestClient(app).get("/openapi.json").status_code == 200


def test_no_cors_headers(client):
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_demo(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "llm_mode": "demo"}


def test_request_id_generated_and_returned(client):
    assert len(client.get("/healthz").headers["x-request-id"]) >= 32


def test_valid_inbound_request_id_is_kept(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_invalid_inbound_request_id_is_replaced(client):
    bad = "x" * 65
    for value in (bad, "has space", "a/b", b"i\xe9"):
        r = client.get("/healthz", headers={"X-Request-ID": value})
        assert r.headers["x-request-id"] != value
        assert r.status_code == 200


def test_support_agent_is_never_invoked(monkeypatch, client):
    import supportops_agent.runtime.agent as agent_mod

    def boom(*a, **k):
        raise AssertionError("SupportAgent used by the service skeleton")

    monkeypatch.setattr(agent_mod.SupportAgent, "__init__", boom)
    monkeypatch.setattr(agent_mod.SupportAgent, "run", boom)
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_service_package_does_not_import_agent_sources():
    import pathlib

    import supportops_service

    root = pathlib.Path(supportops_service.__file__).parent
    for path in root.rglob("*.py"):
        text = path.read_text()
        assert "supportops_agent" not in text.replace("``supportops_agent``", ""), path
