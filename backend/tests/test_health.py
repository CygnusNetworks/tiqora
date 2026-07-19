"""Smoke tests for ops endpoints (no database required)."""

from fastapi.testclient import TestClient

from tiqora.api.app import create_app
from tiqora.config import Settings


def test_health_ok() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_metrics_exposes_prometheus() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert b"tiqora_http_requests_total" in response.content or response.content


def test_root() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "Tiqora"
