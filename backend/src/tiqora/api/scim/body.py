"""Request body handling for the SCIM endpoint: content type, size limit, JSON object."""

from __future__ import annotations

import json
from typing import Any, Final

from fastapi import Request

from tiqora.identity.scim.errors import ScimError

_ACCEPTED: Final = frozenset({"application/scim+json", "application/json"})


def check_content_type(request: Request) -> None:
    """415 unless the media type is ``application/scim+json`` or ``application/json``."""
    raw = request.headers.get("content-type", "")
    media = raw.split(";", 1)[0].strip().lower()
    if media not in _ACCEPTED:
        raise ScimError(415, None, "Content-Type must be application/scim+json")


async def read_scim_json(request: Request, max_bytes: int) -> dict[str, Any]:
    """Read the JSON object body in chunks; 413 over ``max_bytes``, 400 on bad JSON.

    A lone surrogate in a string (valid JSON, not encodable) is rejected here: it would
    otherwise fail later at the database driver and answer 500.
    """
    check_content_type(request)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ScimError(413, None, "Request body too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise ScimError(413, None, "Request body too large")
        chunks.append(chunk)
    try:
        doc = json.loads(b"".join(chunks))
    except (ValueError, RecursionError):
        raise ScimError(400, "invalidSyntax", "Body is not valid JSON") from None
    if not isinstance(doc, dict):
        raise ScimError(400, "invalidSyntax", "Body must be a JSON object")
    try:
        json.dumps(doc, ensure_ascii=False).encode("utf-8")
    except (UnicodeEncodeError, RecursionError):
        raise ScimError(400, "invalidValue", "Body contains invalid characters") from None
    return doc
