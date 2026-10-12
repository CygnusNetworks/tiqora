"""Chat API models (C8).

Visitor-facing models (everything above the "agent side" section) are the ONLY shapes the
public chat API returns: none of them carries a ticket id/number, queue, article id, user
id, agent login or e-mail, or an internal note; ``serialize.message_out`` is the only
serializer of a transcript line. The agent-side models at the end are used only by the
authenticated agent API (``tiqora.api.v1.chat``) and never by ``/api/chat/v1``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.webchat.models import DEFAULT_BOT_NAME, ChatConfigValues, ChatTheme, PrechatOption
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
    identity: str | None = Field(default=None, max_length=4096)


class ChatIdentifyIn(BaseModel):
    identity: str = Field(min_length=1, max_length=4096)


class ChatPortalIdentityIn(BaseModel):
    key: str = Field(max_length=40)
    origin: str = Field(max_length=200)
    visitor_id: str = Field(pattern=VISITOR_ID_PATTERN)


class ChatPortalIdentityOut(BaseModel):
    identity: str
    name: str | None


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
    # The AI answers first on this topic right now (topic ``first_line`` and the bot is able to).
    ai_first: bool = False


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
    offline_mode: Literal["form", "ai", "hide"]
    prechat_fields: list[ChatPublicFieldOut]
    upload: ChatPublicUploadOut
    max_message_length: int
    portal_identity: bool = False
    bot_name: str = DEFAULT_BOT_NAME


class ChatAttachmentOut(BaseModel):
    index: int
    filename: str
    content_type: str
    size: int


class ChatMessageOut(BaseModel):
    seq: int
    sender: Literal["visitor", "agent", "bot", "system"]
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
    identified_as: str | None = None
    # The assistant's display name while the AI answers (``bot``), else ``None``.
    bot_name: str | None = None
    # The visitor may press "talk to a human": true exactly while the AI answers.
    can_handoff: bool = False


class ChatOfflineOut(BaseModel):
    """Empty acknowledgement of the offline form (``{}``)."""


# -- agent side (``/api/v1/chat``, authenticated agents only) ------------------------------------


class ChatTopicRefOut(BaseModel):
    """A chat topic the agent may take (``rw`` on its queue); ``ref`` = ``<config_id>:<key>``."""

    ref: str
    config_name: str
    label: str
    queue_id: int


class ChatAgentStateOut(BaseModel):
    available: bool
    topics: list[str]
    max_concurrent: int
    active_count: int


class ChatAgentStateIn(BaseModel):
    """``topics`` empty = every topic the agent may take; refs must come from ``/chat/topics``."""

    available: bool
    topics: list[str] = Field(default_factory=list, max_length=100)
    max_concurrent: int = Field(3, ge=1, le=20)


class ChatInboxItemOut(BaseModel):
    session_id: str
    ticket_id: int
    tn: str
    title: str
    topic_label: str | None
    visitor_name: str | None
    status: Literal["bot", "waiting", "active"]
    waiting_since: UtcDateTime | None
    accepted_at: UtcDateTime | None


class ChatInboxOut(BaseModel):
    waiting: list[ChatInboxItemOut]
    mine: list[ChatInboxItemOut]
    # Chats the AI is answering on queues the agent holds ``rw`` on (newest first); no ring.
    bot: list[ChatInboxItemOut] = Field(default_factory=list)


class ChatAcceptOut(BaseModel):
    ticket_id: int


class ChatEndIn(BaseModel):
    close_ticket: bool = False


class TicketChatOut(BaseModel):
    """The newest chat of a ticket for the agent's conversation header (``ro`` on its queue).

    ``visitor_email_verified`` is True only for a chat with a verified identity
    (``identity_source`` set, signed token or portal login) that carries an address
    (R-NOTIFY b); otherwise the address is what the visitor typed.
    """

    session_id: str
    status: str
    end_reason: str | None
    visitor_name: str | None
    visitor_email: str | None
    visitor_email_verified: bool = False
    identity_source: str | None = None
    # ``visitor`` | ``ai`` | ``limit`` | ``error`` | ``unavailable`` | ``takeover``; the AI's own
    # handoff summary is the internal note on the ticket, never part of this response.
    handoff_reason: str | None = None
    handoff_at: UtcDateTime | None = None
    # The assistant's name when the AI took part in this chat, else ``None``.
    bot_name: str | None = None
    topic_label: str | None
    page_url: str | None
    language: str | None
    waiting_since: UtcDateTime | None
    accepted_at: UtcDateTime | None
    ended_at: UtcDateTime | None
    assigned_user_id: int | None
    assigned_name: str | None
    visitor_online: bool
    visitor_typing: bool


# -- admin side (``/api/v1/admin/chat-configs``, admins only) ------------------------------------


class ChatConfigOut(BaseModel):
    id: int
    name: str
    public_key: str
    valid: bool
    values: ChatConfigValues
    embed_snippet: str
    session_count: int
    created: UtcDateTime
    changed: UtcDateTime


class ChatConfigCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    valid: bool = True
    values: ChatConfigValues


class ChatConfigUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    valid: bool | None = None
    values: ChatConfigValues | None = None
