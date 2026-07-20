"""The api serves the built SPA when a dist is present (single-image deploy).

No database required — the ops/SPA routes don't touch it, and TestClient here
is used without the lifespan context so startup hooks don't run.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tiqora.api.app import create_app
from tiqora.config import Settings


def _write_dist(tmp: Path) -> Path:
    dist = tmp / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Tiqora SPA</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def _app(tmp: Path, *, serve: bool = True) -> TestClient:
    dist = _write_dist(tmp)
    settings = Settings(
        environment="test",
        serve_frontend=serve,
        frontend_dist_dir=str(dist),
    )
    return TestClient(create_app(settings))


def test_spa_root_and_deeplinks_serve_index(tmp_path: Path) -> None:
    client = _app(tmp_path)

    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "Tiqora SPA" in root.text

    # Client-side deep link with no server route -> SPA shell.
    deep = client.get("/agent/queues")
    assert deep.status_code == 200
    assert "Tiqora SPA" in deep.text


def test_spa_serves_static_assets(tmp_path: Path) -> None:
    client = _app(tmp_path)

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log('app')" in asset.text

    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert "<svg/>" in favicon.text


def test_api_routes_stay_json_not_spa(tmp_path: Path) -> None:
    client = _app(tmp_path)

    # Ops routes keep working.
    assert client.get("/health").json()["status"] == "ok"

    # Unknown API paths must stay JSON 404, never the SPA HTML shell.
    missing = client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert "text/html" not in missing.headers.get("content-type", "")
    assert "Tiqora SPA" not in missing.text
    assert missing.json()["detail"]


def test_serve_frontend_disabled_keeps_json_root(tmp_path: Path) -> None:
    client = _app(tmp_path, serve=False)
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["name"] == "Tiqora"


def test_missing_dist_keeps_json_root() -> None:
    settings = Settings(
        environment="test",
        serve_frontend=True,
        frontend_dist_dir="/nonexistent/tiqora/dist",
    )
    client = TestClient(create_app(settings))
    assert client.get("/").json()["name"] == "Tiqora"
