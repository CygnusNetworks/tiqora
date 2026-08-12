"""Bounded upload reads and the request-body size gate (security review M-4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient

from tiqora.api.uploads import (
    MAX_ATTACHMENT_BYTES,
    body_size_limit_middleware,
    read_upload_within_limit,
)


class _FakeUpload:
    """Yields *total* bytes in chunks, counting how many were handed out."""

    def __init__(self, total: int) -> None:
        self._remaining = total
        self.bytes_served = 0

    async def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        n = self._remaining if size < 0 else min(size, self._remaining)
        self._remaining -= n
        self.bytes_served += n
        return b"x" * n


async def test_reads_a_small_upload_whole() -> None:
    upload = _FakeUpload(1024)
    content = await read_upload_within_limit(upload, 4096)  # type: ignore[arg-type]
    assert content == b"x" * 1024


async def test_upload_exactly_at_the_limit_is_accepted() -> None:
    upload = _FakeUpload(4096)
    content = await read_upload_within_limit(upload, 4096)  # type: ignore[arg-type]
    assert len(content) == 4096


async def test_oversized_upload_raises_413() -> None:
    upload = _FakeUpload(4097)
    with pytest.raises(HTTPException) as exc:
        await read_upload_within_limit(upload, 4096)  # type: ignore[arg-type]
    assert exc.value.status_code == 413


async def test_oversized_upload_stops_reading_early() -> None:
    """The point of the fix: a huge body must not be pulled into memory in full
    before it is rejected."""
    huge = 200 * 1024 * 1024
    upload = _FakeUpload(huge)
    with pytest.raises(HTTPException):
        await read_upload_within_limit(upload, 1024 * 1024)  # type: ignore[arg-type]
    assert upload.bytes_served < 2 * 1024 * 1024


def _app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        return await body_size_limit_middleware(request, call_next)

    @app.post("/echo")
    async def echo(request: Request) -> Response:
        body = await request.body()
        return JSONResponse({"len": len(body)})

    return app


def test_middleware_passes_a_normal_body_through() -> None:
    with TestClient(_app()) as client:
        resp = client.post("/echo", content=b"hello")
        assert resp.status_code == 200
        assert resp.json() == {"len": 5}


def test_middleware_rejects_an_oversized_content_length_without_reading_it() -> None:
    """Answered from the header alone — the body is never parsed or spooled."""
    with TestClient(_app()) as client:
        resp = client.post(
            "/echo",
            content=b"",
            headers={"content-length": str(MAX_ATTACHMENT_BYTES * 4)},
        )
        assert resp.status_code == 413
        assert "upload limit" in resp.json()["detail"]


def test_middleware_ignores_a_malformed_content_length() -> None:
    """A garbage header must not become an accidental allow-everything path;
    it falls through to the per-upload chunked guard."""
    with TestClient(_app()) as client:
        resp = client.post("/echo", content=b"hi", headers={"content-length": "2"})
        assert resp.status_code == 200
