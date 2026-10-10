"""Public, anonymous chat API under ``/api/chat/v1`` (C8) — used only by the widget frame.

Every call comes from the chat frame, which runs on Tiqora's own origin, so there are no
cookies and no CORS: unsafe methods must carry a same-origin ``Origin`` (R-ORIGIN), and a
visitor proves a session with the ``X-Chat-Token`` header only.

Fixed check order (R-PREAUTH):

1. per-IP-hash backstop ``public`` 300/min (Redis, FAIL CLOSED) — router dependency, before
   any DB access;
2. same-origin check — router dependency;
3. channel switch + config row from the in-process cache (``config_cache``);
4. the session token (identical ``404 {"detail": "not_found"}`` for a wrong, foreign,
   garbled or missing token and for unknown or purged sessions; each failure spends the
   per-IP ``auth_fail`` bucket);
5. per-session limits, keyed only after the token matched.

Request bodies are bounded before FastAPI reads them (``chat_body_limit_middleware``:
JSON ≤ 64 KiB, uploads need a ``Content-Length`` ≤ 25 MiB + 64 KiB; R-UPLOAD).

Transactions (R-TX): handlers never open ``db.begin()``. Pre-checks read in the autobegun
transaction and the service writes in the SAME transaction, which the handler commits (or
rolls back on a refusal); post-commit notifications are flushed afterwards.

Raw client addresses are never stored or used as Redis keys — only ``hash_ip`` digests.
Visitor responses are built only from :mod:`tiqora.webchat.schemas` (``message_out`` is the
only transcript serializer).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Final, Literal, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi import UploadFile as FastAPIUploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from tiqora.api.attachment_response import safe_attachment_response
from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.uploads import MAX_ATTACHMENT_BYTES, read_upload_within_limit
from tiqora.api.v1.events import RedisDep
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession
from tiqora.jobs.service import utc_now
from tiqora.security.ratelimit import client_ip
from tiqora.storage.backend import DbMimeStorage
from tiqora.webchat.availability import VISITOR_TYPING_PREFIX, config_availability
from tiqora.webchat.config_cache import CachedConfig, public_config_cache
from tiqora.webchat.hub import StreamLimitReached
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.notify import flush_chat_notifications, queue_agent_event
from tiqora.webchat.origins import (
    OriginError,
    origin_allowed,
    own_origin,
    referer_origin,
    request_same_origin_ok,
)
from tiqora.webchat.ratelimit import (
    AUTH_FAIL_LIMIT,
    AUTH_FAIL_SCOPE,
    AUTH_FAIL_WINDOW,
    ChatBackendUnavailable,
    ChatRateLimiter,
    RateLimited,
    check_public_backstop,
)
from tiqora.webchat.schemas import (
    ChatAttachmentOut,
    ChatContactIn,
    ChatMessageIn,
    ChatMessageOut,
    ChatOfflineIn,
    ChatOfflineOut,
    ChatPublicConfigOut,
    ChatPublicFieldOut,
    ChatPublicTopicOut,
    ChatPublicUploadOut,
    ChatSessionCreatedOut,
    ChatSessionCreateIn,
    ChatSessionOut,
)
from tiqora.webchat.sessions import (
    CONTACT_END_REASONS,
    CONTACT_WINDOW_SECONDS,
    LIVE_STATUSES,
    ChatError,
    VisitorContext,
    create_session,
    end_by_visitor,
    post_visitor_attachment,
    post_visitor_message,
    set_contact,
    submit_offline,
    touch_visitor_last_seen,
)
from tiqora.webchat.store import get_chat_session, load_messages_since
from tiqora.webchat.text import MAX_MESSAGE_LENGTH
from tiqora.webchat.tokens import (
    CHAT_TOKEN_HEADER,
    hash_ip,
    parse_visitor_token,
    token_matches,
)
from tiqora.webchat.uploads import (
    JSON_BODY_MAX,
    UPLOAD_ENVELOPE_SLACK,
    content_length_within,
    is_raster_image,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

CHAT_PUBLIC_PREFIX: Final = "/api/chat/v1"
UNAVAILABLE_RETRY_AFTER: Final = 30

# Limits that are not admin-editable (C8 / R-UPLOAD).
CONFIG_READS_PER_IP_MINUTE: Final = 60
SESSIONS_PER_VISITOR_10MIN: Final = 3
SESSION_READS_PER_MINUTE: Final = 120
UPLOADS_PER_IP_10MIN: Final = 20
UPLOAD_BYTES_PER_CONFIG_DAY: Final = 500 * 1024 * 1024
TYPING_WINDOW_SECONDS: Final = 2
VISITOR_TYPING_TTL: Final = 6
_UPLOAD_BUDGET_KEY: Final = "tiqora:chat:rl:upload_bytes:cfg"
_DAY: Final = 86400

_SID_RE: Final = re.compile(r"[0-9a-f]{32}")
_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_IP_HASH_STATE: Final = "webchat_ip_hash"

ChatException = ChatError | RateLimited | ChatBackendUnavailable | StreamLimitReached


# -- errors ------------------------------------------------------------------------------------


def chat_error_response(exc: ChatException) -> JSONResponse:
    """The one mapping of chat refusals to HTTP answers.

    ``ChatError`` → its status + ``{"detail": code[, "errors": …]}``; ``RateLimited`` → 429 +
    ``Retry-After`` + ``{"detail": "rate_limited"}``; Redis down / stream caps → 503 +
    ``Retry-After: 30`` + ``{"detail": "chat_unavailable"}``.
    """
    if isinstance(exc, ChatError):
        content: dict[str, Any] = {"detail": exc.code}
        if exc.errors:
            content["errors"] = dict(exc.errors)
        return JSONResponse(content, status_code=exc.http_status)
    if isinstance(exc, RateLimited):
        return JSONResponse(
            {"detail": "rate_limited"},
            status_code=429,
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        )
    return JSONResponse(
        {"detail": "chat_unavailable"},
        status_code=503,
        headers={"Retry-After": str(UNAVAILABLE_RETRY_AFTER)},
    )


def _http_error(exc: RateLimited | ChatBackendUnavailable) -> HTTPException:
    """Same answers as ``chat_error_response`` for refusals raised in router dependencies."""
    if isinstance(exc, RateLimited):
        return HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        )
    return HTTPException(
        status_code=503,
        detail="chat_unavailable",
        headers={"Retry-After": str(UNAVAILABLE_RETRY_AFTER)},
    )


# -- request helpers ---------------------------------------------------------------------------


def request_ip_hash(request: Request, settings: Settings) -> str:
    """``hash_ip`` of the client (``TIQORA_TRUSTED_PROXIES`` honoured), once per request."""
    cached = getattr(request.state, _IP_HASH_STATE, None)
    if isinstance(cached, str):
        return cached
    value = hash_ip(client_ip(request, settings.trusted_proxies), settings.secret_key)
    setattr(request.state, _IP_HASH_STATE, value)
    return value


def visitor_context(request: Request, settings: Settings) -> VisitorContext:
    """Visitor metadata of a request; page URL and language come from the request body."""
    return VisitorContext(
        ip_hash=request_ip_hash(request, settings),
        user_agent=request.headers.get("user-agent"),
        page_url=None,
        language=None,
    )


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    return factory if factory is not None else get_session_factory()


# -- router dependencies (R-PREAUTH steps 1 and 2) ---------------------------------------------


async def public_backstop(request: Request, settings: AppSettings, redis_client: RedisDep) -> None:
    """Spend the per-IP-hash ``public`` bucket before anything touches the DB."""
    try:
        await check_public_backstop(
            ChatRateLimiter(redis_client), request_ip_hash(request, settings)
        )
    except (RateLimited, ChatBackendUnavailable) as exc:
        raise _http_error(exc) from exc


async def require_same_origin(request: Request, settings: AppSettings) -> None:
    """A foreign/missing (unsafe methods) ``Origin`` → ``403 {"detail": "cross_origin"}``."""
    try:
        ok = request_same_origin_ok(request, settings)
    except OriginError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="cross_origin")


router = APIRouter(
    tags=["webchat-public"],
    dependencies=[Depends(public_backstop), Depends(require_same_origin)],
)


async def chat_body_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Bound public chat bodies BEFORE they are read (R-UPLOAD).

    Uploads need a ``Content-Length`` ≤ 25 MiB + 64 KiB (the per-config limit is checked in
    the handler); every other body ≤ 64 KiB. Chunked bodies are refused — the widget always
    sends a length.
    """
    path = request.url.path
    if path.startswith(CHAT_PUBLIC_PREFIX + "/") and request.method.upper() in _UNSAFE_METHODS:
        raw = request.headers.get("content-length")
        chunked = request.headers.get("transfer-encoding") is not None
        if path.endswith("/attachments"):
            if chunked or not content_length_within(
                raw, MAX_ATTACHMENT_BYTES + UPLOAD_ENVELOPE_SLACK
            ):
                return _upload_too_large()
        elif chunked or (
            raw is not None and not content_length_within(raw, JSON_BODY_MAX, minimum=0)
        ):
            return JSONResponse({"detail": "body_too_large"}, status_code=413)
    return await call_next(request)


