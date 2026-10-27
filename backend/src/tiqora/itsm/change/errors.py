"""The one error type of the change module; it already is the HTTP answer.

``ChangeError`` is an ``HTTPException`` whose ``detail`` is ``{**detail, "code": <code>}``, so
services raise it and every route answers ``{"detail": {"code", ...}}`` without a translation
layer. ``code`` and ``http_status`` are positional-only: a detail entry may itself be called
``code`` (the approval adapter passes the engine's code that way): the primary code wins in the
answer, the entry stays readable in ``extra``.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ChangeError(HTTPException):
    def __init__(self, code: str, http_status: int = 409, /, **detail: Any) -> None:
        super().__init__(http_status, detail={**detail, "code": code})
        self.error_code = code
        self.http_status = http_status
        self.extra = detail
