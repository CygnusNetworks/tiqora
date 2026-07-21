"""Production startup guards, docs/metrics gating, and security headers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tiqora.api.app import create_app
from tiqora.config import (
    DEFAULT_SECRET_KEY,
    ProductionConfigError,
    Settings,
)

# A 64-char secret that is not the published default (openssl rand -hex 32 length).
_PROD_SECRET = "a" * 64
_PROD_MEILI = "prod-meili-master-key-not-a-dev-default-xx"


def _safe_production_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "environment": "production",
        "secret_key": _PROD_SECRET,
        "debug": False,
        "cors_origins": "https://helpdesk.example.com",
        "meili_master_key": _PROD_MEILI,
    }
    base.update(overrides)
    return base


def test_validate_production_rejects_default_secret() -> None:
    s = Settings(**_safe_production_kwargs(secret_key=DEFAULT_SECRET_KEY))
    with pytest.raises(ProductionConfigError, match="TIQORA_SECRET_KEY"):
        s.validate_production()


def test_validate_production_rejects_short_secret() -> None:
    s = Settings(**_safe_production_kwargs(secret_key="too-short-for-prod"))
    with pytest.raises(ProductionConfigError, match="too short"):
        s.validate_production()


def test_validate_production_rejects_debug() -> None:
    s = Settings(**_safe_production_kwargs(debug=True))
    with pytest.raises(ProductionConfigError, match="TIQORA_DEBUG"):
        s.validate_production()


def test_validate_production_rejects_cors_wildcard() -> None:
    s = Settings(**_safe_production_kwargs(cors_origins="*"))
    with pytest.raises(ProductionConfigError, match="CORS"):
        s.validate_production()


def test_validate_production_rejects_dev_meili_key() -> None:
    s = Settings(**_safe_production_kwargs(meili_master_key="tiqora-dev-master-key"))
    with pytest.raises(ProductionConfigError, match="MEILI_MASTER_KEY"):
        s.validate_production()

    s2 = Settings(**_safe_production_kwargs(meili_master_key="change-me-meili-master-key"))
    with pytest.raises(ProductionConfigError, match="MEILI_MASTER_KEY"):
        s2.validate_production()


def test_validate_production_accepts_safe_config() -> None:
    s = Settings(**_safe_production_kwargs())
    s.validate_production()  # must not raise


def test_validate_production_noop_outside_production() -> None:
    """Dev/test defaults must never hard-fail."""
    for env in ("development", "test", "staging"):
        s = Settings(
            environment=env,
            secret_key=DEFAULT_SECRET_KEY,
            debug=True,
            cors_origins="*",
            meili_master_key="tiqora-dev-master-key",
        )
        s.validate_production()  # must not raise


def test_create_app_raises_on_unsafe_production() -> None:
    with pytest.raises(ProductionConfigError):
        create_app(Settings(**_safe_production_kwargs(secret_key=DEFAULT_SECRET_KEY)))


def test_session_cookie_secure_defaults_true_in_production() -> None:
    s = Settings(**_safe_production_kwargs())
    assert s.session_cookie_secure is True


def test_session_cookie_secure_defaults_false_outside_production() -> None:
    s = Settings(environment="development")
    assert s.session_cookie_secure is False
    s2 = Settings(environment="test")
    assert s2.session_cookie_secure is False


def test_session_cookie_secure_overridable_in_production() -> None:
    s = Settings(**_safe_production_kwargs(session_cookie_secure=False))
    assert s.session_cookie_secure is False
    s2 = Settings(**_safe_production_kwargs(session_cookie_secure=True))
    assert s2.session_cookie_secure is True


def test_docs_disabled_in_production() -> None:
    app = create_app(Settings(**_safe_production_kwargs()))
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_enabled_outside_production() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_metrics_enabled_by_default() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200


def test_metrics_disabled_returns_404() -> None:
    app = create_app(Settings(environment="test", metrics_enabled=False))
    client = TestClient(app)
    assert client.get("/metrics").status_code == 404


def test_security_headers_present() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    # Default: report-only so SPA is not broken by CSP
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "default-src 'self'" in response.headers["Content-Security-Policy-Report-Only"]
    assert "Content-Security-Policy" not in response.headers


def test_csp_enforce_sets_enforcing_header() -> None:
    app = create_app(Settings(environment="test", csp_enforce=True))
    client = TestClient(app)
    response = client.get("/health")
    assert "Content-Security-Policy" in response.headers
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "Content-Security-Policy-Report-Only" not in response.headers
