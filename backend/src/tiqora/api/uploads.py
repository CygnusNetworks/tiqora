"""Bounded reading of client uploads (agent KB + customer portal).

``await file.read()`` on an ``UploadFile`` pulls the whole part into memory, so
checking ``len(content)`` afterwards enforces a limit only *after* the damage is
done — a large upload has already been spooled to disk by the multipart parser
and then copied into RAM (security review M-4).

Two layers close that:

* :func:`body_size_limit_middleware` rejects an oversized request on its
  ``Content-Length`` **before** Starlette parses the body at all, so neither the
  disk spool nor the read happens.
* :func:`read_upload_within_limit` reads in chunks and stops at the first byte
  over the limit, which also covers a missing or lying ``Content-Length``
  (chunked transfer encoding).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, UploadFile, status
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Largest single attachment accepted from an agent or a portal customer.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: Headroom over MAX_ATTACHMENT_BYTES for multipart framing and other form
#: fields, so the middleware never rejects a request the endpoint would accept.
_MULTIPART_OVERHEAD_BYTES = 1 * 1024 * 1024

_READ_CHUNK_BYTES = 64 * 1024

CallNext = Callable[[Request], Awaitable[Response]]


def _too_large(max_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Upload exceeds the {max_bytes // (1024 * 1024)} MiB limit",
    )


async def read_upload_within_limit(
    file: UploadFile, max_bytes: int = MAX_ATTACHMENT_BYTES
) -> bytes:
    """Return the upload's bytes, or raise 413 as soon as it grows too large.

    Never materialises more than ``max_bytes`` (+ one chunk) in memory.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


async def body_size_limit_middleware(request: Request, call_next: CallNext) -> Response:
    """Reject oversized request bodies up front, based on ``Content-Length``.

    Only applies to requests that declare a length; a chunked request passes
    through here and is caught by :func:`read_upload_within_limit` instead.
    """
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            length = int(raw_length)
        except ValueError:
            length = 0
        if length > MAX_ATTACHMENT_BYTES + _MULTIPART_OVERHEAD_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "detail": (
                        f"Request body exceeds the "
                        f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB upload limit"
                    )
                },
            )
    return await call_next(request)


__all__ = [
    "MAX_ATTACHMENT_BYTES",
    "body_size_limit_middleware",
    "read_upload_within_limit",
]
