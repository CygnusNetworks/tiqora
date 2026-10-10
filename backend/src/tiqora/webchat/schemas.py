"""Visitor-facing API models (C8) — the ONLY shapes the public chat API returns.

None of them carries a ticket id/number, queue, article id, user id, agent login or e-mail,
or an internal note; ``public_api.message_out`` is the only serializer of a transcript line.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.webchat.models import ChatTheme, PrechatOption
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


class ChatMessageIn(BaseModel):
    body: str = Field(max_length=4000)
    client_id: str | None = Field(default=None, max_length=64)


class ChatContactIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str = Field(default="", max_length=MAX_EMAIL_LENGTH)


# -- responses ---------------------------------------------------------------------------------


class ChatPublicTopicOut(BaseModel):
    key: str
    label: str
    online: bool


class ChatPublicFieldOut(BaseModel):
    key: str
    kind: Literal["text", "textarea", "email", "select", "checkbox"]
    label: str
    required: bool
    options: list[PrechatOption]


class ChatPublicUploadOut(BaseModel):
    enabled: bool
    max_bytes: int
    types: list[str]


class ChatPublicConfigOut(BaseModel):
    """What the widget needs to render; no admin name, ids or queue data."""

    greeting: str | None
    privacy_url: str | None
    theme: ChatTheme
    topics: list[ChatPublicTopicOut]
    online: bool
    offline_mode: Literal["form", "hide"]
    prechat_fields: list[ChatPublicFieldOut]
    upload: ChatPublicUploadOut
    max_message_length: int


class ChatAttachmentOut(BaseModel):
    index: int
    filename: str
    content_type: str
    size: int


class ChatMessageOut(BaseModel):
    seq: int
    sender: Literal["visitor", "agent", "system"]
    body: str | None
    code: str | None
    params: dict[str, str | bool | None]
    agent_name: str | None
    created: UtcDateTime
    attachments: list[ChatAttachmentOut]
    client_id: str | None


class ChatSessionCreatedOut(BaseModel):
    session_id: str
    token: str
    status: str
    message: ChatMessageOut


class ChatSessionOut(BaseModel):
    session_id: str
    status: str
    agent_name: str | None
    topic_label: str | None
    can_send: bool
    needs_contact: bool
    last_seq: int


class ChatOfflineOut(BaseModel):
    """Empty acknowledgement of the offline form (``{}``)."""
