"""Visitor-facing API models (C8). Task 6 adds the two inputs its services take;
Task 7 adds the rest of the visitor shapes next to them."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tiqora.webchat.text import MAX_EMAIL_LENGTH, MAX_PAGE_URL_LENGTH
from tiqora.webchat.tokens import VISITOR_ID_RE

VISITOR_ID_PATTERN = VISITOR_ID_RE.pattern


class ChatSessionCreateIn(BaseModel):
    key: str = Field(max_length=40)
    origin: str = Field(max_length=200)
    visitor_id: str = Field(pattern=VISITOR_ID_PATTERN)
    topic: str | None = Field(default=None, max_length=40)
    prechat: dict[str, str | bool] = Field(default_factory=dict, max_length=20)
    message: str = Field(max_length=4000)
    client_id: str | None = Field(default=None, max_length=64)
    page_url: str | None = Field(default=None, max_length=MAX_PAGE_URL_LENGTH)
    language: str | None = Field(default=None, max_length=16)


class ChatOfflineIn(BaseModel):
    """Offline form; the same bounds as ``ChatSessionCreateIn`` (R-INPUT).

    ``email`` defaults to ``""`` so a missing address is answered by the service with
    ``invalid_prechat`` / ``{"email": "required"}`` instead of FastAPI's list shape
    (R-OFFLINE-EMAIL).
    """

    key: str = Field(max_length=40)
    origin: str = Field(max_length=200)
    visitor_id: str = Field(pattern=VISITOR_ID_PATTERN)
    topic: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=200)
    email: str = Field(default="", max_length=MAX_EMAIL_LENGTH)
    message: str = Field(max_length=4000)
    prechat: dict[str, str | bool] = Field(default_factory=dict, max_length=20)
    page_url: str | None = Field(default=None, max_length=MAX_PAGE_URL_LENGTH)
    language: str | None = Field(default=None, max_length=16)
