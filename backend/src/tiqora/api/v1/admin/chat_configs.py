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

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.dynamic_field_values import load_ticket_dynamic_field
from tiqora.jobs.service import utc_now
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.models import DF_KINDS_FOR_PRECHAT, ChatConfigValues
from tiqora.webchat.origins import OriginError, own_origin
from tiqora.webchat.schemas import ChatConfigCreateIn, ChatConfigOut, ChatConfigUpdateIn
from tiqora.webchat.store import config_values

router = APIRouter(prefix="/chat-configs", tags=["admin:chat-configs"])

ADMIN_PATH_SUFFIX: Final = "/admin/chat-configs"
_JSON_COLUMNS: Final = (
    "allowed_origins",
    "topics",
    "prechat_fields",
    "upload_policy",
    "theme",
    "rate_limits",
)
_SCALAR_COLUMNS: Final = (
    "greeting",
    "privacy_url",
    "offline_mode",
    "business_calendar",
    "wait_timeout_seconds",
    "default_customer_user",
    "show_agent_names",
    "retention_days",
)
_VALUE_ERROR_PREFIX: Final = "Value error, "


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
    return errors


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
