"""Small helpers to drive the real peer container via docker exec.

Uses GOLDEN_COMPOSE_PROJECT / GOLDEN_PEER so multi-peer stacks stay isolated.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent


def _compose_project() -> str:
    peer = os.environ.get("GOLDEN_PEER", "znuny-6.5")
    if env := os.environ.get("GOLDEN_COMPOSE_PROJECT"):
        return env
    # Compose project names may not contain dots (peer ids like znuny-6.5).
    return f"tiqora-golden-{peer.lower().replace('.', '-')}"


def _compose_base() -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        _compose_project(),
        "-f",
        str(GOLDEN_DIR / "docker-compose.golden.yml"),
    ]


def znuny_console(*args: str, check: bool = True) -> str:
    """Run peer console command inside the golden container via docker exec."""
    cmd = [
        *_compose_base(),
        "exec",
        "-T",
        "znuny",
        "/usr/local/bin/znuny-entrypoint.sh",
        "console",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"peer console command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


def znuny_perl_eval(code: str) -> str:
    """Run an inline Perl snippet inside the golden container."""
    cmd = [
        *_compose_base(),
        "exec",
        "-T",
        "-u",
        "otrs",
        "znuny",
        "perl",
        "-I",
        "/opt/otrs",
        "-I",
        "/opt/otrs/Kernel/cpan-lib",
        "-e",
        code,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"peer perl eval failed: {result.stderr}\nstdout: {result.stdout}")
    return result.stdout


def znuny_copy_in(local: Path, remote: str) -> None:
    """Copy a local file into the golden Znuny container (``docker compose cp``)."""
    cmd = [*_compose_base(), "cp", str(local), f"znuny:{remote}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose cp {local} -> {remote} failed ({result.returncode}): {result.stderr}"
        )


class _FakeRedis:
    """Minimal in-memory Redis stand-in for the Tiqora session store."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def tiqora_http_client(session_factory):  # type: ignore[no-untyped-def]
    """``httpx.AsyncClient`` over Tiqora's real ASGI app, wired to the golden DB.

    Use as ``async with tiqora_http_client(factory) as client``. The app is built
    fresh, so its webservice registry loads the rows currently in
    ``gi_webservice_config`` (shared with the Znuny peer) on the first request.
    """
    import httpx

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings
    from tiqora.domain.auth import SessionStore

    async def _override_get_db():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = session_factory
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://tiqora.test", timeout=60
    )


__all__ = ["tiqora_http_client", "znuny_console", "znuny_copy_in", "znuny_perl_eval"]