def _upload_too_large() -> JSONResponse:
    return JSONResponse({"detail": "upload_rejected", "errors": {"file": "size"}}, status_code=413)


# -- checks inside the handlers (R-PREAUTH steps 3-5) ------------------------------------------


async def _require_channel(db: AsyncSession) -> None:
    if not await public_config_cache.channel_enabled(db):
        raise ChatError("disabled", 404)


async def _config_by_key(db: AsyncSession, key: str) -> CachedConfig:
    await _require_channel(db)
    cached = await public_config_cache.by_key(db, key)
    if cached is None:
        raise ChatError("not_found", 404)
    return cached


async def visitor_session(
    session_id: str,
    request: Request,
    db: AsyncSession,
    *,
    limiter: ChatRateLimiter,
    ip_hash: str,
) -> TiqoraChatSession:
    """The session the ``X-Chat-Token`` proves, else ``ChatError("not_found", 404)``.

    The token's sid must equal the path id and its secret must match the stored digest;
    purged sessions count as unknown. Every failure spends the per-IP ``auth_fail`` bucket
    (60 / 10 min) — so guessing ends in 429 — and answers exactly like an unknown session.
    """
    parsed = parse_visitor_token(request.headers.get(CHAT_TOKEN_HEADER))
    chat: TiqoraChatSession | None = None
    if parsed is not None and parsed[0] == session_id and _SID_RE.fullmatch(session_id):
        chat = await get_chat_session(db, session_id)
        if chat is not None and (
            chat.purged_at is not None or not token_matches(parsed[1], chat.token_hash)
        ):
            chat = None
    if chat is None:
        await limiter.hit(
            AUTH_FAIL_SCOPE, ip_hash, limit=AUTH_FAIL_LIMIT, window_seconds=AUTH_FAIL_WINDOW
        )
        raise ChatError("not_found", 404)
    return chat


