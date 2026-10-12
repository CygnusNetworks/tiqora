"""Admin CRUD for web-chat configurations, mounted at ``/api/v1/admin/chat-configs`` (C8).

Save-time checks read the Znuny tables (queue, priority, dynamic field, customer user) with
separate statements; nothing here JOINs a ``tiqora_*`` table with a Znuny table (the
production MariaDB mixes collations). Failures answer 422 with
``{"detail": {"errors": {"<path>": "<German message>"}}}`` — the same shape the
path-scoped ``RequestValidationError`` handler (R-ADMIN-422) gives for body validation.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Final

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession, RedisDep
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.crypto.secret import encrypt_secret
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.dynamic_field_values import load_ticket_dynamic_field
from tiqora.jobs.service import utc_now
from tiqora.webchat.bot import bot_availability
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.identity import new_identity_secret
from tiqora.webchat.models import DF_KINDS_FOR_PRECHAT, ChatConfigValues
from tiqora.webchat.origins import OriginError, own_origin
from tiqora.webchat.schemas import (
    ChatAiStatusOut,
    ChatAiTopicStatusOut,
    ChatConfigCreateIn,
    ChatConfigOut,
    ChatConfigUpdateIn,
    ChatIdentitySecretOut,
)
from tiqora.webchat.store import _JSON_COLUMNS, _SCALAR_COLUMNS, config_values

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat-configs", tags=["admin:chat-configs"])

ADMIN_PATH_SUFFIX: Final = "/admin/chat-configs"
_VALUE_ERROR_PREFIX: Final = "Value error, "
_ONE_PORTAL_CHAT: Final = "Es kann nur ein Chat im Kundenportal angezeigt werden."
_OFFLINE_AI_NEEDS_TOPIC: Final = (
    "„KI antwortet allein“ braucht mindestens ein Thema mit „KI antwortet zuerst“."
)


def new_public_key() -> str:
    return "wc_" + secrets.token_urlsafe(24)[:32]


def _errors_422(errors: dict[str, str]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"errors": errors}
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Chat-Konfiguration nicht gefunden"
    )


async def chat_config_validation_handler(request: Request, exc: Exception) -> Response:
    """R-ADMIN-422: body validation errors of this router in the save-time shape.

    Every other path keeps FastAPI's default list shape.
    """
    assert isinstance(exc, RequestValidationError)
    prefix = request.app.state.settings.api_prefix
    if not request.url.path.startswith(prefix + ADMIN_PATH_SUFFIX):
        return await request_validation_exception_handler(request, exc)
    errors: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ())]
        if loc and loc[0] == "body":
            loc = loc[1:]
        if loc and loc[0] == "values":
            loc = loc[1:]
        path = ".".join(loc) or "body"
        msg = str(err.get("msg", ""))
        ctx = err.get("ctx") or {}
        if err.get("type") == "origin_invalid":
            path = f"{path}.{ctx.get('index', 0)}"
            msg = str(ctx.get("message", msg))
        elif err.get("type") == "prechat_key_reserved":
            path = f"{path}.key"
            msg = f"Der Schlüssel „{ctx.get('key', '')}“ ist reserviert."
        elif err.get("type") == "prechat_map_duplicate":
            path = ".".join([*loc, "prechat_fields", str(ctx.get("index", 0)), "maps_to"])
            what = "E-Mail" if ctx.get("target") == "email" else "Name"
            msg = f"Nur ein Feld darf in „{what}“ übernommen werden."
        if msg.startswith(_VALUE_ERROR_PREFIX):
            msg = msg[len(_VALUE_ERROR_PREFIX) :]
        errors.setdefault(path, msg)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": {"errors": errors}}
    )


async def _existing_ids(db: AsyncSession, table: str, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    stmt = text(f"SELECT id FROM {table} WHERE id IN :ids AND valid_id = 1").bindparams(  # noqa: S608
        bindparam("ids", expanding=True)
    )
    return {int(r[0]) for r in (await db.execute(stmt, {"ids": sorted(ids)})).all()}


async def _check_values(db: AsyncSession, values: ChatConfigValues) -> dict[str, str]:
    """Znuny-side existence checks; keys are dotted paths below ``values``."""
    errors: dict[str, str] = {}
    queues = await _existing_ids(db, "queue", {t.queue_id for t in values.topics})
    priorities = await _existing_ids(
        db, "ticket_priority", {t.priority_id for t in values.topics if t.priority_id}
    )
    for i, topic in enumerate(values.topics):
        if topic.queue_id not in queues:
            errors[f"topics.{i}.queue_id"] = "Diese Queue gibt es nicht."
        if topic.priority_id is not None and topic.priority_id not in priorities:
            errors[f"topics.{i}.priority_id"] = "Diese Priorität gibt es nicht."
    for i, field in enumerate(values.prechat_fields):
        if field.dynamic_field is None:
            continue
        df = await load_ticket_dynamic_field(db, field.dynamic_field)
        if df is None:
            errors[f"prechat_fields.{i}.dynamic_field"] = (
                "Dieses Dynamische Feld gibt es nicht (gültiges Ticket-Feld erforderlich)."
            )
        elif df.field_type not in DF_KINDS_FOR_PRECHAT:
            errors[f"prechat_fields.{i}.dynamic_field"] = (
                "Dieser Feldtyp ist im Chat nicht möglich (nur Text, Auswahl, Checkbox)."
            )
    if values.default_customer_user:
        found = (
            await db.execute(
                text("SELECT 1 FROM customer_user WHERE login = :l AND valid_id = 1 LIMIT 1"),
                {"l": values.default_customer_user},
            )
        ).first()
        if found is None:
            errors["default_customer_user"] = "Diesen Kundenbenutzer gibt es nicht."
    if values.offline_mode == "ai" and not any(t.ai_mode == "first_line" for t in values.topics):
        errors["offline_mode"] = _OFFLINE_AI_NEEDS_TOPIC
    return errors


async def _portal_conflict(
    db: AsyncSession, values: ChatConfigValues, *, valid: bool, exclude_id: int | None
) -> dict[str, str]:
    """Only one valid chat may sit in the customer portal."""
    if not (values.show_on_portal and valid):
        return {}
    stmt = select(TiqoraChatConfig.id).where(
        TiqoraChatConfig.show_on_portal.is_(True), TiqoraChatConfig.valid.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(TiqoraChatConfig.id != exclude_id)
    if (await db.execute(stmt.limit(1))).first() is not None:
        return {"show_on_portal": _ONE_PORTAL_CHAT}
    return {}


def _apply_values(row: TiqoraChatConfig, values: ChatConfigValues) -> None:
    dumped: dict[str, Any] = values.model_dump(mode="json")
    for column in _JSON_COLUMNS:
        setattr(row, column, json.dumps(dumped[column]))
    for column in _SCALAR_COLUMNS:
        setattr(row, column, dumped[column])


async def _session_counts(db: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(TiqoraChatSession.config_id, func.count())
            .where(TiqoraChatSession.config_id.in_(ids))
            .group_by(TiqoraChatSession.config_id)
        )
    ).all()
    return {int(r[0]): int(r[1]) for r in rows}


def _snippet(request: Request, settings: Any, public_key: str) -> str:
    try:
        base = own_origin(request, settings)
    except OriginError:
        base = ""
    return f'<script src="{base}/chat/v1/loader.js" data-tiqora-chat="{public_key}" async></script>'


def _to_out(
    request: Request, settings: Any, row: TiqoraChatConfig, session_count: int
) -> ChatConfigOut:
    return ChatConfigOut(
        id=row.id,
        name=row.name,
        public_key=row.public_key,
        valid=row.valid,
        values=config_values(row),
        identity_secret_set=bool(row.identity_secret),
        embed_snippet=_snippet(request, settings, row.public_key),
        session_count=session_count,
        created=row.created,
        changed=row.changed,
    )


async def _out(
    request: Request, settings: Any, db: AsyncSession, row: TiqoraChatConfig
) -> ChatConfigOut:
    counts = await _session_counts(db, [row.id])
    return _to_out(request, settings, row, counts.get(row.id, 0))


async def _name_taken(db: AsyncSession, name: str, *, exclude_id: int | None = None) -> bool:
    stmt = select(TiqoraChatConfig.id).where(TiqoraChatConfig.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TiqoraChatConfig.id != exclude_id)
    return (await db.execute(stmt.limit(1))).first() is not None


async def _get(db: AsyncSession, config_id: int) -> TiqoraChatConfig:
    row = await db.get(TiqoraChatConfig, config_id)
    if row is None:
        raise _not_found()
    return row


@router.get("", response_model=list[ChatConfigOut])
async def list_chat_configs(
    request: Request, admin: AdminUser, db: DbSession, settings: AppSettings
) -> list[ChatConfigOut]:
    _ = admin
    rows = list(
        (await db.execute(select(TiqoraChatConfig).order_by(TiqoraChatConfig.name))).scalars()
    )
    counts = await _session_counts(db, [r.id for r in rows])
    return [_to_out(request, settings, r, counts.get(r.id, 0)) for r in rows]


@router.get("/{config_id}", response_model=ChatConfigOut)
async def get_chat_config(
    config_id: int, request: Request, admin: AdminUser, db: DbSession, settings: AppSettings
) -> ChatConfigOut:
    _ = admin
    return await _out(request, settings, db, await _get(db, config_id))


@router.post("", response_model=ChatConfigOut, status_code=status.HTTP_201_CREATED)
async def create_chat_config(
    body: ChatConfigCreateIn,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    settings: AppSettings,
) -> ChatConfigOut:
    errors = await _check_values(db, body.values)
    errors.update(await _portal_conflict(db, body.values, valid=body.valid, exclude_id=None))
    if await _name_taken(db, body.name):
        errors["name"] = "Dieser Name ist bereits vergeben."
    if errors:
        raise _errors_422(errors)
    now = utc_now()
    row = TiqoraChatConfig(
        name=body.name,
        public_key=new_public_key(),
        valid=body.valid,
        created=now,
        created_by=admin.id,
        changed=now,
        changed_by=admin.id,
    )
    _apply_values(row, body.values)
    db.add(row)
    await db.commit()
    clear_config_cache()
    await db.refresh(row)
    return _to_out(request, settings, row, 0)


@router.patch("/{config_id}", response_model=ChatConfigOut)
async def update_chat_config(
    config_id: int,
    body: ChatConfigUpdateIn,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    settings: AppSettings,
) -> ChatConfigOut:
    row = await _get(db, config_id)
    errors: dict[str, str] = {}
    if body.values is not None:
        errors = await _check_values(db, body.values)
    if body.values is not None or body.valid is not None:
        errors.update(
            await _portal_conflict(
                db,
                body.values if body.values is not None else config_values(row),
                valid=body.valid if body.valid is not None else row.valid,
                exclude_id=config_id,
            )
        )
    if body.name is not None and await _name_taken(db, body.name, exclude_id=config_id):
        errors["name"] = "Dieser Name ist bereits vergeben."
    if errors:
        raise _errors_422(errors)
    if body.name is not None:
        row.name = body.name
    if body.valid is not None:
        row.valid = body.valid
    if body.values is not None:
        _apply_values(row, body.values)
    row.changed = utc_now()
    row.changed_by = admin.id
    await db.commit()
    clear_config_cache()
    await db.refresh(row)
    return await _out(request, settings, db, row)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_config(config_id: int, admin: AdminUser, db: DbSession) -> Response:
    _ = admin
    row = await _get(db, config_id)
    if (await _session_counts(db, [config_id])).get(config_id, 0) > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="in_use")
    await db.delete(row)
    await db.commit()
    clear_config_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{config_id}/rotate-key", response_model=ChatConfigOut)
async def rotate_chat_config_key(
    config_id: int, request: Request, admin: AdminUser, db: DbSession, settings: AppSettings
) -> ChatConfigOut:
    row = await _get(db, config_id)
    row.public_key = new_public_key()
    row.changed = utc_now()
    row.changed_by = admin.id
    await db.commit()
    clear_config_cache()
    await db.refresh(row)
    return await _out(request, settings, db, row)


@router.post(
    "/{config_id}/identity-secret",
    response_model=ChatIdentitySecretOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_identity_secret(
    config_id: int, admin: AdminUser, db: DbSession, settings: AppSettings
) -> ChatIdentitySecretOut:
    """Create or rotate the website sign-in secret; the plain value is in this response only.

    Tokens signed with the previous secret stop working at once on this replica and within the
    config cache TTL (10 s) on the others.
    """
    row = await _get(db, config_id)
    secret = new_identity_secret()
    row.identity_secret = encrypt_secret(settings.secret_key, secret)
    row.changed = utc_now()
    row.changed_by = admin.id
    await db.commit()
    clear_config_cache()
    logger.info("webchat_identity_secret_rotated", config_id=config_id)
    return ChatIdentitySecretOut(secret=secret)


@router.delete("/{config_id}/identity-secret", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_secret(config_id: int, admin: AdminUser, db: DbSession) -> Response:
    row = await _get(db, config_id)
    row.identity_secret = None
    row.changed = utc_now()
    row.changed_by = admin.id
    await db.commit()
    clear_config_cache()
    logger.info("webchat_identity_secret_removed", config_id=config_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{config_id}/ai-status", response_model=ChatAiStatusOut)
async def chat_ai_status(
    config_id: int, admin: AdminUser, db: DbSession, settings: AppSettings, redis_client: RedisDep
) -> ChatAiStatusOut:
    """Per topic: whether the chat assistant would answer there right now, else why not."""
    _ = admin
    row = await _get(db, config_id)
    topics: list[ChatAiTopicStatusOut] = []
    for topic in config_values(row).topics:
        avail = await bot_availability(
            db,
            redis_client,
            settings,
            config_id=config_id,
            topic=topic,
            queue_id=topic.queue_id,
            use_cache=False,
        )
        topics.append(
            ChatAiTopicStatusOut(
                key=topic.key,
                label=topic.label,
                queue_id=topic.queue_id,
                ai_mode=topic.ai_mode,
                ok=avail.ok,
                reason=avail.reason,
            )
        )
    return ChatAiStatusOut(topics=topics)
