"""LLM-Request-Audit — one ``tiqora_ai_audit_log`` row per ``chat()`` call.

:class:`AuditingLlmClient` wraps a production :class:`~tiqora.ai.llm.LlmClient`
(or vision-pre-pass client) and writes an audit row after every call,
success or failure. It is deliberately **not** wired into
:func:`tiqora.ai.kb_wiring.build_llm_client` itself: the :class:`PiiMapper`
used to mask a run's messages is created *after* the client is built (inside
:func:`tiqora.ai.runtime.run_ticket_agent` / :func:`tiqora.ai.summary.summarize_ticket`),
so those two call sites wrap the injected ``llm`` client themselves once the
mapper exists — see their module docstrings. The vision pre-pass client
*is* wrapped centrally in :func:`tiqora.ai.kb_wiring.build_vision_llm_factory`
(no PII masking applies to image requests, only the data-URL redaction
below).

The audit write uses its own :class:`~sqlalchemy.ext.asyncio.AsyncSession`
bound to the **same engine** as the caller's session (not a second
connection to a different database, and not the caller's own session/
transaction) so a slow or failed audit write can never corrupt or block the
run's own DB transaction.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai.llm import LlmClient, LlmHttpError, LlmMessage, LlmResponse
from tiqora.ai.models import TiqoraAiAuditLog, TiqoraLlmProvider
from tiqora.ai.pii import PiiMapper
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)

FEATURE_DRAFT = "draft"
FEATURE_SUMMARY = "summary"
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_VISION = "vision"
FEATURE_TEST = "test"

DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Metadata threaded through to the audit row for a run/feature."""

    feature: str
    run_id: str | None = None
    ticket_id: int | None = None
    queue_id: int | None = None
    acting_user_id: int | None = None
    trigger: str | None = None
    provider_id: int | None = None
    model: str | None = None


