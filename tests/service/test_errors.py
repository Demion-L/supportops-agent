import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from supportops_service.app import create_app
from supportops_service.config import ServiceSettings

MARKER = "SECRET-MARKER-9f3a"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int


@pytest.fixture
def probe_client():
    app = create_app(ServiceSettings())

    @app.post("/probe")
    def probe(payload: Payload) -> dict:
        return {"ok": True}

    @app.get("/probe-query")
    def probe_query(n: int) -> dict:
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError(f"{MARKER} /home/secret/path.py password=hunter2")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _assert_envelope(r, status, code):
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == code
    assert err["request_id"] == r.headers["x-request-id"]
    assert set(err) >= {"code", "message", "request_id"}
    return err


def test_404_envelope(client):
    r = client.get(f"/nope-{MARKER}")
    _assert_envelope(r, 404, "not_found")
    assert MARKER not in r.text


def test_405_envelope(client):
    r = client.post("/healthz")
    _assert_envelope(r, 405, "method_not_allowed")


def test_422_body_does_not_reflect_values(probe_client):
    r = probe_client.post("/probe", json={"count": MARKER})
    err = _assert_envelope(r, 422, "validation_error")
    assert MARKER not in r.text
    assert err["fields"][0]["path"] == ["body", "count"]


def test_422_does_not_reflect_unknown_keys(probe_client):
    r = probe_client.post("/probe", json={"count": 1, MARKER: "v"})
    _assert_envelope(r, 422, "validation_error")
    assert MARKER not in r.text


def test_422_query_and_malformed_json_do_not_reflect(probe_client):
    r = probe_client.get(f"/probe-query?n={MARKER}")
    _assert_envelope(r, 422, "validation_error")
    assert MARKER not in r.text
    r = probe_client.post(
        "/probe", content=f'{{"count": {MARKER}'.encode(), headers={"content-type": "application/json"}
    )
    _assert_envelope(r, 422, "validation_error")
    assert MARKER not in r.text


def test_500_is_safe(probe_client):
    r = probe_client.get("/boom", headers={"X-Request-ID": "req-1"})
    err = _assert_envelope(r, 500, "internal_error")
    assert err["request_id"] == "req-1"
    for leaked in (MARKER, "hunter2", "/home/secret", "Traceback", "RuntimeError"):
        assert leaked not in r.text


def test_500_server_log_has_no_exception_message(probe_client, caplog):
    with caplog.at_level("ERROR", logger="supportops_service"):
        probe_client.get("/boom")
    assert "RuntimeError" in caplog.text
    assert MARKER not in caplog.text
    assert "hunter2" not in caplog.text


def test_v1_responses_are_no_store(probe_client):
    r = probe_client.get("/v1/anything")
    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store"
    assert "cache-control" not in probe_client.get("/healthz").headers
