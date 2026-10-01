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


def test_health_ai_reports_exhausted_caps_without_names(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Monitoring probe: status flips to ``limit_reached``; the unauthenticated
    body names caps by kind/id/window only, never queue or provider names."""
    from contextlib import asynccontextmanager
    from datetime import datetime

    from tiqora.ai.limits import LimitStatus

    reached = [
        LimitStatus(
            kind="queue_tokens_day",
            subject_id=5,
            subject_name="stw-bn",
            window="day",
            used=3_100_000,
            limit=3_000_000,
            window_start=datetime(2026, 10, 1),
            resets_at=datetime(2026, 10, 2),
        )
    ]
    results = [reached, []]

    async def _fake_exhausted(_session: object) -> list[LimitStatus]:
        return results.pop(0)

    @asynccontextmanager
    async def _fake_session():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr("tiqora.ai.limits.exhausted_limits", _fake_exhausted)
    monkeypatch.setattr("tiqora.api.app.get_session_factory", lambda: _fake_session)
    client = TestClient(create_app(Settings(environment="test")))

    body = client.get("/health/ai").json()
    assert body == {
        "status": "limit_reached",
        "limits_reached": [
            {
                "kind": "queue_tokens_day",
                "subject_id": 5,
                "window": "day",
                "resets_at": "2026-10-02T00:00:00Z",
            }
        ],
    }
    assert "stw-bn" not in str(body)
    assert client.get("/health/ai").json() == {"status": "ok", "limits_reached": []}