def _own_session_factory(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to the same engine as ``session`` — a fresh
    connection/transaction, not a second database (see module docstring).
    ``session.bind`` (not ``session.get_bind()``, which unwraps to the
    *sync* engine SQLAlchemy uses internally) is the original
    :class:`AsyncEngine` passed to the caller's ``async_sessionmaker``."""
    return async_sessionmaker(bind=session.bind, class_=AsyncSession, expire_on_commit=False)


def _redact_image_urls(wire_messages: list[dict[str, Any]]) -> None:
    """Replace ``data:`` image-url content parts in-place with a byte-count
    placeholder — the audit log must never carry raw image bytes."""
    for msg in wire_messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            if not isinstance(image_url, dict):
                continue
            url = image_url.get("url")
            if isinstance(url, str) and url.startswith("data:"):
                image_url["url"] = f"[image: {len(url)} bytes]"


def _pii_counts(mapping: dict[str, str]) -> dict[str, int]:
    """``{token: original}`` -> ``{"EMAIL": 3, "IPV4": 1, ...}`` — parsed from
    the ``[KIND_n]`` token shape (see :class:`tiqora.ai.pii.PiiMapper`)."""
    counts: dict[str, int] = {}
    for token in mapping:
        inner = token.strip("[]")
        kind = inner.rsplit("_", 1)[0] if "_" in inner else inner
        counts[kind] = counts.get(kind, 0) + 1
    return counts


async def write_audit_log(
    session: AsyncSession,
    *,
    settings: Settings,
    context: AuditContext,
    request_json: str,
    response_json: str | None,
    status_code: int | None,
    error: str | None,
    duration_ms: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    pii_mapping: dict[str, str] | None = None,
) -> None:
    """Insert one audit row using a session bound to the same engine as
    ``session`` (a fresh connection/transaction, not ``session`` itself —
    see module docstring). Best-effort: a failed audit write is logged and
    swallowed so it can never take down the LLM call it is instrumenting."""
    provider_name = ""
    if context.provider_id is not None:
        provider_name = (
            await session.execute(
                select(TiqoraLlmProvider.name).where(TiqoraLlmProvider.id == context.provider_id)
            )
        ).scalar_one_or_none() or ""

    pii_map_enc: str | None = None
    pii_counts_json: str | None = None
    if pii_mapping:
        pii_map_enc = encrypt_secret(settings.secret_key, json.dumps(pii_mapping))
        pii_counts_json = json.dumps(_pii_counts(pii_mapping))

    row = TiqoraAiAuditLog(
        run_id=context.run_id,
        provider_id=context.provider_id,
        provider_name=provider_name,
        model=context.model or "",
        feature=context.feature,
        ticket_id=context.ticket_id,
        queue_id=context.queue_id,
        acting_user_id=context.acting_user_id,
        trigger=context.trigger,
        status_code=status_code,
        error=error,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_json=request_json,
        response_json=response_json,
        pii_map_enc=pii_map_enc,
        pii_counts_json=pii_counts_json,
    )
    try:
        factory = _own_session_factory(session)
        async with factory() as audit_session, audit_session.begin():
            audit_session.add(row)
    except Exception:  # noqa: BLE001 — audit logging must never break an LLM call
        logger.exception(
            "ai_audit_write_failed", feature=context.feature, ticket_id=context.ticket_id
        )


class AuditingLlmClient:
    """Wraps an :class:`LlmClient`; writes one audit row per ``chat()`` call
    (success or failure), storing the exact (already PII-masked) wire
    payload — see module docstring."""

    def __init__(
        self,
        inner: LlmClient,
        *,
        settings: Settings,
        context: AuditContext,
        session: AsyncSession,
        pii_mapper: PiiMapper | None = None,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._context = context
        self._session = session
        self._pii_mapper = pii_mapper

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        wire_messages = [m.to_wire() for m in messages]
        _redact_image_urls(wire_messages)
        request_payload: dict[str, Any] = {
            "messages": wire_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            request_payload["tools"] = tools
            if tool_choice is not None:
                request_payload["tool_choice"] = tool_choice

        start = time.monotonic()
        status_code: int | None = None
        error: str | None = None
        response_json: str | None = None
        model_used = self._context.model
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        try:
            response = await self._inner.chat(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            status_code = 200
            model_used = response.model or model_used
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            response_json = json.dumps(
                {
                    "content": response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "finish_reason": response.finish_reason,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                }
            )
            return response
        except LlmHttpError as exc:
            status_code = exc.status_code
            error = str(exc)
            raise
        except Exception as exc:  # noqa: BLE001 — audit every failure mode, then re-raise
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            await write_audit_log(
                self._session,
                settings=self._settings,
                context=replace(self._context, model=model_used),
                request_json=json.dumps(request_payload),
                response_json=response_json,
                status_code=status_code,
                error=error,
                duration_ms=duration_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                pii_mapping=self._pii_mapper.mapping if self._pii_mapper else None,
            )


# ---------------------------------------------------------------------------
# Admin listing / stats / detail / reveal / cleanup
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditLogPage:
    items: list[TiqoraAiAuditLog]
    total: int


async def list_audit_log(
    session: AsyncSession,
    *,
    ts_from: datetime | None = None,
    ts_to: datetime | None = None,
    provider_id: int | None = None,
    feature: str | None = None,
    ticket_id: int | None = None,
    status: str | None = None,  # "ok" | "error"
    page: int = 1,
    page_size: int = 50,
) -> AuditLogPage:
    filters = []
    if ts_from is not None:
        filters.append(TiqoraAiAuditLog.ts >= ts_from)
    if ts_to is not None:
        filters.append(TiqoraAiAuditLog.ts <= ts_to)
    if provider_id is not None:
        filters.append(TiqoraAiAuditLog.provider_id == provider_id)
    if feature is not None:
        filters.append(TiqoraAiAuditLog.feature == feature)
    if ticket_id is not None:
        filters.append(TiqoraAiAuditLog.ticket_id == ticket_id)
    if status == "ok":
        filters.append(TiqoraAiAuditLog.error.is_(None))
    elif status == "error":
        filters.append(TiqoraAiAuditLog.error.is_not(None))

    total = (
        await session.execute(select(func.count()).select_from(TiqoraAiAuditLog).where(*filters))
    ).scalar_one()

    page = max(1, page)
    page_size = max(1, min(500, page_size))
    rows = (
        (
            await session.execute(
                select(TiqoraAiAuditLog)
                .where(*filters)
                .order_by(TiqoraAiAuditLog.ts.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return AuditLogPage(items=list(rows), total=int(total))


@dataclass(frozen=True, slots=True)
class AuditLogStats:
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    error_rate: float
    per_day: list[dict[str, Any]]
    top_model: str | None


async def audit_log_stats(
    session: AsyncSession,
    *,
    ts_from: datetime | None = None,
    ts_to: datetime | None = None,
    provider_id: int | None = None,
    feature: str | None = None,
    ticket_id: int | None = None,
) -> AuditLogStats:
    filters = []
    if ts_from is not None:
        filters.append(TiqoraAiAuditLog.ts >= ts_from)
    if ts_to is not None:
        filters.append(TiqoraAiAuditLog.ts <= ts_to)
    if provider_id is not None:
        filters.append(TiqoraAiAuditLog.provider_id == provider_id)
    if feature is not None:
        filters.append(TiqoraAiAuditLog.feature == feature)
    if ticket_id is not None:
        filters.append(TiqoraAiAuditLog.ticket_id == ticket_id)

    total = (
        await session.execute(select(func.count()).select_from(TiqoraAiAuditLog).where(*filters))
    ).scalar_one()
    errors = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraAiAuditLog)
            .where(*filters, TiqoraAiAuditLog.error.is_not(None))
        )
    ).scalar_one()
    prompt_sum, completion_sum = (
        await session.execute(
            select(
                func.coalesce(func.sum(TiqoraAiAuditLog.prompt_tokens), 0),
                func.coalesce(func.sum(TiqoraAiAuditLog.completion_tokens), 0),
            ).where(*filters)
        )
    ).one()

    day_expr = func.date(TiqoraAiAuditLog.ts)
    per_day_rows = (
        await session.execute(
            select(day_expr, func.count()).where(*filters).group_by(day_expr).order_by(day_expr)
        )
    ).all()
    per_day = [{"date": str(d), "count": int(c)} for d, c in per_day_rows]

    top_model_row = (
        await session.execute(
            select(TiqoraAiAuditLog.model, func.count().label("cnt"))
            .where(*filters, TiqoraAiAuditLog.model != "")
            .group_by(TiqoraAiAuditLog.model)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()
    top_model = top_model_row[0] if top_model_row else None

    return AuditLogStats(
        total_requests=int(total),
        total_prompt_tokens=int(prompt_sum),
        total_completion_tokens=int(completion_sum),
        error_rate=(int(errors) / int(total)) if total else 0.0,
        per_day=per_day,
        top_model=top_model,
    )


async def get_audit_log_entry(session: AsyncSession, entry_id: int) -> TiqoraAiAuditLog | None:
    return await session.get(TiqoraAiAuditLog, entry_id)


class PiiRevealError(Exception):
    """No/undecryptable PII map on this entry."""


async def reveal_pii(
    session: AsyncSession,
    entry: TiqoraAiAuditLog,
    *,
    settings: Settings,
    admin_user_id: int,
) -> dict[str, str]:
    """Decrypt ``pii_map_enc`` and return ``{placeholder: original}``.

    Logs a structured event on every call (successful reveals are a
    sensitive action worth an audit trail of their own) and, when the GDPR
    audit table is reachable, records an ``ai_audit_pii_reveal`` row there
    too — see :mod:`tiqora.gdpr.audit`.
    """
    logger.info(
        "ai_audit_pii_reveal",
        entry_id=entry.id,
        admin_user_id=admin_user_id,
        ticket_id=entry.ticket_id,
    )
    try:
        from tiqora.gdpr.audit import record_audit as record_gdpr_audit

        await record_gdpr_audit(
            session,
            action="ai_audit_pii_reveal",
            target=str(entry.id),
            actor=str(admin_user_id),
            counts={"entry_id": entry.id},
        )
    except Exception:  # noqa: BLE001 — the reveal itself must still succeed
        logger.exception("ai_audit_pii_reveal_gdpr_log_failed", entry_id=entry.id)

    if not entry.pii_map_enc:
        raise PiiRevealError("This entry has no stored PII mapping")
    plaintext = decrypt_secret(settings.secret_key, entry.pii_map_enc)
    if plaintext is None:
        raise PiiRevealError("Stored PII mapping could not be decrypted")
    mapping: dict[str, str] = json.loads(plaintext)
    return mapping


async def cleanup_audit_log(session: AsyncSession, *, retention_days: int) -> int:
    """Delete rows older than ``retention_days``; returns the deleted count."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    result = await session.execute(delete(TiqoraAiAuditLog).where(TiqoraAiAuditLog.ts < cutoff))
    await session.commit()
    # CursorResult at runtime (a DELETE always yields one); mypy only sees the
    # generic Result[Any] return type of session.execute().
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "FEATURE_AUTO_REPLY",
    "FEATURE_DRAFT",
    "FEATURE_SUMMARY",
    "FEATURE_TEST",
    "FEATURE_VISION",
    "MAX_RETENTION_DAYS",
    "MIN_RETENTION_DAYS",
    "AuditContext",
    "AuditLogPage",
    "AuditLogStats",
    "AuditingLlmClient",
    "PiiRevealError",
    "audit_log_stats",
    "cleanup_audit_log",
    "get_audit_log_entry",
    "list_audit_log",
    "reveal_pii",
    "write_audit_log",
]