async def _authorised(
    session_id: str,
    request: Request,
    db: AsyncSession,
    limiter: ChatRateLimiter,
    settings: Settings,
) -> tuple[TiqoraChatSession, CachedConfig]:
    await _require_channel(db)
    chat = await visitor_session(
        session_id, request, db, limiter=limiter, ip_hash=request_ip_hash(request, settings)
    )
    cached = await public_config_cache.by_id(db, int(chat.config_id))
    if cached is None:
        raise ChatError("not_found", 404)
    return chat, cached


async def _session_read_limit(limiter: ChatRateLimiter, session_id: str) -> None:
    await limiter.hit("session_read", session_id, limit=SESSION_READS_PER_MINUTE, window_seconds=60)


async def _message_limits(
    limiter: ChatRateLimiter, values: ChatConfigValues, session_id: str, ip_hash: str
) -> None:
    rl = values.rate_limits
    await limiter.hit(
        "message", session_id, limit=rl.messages_per_session_minute, window_seconds=60
    )
    await limiter.hit("message_ip", ip_hash, limit=rl.messages_per_ip_minute, window_seconds=60)


async def _spend_upload_budget(redis_client: Any, config_id: int, nbytes: int) -> None:
    """Per-config daily upload volume (R-UPLOAD); above it → 503 ``chat_unavailable``."""
    key = f"{_UPLOAD_BUDGET_KEY}{config_id}"
    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.incrby(key, nbytes)
            pipe.ttl(key)
            total, ttl = await pipe.execute()
        if ttl is None or int(ttl) < 0:
            await redis_client.expire(key, _DAY)
    except Exception as exc:  # noqa: BLE001 - fail closed like the rate limiter
        raise ChatBackendUnavailable(str(exc)) from exc
    if int(total) > UPLOAD_BYTES_PER_CONFIG_DAY:
        logger.warning("webchat_upload_budget_exhausted", config_id=config_id)
        raise ChatBackendUnavailable("upload budget exhausted")


