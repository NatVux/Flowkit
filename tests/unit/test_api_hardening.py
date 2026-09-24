"""API boundary hardening tests."""

import pytest
from fastapi.testclient import TestClient

from agent.main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_validation_errors_are_structured(client):
    response = client.post("/api/requests", json={"type": "NOT_A_REQUEST_TYPE"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "message" in body["error"]


def test_oversized_request_is_rejected(client):
    response = client.post(
        "/api/requests",
        content=b"x" * (32 * 1024 * 1024 + 1),
        headers={"content-type": "application/json", "content-length": str(32 * 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_invalid_resource_id_is_rejected(client):
    response = client.get("/api/characters/../secret")
    assert response.status_code in (404, 422)


def test_callback_requires_secret(client):
    response = client.post("/api/ext/callback", json={"id": "bad"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HTTP_401"