# -- serializers -------------------------------------------------------------------------------


def _json_obj(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _attachments_out(raw: str | None) -> list[ChatAttachmentOut]:
    data = _json_obj(raw)
    out: list[ChatAttachmentOut] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                ChatAttachmentOut(
                    index=int(item["index"]),
                    filename=str(item["filename"]),
                    content_type=str(item["content_type"]),
                    size=int(item["size"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def message_out(m: TiqoraChatMessage) -> ChatMessageOut:
    """The ONLY visitor serializer of a transcript line.

    System lines carry their code (``body`` column) and params; agent lines the agent's
    display name the agent side stored in ``meta`` (already ``None`` when the config hides
    names); never a user id, article id or ticket data.
    """
    meta = _json_obj(m.meta)
    meta = meta if isinstance(meta, dict) else {}
    sender: Literal["visitor", "agent", "system"] = (
        "visitor" if m.sender == "visitor" else "agent" if m.sender == "agent" else "system"
    )
    if sender == "system":
        body: str | None = None
        code: str | None = m.body if m.sender == "system" else None
        params = {str(k): v for k, v in meta.items() if v is None or isinstance(v, (str, bool))}
    else:
        body, code, params = m.body, None, {}
    agent_name = meta.get("agent_name") if sender == "agent" else None
    return ChatMessageOut(
        seq=int(m.seq),
        sender=sender,
        body=body,
        code=code,
        params=params,
        agent_name=agent_name if isinstance(agent_name, str) and agent_name else None,
        created=m.created,
        attachments=_attachments_out(m.attachments),
        client_id=m.client_id if sender == "visitor" else None,
    )


def _needs_contact(chat: TiqoraChatSession, now: datetime) -> bool:
    ended_at = chat.ended_at
    return (
        chat.status in ("ended", "abandoned")
        and chat.end_reason in CONTACT_END_REASONS
        and ended_at is not None
        and (now - ended_at).total_seconds() < CONTACT_WINDOW_SECONDS
        and not chat.visitor_email
    )


async def session_agent_name(
    db: AsyncSession, chat: TiqoraChatSession, values: ChatConfigValues
) -> str | None:
    """First name of the assigned agent, ``None`` when unassigned or names are hidden."""
    if chat.assigned_user_id is None or not values.show_agent_names:
        return None
    row = (
        await db.execute(
            text("SELECT first_name FROM users WHERE id = :u"),
            {"u": int(chat.assigned_user_id)},
        )
    ).first()
    name = str(row[0]).strip() if row is not None and row[0] else ""
    return name or None


async def session_out(
    db: AsyncSession, chat: TiqoraChatSession, values: ChatConfigValues, now: datetime
) -> ChatSessionOut:
    topic_label = next((t.label for t in values.topics if t.key == chat.topic_key), None)
    return ChatSessionOut(
        session_id=chat.id,
        status=chat.status,
        agent_name=await session_agent_name(db, chat, values),
        topic_label=topic_label,
        can_send=chat.status in LIVE_STATUSES,
        needs_contact=_needs_contact(chat, now),
        last_seq=int(chat.last_seq),
    )


def _public_config(values: ChatConfigValues, availability: dict[str, bool]) -> ChatPublicConfigOut:
    topics = [
        ChatPublicTopicOut(key=t.key, label=t.label, online=bool(availability.get(t.key)))
        for t in values.topics
    ]
    policy = values.upload_policy
    return ChatPublicConfigOut(
        greeting=values.greeting,
        privacy_url=values.privacy_url,
        theme=values.theme,
        topics=topics,
        online=any(t.online for t in topics),
        offline_mode=values.offline_mode,
        prechat_fields=[
            ChatPublicFieldOut(
                key=f.key, kind=f.kind, label=f.label, required=f.required, options=f.options
            )
            for f in values.prechat_fields
        ],
        upload=ChatPublicUploadOut(
            enabled=policy.enabled, max_bytes=policy.max_bytes, types=list(policy.types)
        ),
        max_message_length=MAX_MESSAGE_LENGTH,
    )


def _is_own_origin(origin: str, request: Request, settings: Settings) -> bool:
    try:
        own = own_origin(request, settings)
    except OriginError:
        return False
    return referer_origin(origin) == own


async def _ticket_queue_id(db: AsyncSession, chat: TiqoraChatSession) -> int:
    """The ticket's CURRENT queue (R-QUEUE-SOT) for agent-bus events."""
    row = (
        await db.execute(
            text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": int(chat.ticket_id)}
        )
    ).first()
    return int(row[0]) if row is not None else int(chat.queue_id)


async def _commit_and_flush(db: AsyncSession, redis_client: Any) -> None:
    await db.commit()
    await flush_chat_notifications(db, redis_client)


async def _refused(db: AsyncSession, exc: ChatException) -> JSONResponse:
    await db.rollback()
    return chat_error_response(exc)


# -- endpoints ---------------------------------------------------------------------------------

_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "Cross-origin request or website not allowed"},
    404: {"description": "Unknown, switched off or not yours"},
    429: {"description": "Rate limited (Retry-After)"},
    503: {"description": "Chat temporarily unavailable (Retry-After)"},
}

SessionId = Annotated[str, Path()]


@router.get("/config", response_model=ChatPublicConfigOut, responses=_ERRORS)
async def get_public_config(
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
    k: Annotated[str, Query(max_length=40)],
    origin: Annotated[str, Query(max_length=200)],
) -> ChatPublicConfigOut | JSONResponse:
    """What the widget shows for config *k* on the website *origin*."""
    limiter = ChatRateLimiter(redis_client)
    try:
        await limiter.hit(
            "config",
            request_ip_hash(request, settings),
            limit=CONFIG_READS_PER_IP_MINUTE,
            window_seconds=60,
        )
        cached = await _config_by_key(db, k)
        values = cached.values
        # R-PREVIEW: Tiqora's own origin may read the (public) config too.
        if not origin_allowed(origin, values.allowed_origins) and not _is_own_origin(
            origin, request, settings
        ):
            raise ChatError("origin", 403)
        availability = await config_availability(
            db, redis_client, SysConfig(db), config=cached.config, values=values, now=utc_now()
        )
        await db.rollback()
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return _public_config(values, availability)


@router.post("/sessions", status_code=201, response_model=ChatSessionCreatedOut, responses=_ERRORS)
async def create_chat_session(
    payload: ChatSessionCreateIn,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> ChatSessionCreatedOut | JSONResponse:
    """Start a chat; 409 ``offline`` when nobody can take it (use the offline form)."""
    limiter = ChatRateLimiter(redis_client)
    ip_hash = request_ip_hash(request, settings)
    try:
        cached = await _config_by_key(db, payload.key)
        rl = cached.values.rate_limits
        await limiter.hit(
            "session_create_ip", ip_hash, limit=rl.sessions_per_ip_10min, window_seconds=600
        )
        await limiter.hit(
            "session_create_visitor",
            payload.visitor_id,
            limit=SESSIONS_PER_VISITOR_10MIN,
            window_seconds=600,
        )
        await limiter.hit(
            "session_create_config",
            f"cfg{cached.config.id}",
            limit=rl.sessions_per_config_minute,
            window_seconds=60,
        )
        chat, token, first = await create_session(
            db,
            _session_factory(request),
            SysConfig(db),
            redis_client,
            config=cached.config,
            values=cached.values,
            payload=payload,
            ctx=visitor_context(request, settings),
            now=utc_now(),
        )
        out = ChatSessionCreatedOut(
            session_id=chat.id, token=token, status=chat.status, message=message_out(first)
        )
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return out


@router.get("/sessions/{session_id}", response_model=ChatSessionOut, responses=_ERRORS)
async def get_chat_session_state(
    session_id: SessionId,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> ChatSessionOut | JSONResponse:
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, cached = await _authorised(session_id, request, db, limiter, settings)
        await _session_read_limit(limiter, chat.id)
        now = utc_now()
        await touch_visitor_last_seen(db, chat, now)
        out = await session_out(db, chat, cached.values, now)
        await db.commit()
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return out


@router.get(
    "/sessions/{session_id}/messages", response_model=list[ChatMessageOut], responses=_ERRORS
)
async def list_chat_messages(
    session_id: SessionId,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> list[ChatMessageOut] | JSONResponse:
    """Transcript lines with ``seq > after`` in order."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, _cached = await _authorised(session_id, request, db, limiter, settings)
        await _session_read_limit(limiter, chat.id)
        rows = await load_messages_since(db, chat.id, after, limit)
        await touch_visitor_last_seen(db, chat, utc_now())
        await db.commit()
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return [message_out(m) for m in rows]


@router.post(
    "/sessions/{session_id}/messages",
    status_code=201,
    response_model=ChatMessageOut,
    responses=_ERRORS,
)
async def post_chat_message(
    session_id: SessionId,
    payload: ChatMessageIn,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> ChatMessageOut | JSONResponse:
    """One visitor line; a retry with the same ``client_id`` returns the stored line."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, cached = await _authorised(session_id, request, db, limiter, settings)
        await _message_limits(limiter, cached.values, chat.id, request_ip_hash(request, settings))
        message = await post_visitor_message(
            db,
            SysConfig(db),
            chat=chat,
            body=payload.body,
            client_id=payload.client_id,
            now=utc_now(),
        )
        out = message_out(message)
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return out


@router.post("/sessions/{session_id}/typing", status_code=204, responses=_ERRORS)
async def post_chat_typing(
    session_id: SessionId,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> Response:
    """The visitor is typing; at most one signal per 2 s, the rest is dropped silently."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, _cached = await _authorised(session_id, request, db, limiter, settings)
        if chat.status not in LIVE_STATUSES:
            raise ChatError("session_closed", 409)
        if await limiter.try_hit("typing", chat.id, window_seconds=TYPING_WINDOW_SECONDS):
            try:
                await redis_client.set(
                    f"{VISITOR_TYPING_PREFIX}{chat.id}", "1", ex=VISITOR_TYPING_TTL
                )
            except Exception:  # noqa: BLE001 - a typing marker is best-effort
                logger.warning("webchat_visitor_typing_mark_failed")
            queue_agent_event(
                db,
                {
                    "type": "chat_typing",
                    "session_id": chat.id,
                    "ticket_id": int(chat.ticket_id),
                    "queue_id": await _ticket_queue_id(db, chat),
                },
            )
        await touch_visitor_last_seen(db, chat, utc_now())
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return Response(status_code=204)


_UPLOAD_BODY: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "caption": {"type": "string"},
                        "client_id": {"type": "string", "maxLength": 64},
                    },
                }
            }
        },
    }
}


def _form_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@router.post(
    "/sessions/{session_id}/attachments",
    status_code=201,
    response_model=ChatMessageOut,
    responses={**_ERRORS, 413: {"description": "File too large"}},
    openapi_extra=_UPLOAD_BODY,
)
async def post_chat_attachment(
    session_id: SessionId,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> ChatMessageOut | JSONResponse:
    """One file (multipart ``file``, optional ``caption``/``client_id``) = one visitor line.

    Takes the raw request so nothing is parsed before the length, token and limit checks
    (R-UPLOAD).
    """
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, cached = await _authorised(session_id, request, db, limiter, settings)
        policy = cached.values.upload_policy
        raw_length = request.headers.get("content-length")
        if not content_length_within(raw_length, policy.max_bytes + UPLOAD_ENVELOPE_SLACK):
            raise ChatError("upload_rejected", 413, errors={"file": "size"})
        if not policy.enabled:
            raise ChatError("upload_rejected", 422, errors={"file": "disabled"})
        await limiter.hit(
            "upload",
            chat.id,
            limit=cached.values.rate_limits.uploads_per_session_10min,
            window_seconds=600,
        )
        await limiter.hit(
            "upload_ip",
            request_ip_hash(request, settings),
            limit=UPLOADS_PER_IP_10MIN,
            window_seconds=600,
        )
        await _spend_upload_budget(redis_client, int(cached.config.id), int(raw_length or 0))
        try:
            async with request.form(max_files=1, max_fields=3) as form:
                upload = form.get("file")
                if not isinstance(upload, UploadFile):
                    raise ChatError("upload_rejected", 422, errors={"file": "empty"})
                try:
                    content = await read_upload_within_limit(
                        cast(FastAPIUploadFile, upload), policy.max_bytes + 1
                    )
                except HTTPException as exc:  # R-UPLOAD-413: one shape for "too large"
                    raise ChatError("upload_rejected", 413, errors={"file": "size"}) from exc
                filename = upload.filename or ""
                caption = _form_text(form.get("caption"))
                client_id = _form_text(form.get("client_id"))
        except StarletteHTTPException as exc:  # malformed multipart, too many parts
            raise ChatError("upload_rejected", 422, errors={"file": "type"}) from exc
        message = await post_visitor_attachment(
            db,
            SysConfig(db),
            chat=chat,
            values=cached.values,
            filename=filename,
            content=content,
            caption=caption,
            client_id=client_id,
            now=utc_now(),
        )
        out = message_out(message)
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return out


@router.get(
    "/sessions/{session_id}/messages/{seq}/attachments/{index}",
    responses={**_ERRORS, 200: {"content": {"application/octet-stream": {}}}},
    response_class=Response,
)
async def get_chat_attachment(
    session_id: SessionId,
    seq: Annotated[int, Path(ge=1)],
    index: Annotated[int, Path(ge=0, le=100)],
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> Response:
    """A file of this session's transcript (raster images inline, everything else as a
    download; ``nosniff`` + ``CSP: sandbox`` always)."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, _cached = await _authorised(session_id, request, db, limiter, settings)
        await _session_read_limit(limiter, chat.id)
        message = (
            await db.execute(
                select(TiqoraChatMessage).where(
                    TiqoraChatMessage.session_id == chat.id, TiqoraChatMessage.seq == seq
                )
            )
        ).scalar_one_or_none()
        listing = _attachments_out(message.attachments) if message is not None else []
        entry = next((a for a in listing if a.index == index), None)
        if message is None or entry is None or message.article_id is None:
            raise ChatError("not_found", 404)
        storage = DbMimeStorage(db)
        stored = await storage.list_attachments(int(message.article_id))
        if index >= len(stored):
            raise ChatError("not_found", 404)
        blob = await storage.get_attachment(stored[index].id)
        if blob is None:
            raise ChatError("not_found", 404)
        await touch_visitor_last_seen(db, chat, utc_now())
        await db.commit()
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    disposition = "inline" if is_raster_image(entry.content_type) else "attachment"
    response = safe_attachment_response(
        blob.content, entry.content_type, entry.filename, disposition
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.post("/sessions/{session_id}/contact", status_code=204, responses=_ERRORS)
async def post_chat_contact(
    session_id: SessionId,
    payload: ChatContactIn,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> Response:
    """Leave an e-mail address after a chat ended without an answer."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, cached = await _authorised(session_id, request, db, limiter, settings)
        await _message_limits(limiter, cached.values, chat.id, request_ip_hash(request, settings))
        await set_contact(
            db, SysConfig(db), chat=chat, name=payload.name, email=payload.email, now=utc_now()
        )
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/end", status_code=204, responses=_ERRORS)
async def post_chat_end(
    session_id: SessionId,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> Response:
    """The visitor closes the chat (idempotent)."""
    limiter = ChatRateLimiter(redis_client)
    try:
        chat, cached = await _authorised(session_id, request, db, limiter, settings)
        await _message_limits(limiter, cached.values, chat.id, request_ip_hash(request, settings))
        await end_by_visitor(db, chat=chat, now=utc_now())
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return Response(status_code=204)


@router.post("/offline", status_code=201, response_model=ChatOfflineOut, responses=_ERRORS)
async def post_offline_form(
    payload: ChatOfflineIn,
    request: Request,
    db: DbSession,
    redis_client: RedisDep,
    settings: AppSettings,
) -> ChatOfflineOut | JSONResponse:
    """Offline form: a ticket with the visitor's message (an e-mail address is required)."""
    limiter = ChatRateLimiter(redis_client)
    try:
        cached = await _config_by_key(db, payload.key)
        rl = cached.values.rate_limits
        await limiter.hit(
            "offline_ip",
            request_ip_hash(request, settings),
            limit=rl.offline_per_ip_hour,
            window_seconds=3600,
        )
        await limiter.hit(
            "offline_config",
            f"cfg{cached.config.id}",
            limit=rl.offline_per_config_hour,
            window_seconds=3600,
        )
        await submit_offline(
            db,
            _session_factory(request),
            SysConfig(db),
            config=cached.config,
            values=cached.values,
            payload=payload,
            ctx=visitor_context(request, settings),
            now=utc_now(),
        )
        await _commit_and_flush(db, redis_client)
    except (ChatError, RateLimited, ChatBackendUnavailable) as exc:
        return await _refused(db, exc)
    return ChatOfflineOut()
