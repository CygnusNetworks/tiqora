"""Customer identity verification for ``identity_mode=clarify_schema`` (Task 6).

Enforcement is wired only for Telegram source articles (see
``tiqora.ai.runtime.run_ticket_agent``) — email/other channels already have a
somewhat trustworthy sender identity (verified inbound address / portal
login), Telegram chats do not until a human/AI has matched the chat to a
``customer_user`` row via :data:`TiqoraAiQueuePolicy.clarify_schema_json`.

``clarify_schema_json`` is validated at policy save time
(:mod:`tiqora.ai.policies`) against the *real* columns of the ``customer_user``
table — introspected via a ``SELECT * ... LIMIT 0`` (DB-independent, mirrors
the ``SELECT *`` pattern already used in
``tiqora.channels.email.placeholder``), not a hardcoded column list.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import IDENTITY_CLARIFY_SCHEMA, TiqoraAiQueuePolicy, TiqoraAiTicketState
from tiqora.channels.telegram.outbound import TelegramDeliveryError, resolve_chat_id
from tiqora.db.tiqora.models import TiqoraTelegramContact

# Max failed identity-claim attempts before the run escalates to a
# human-reviewed draft instead of asking again (plan: identity verification).
MAX_IDENTITY_ATTEMPTS = 3

_COLUMN_NAME_RE = re.compile(r"^[a-z0-9_]+$")

_TELEGRAM_CHANNEL = "telegram"


@dataclass(frozen=True, slots=True)
class ClarifySchemaField:
    column: str
    label: str


def valid_column_name(column: str) -> bool:
    return bool(_COLUMN_NAME_RE.match(column))


async def get_customer_user_columns(session: AsyncSession) -> frozenset[str]:
    """Real column names of the ``customer_user`` table, DB-independent."""
    result = await session.execute(text("SELECT * FROM customer_user LIMIT 0"))
    return frozenset(str(k) for k in result.keys())  # noqa: SIM118 — CursorResult.keys(), not a dict


def parse_clarify_schema(policy: TiqoraAiQueuePolicy) -> list[ClarifySchemaField] | None:
    """Parse ``policy.clarify_schema_json`` into a field list, or ``None`` if
    unset/malformed (validation already happened at save time in
    :mod:`tiqora.ai.policies` — this is a tolerant runtime read)."""
    raw = (policy.clarify_schema_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    fields_raw = parsed.get("fields")
    if not isinstance(fields_raw, list) or not fields_raw:
        return None
    fields: list[ClarifySchemaField] = []
    for item in fields_raw:
        if not isinstance(item, dict):
            continue
        column = item.get("column")
        label = item.get("label")
        if (
            isinstance(column, str)
            and valid_column_name(column)
            and isinstance(label, str)
            and label
        ):
            fields.append(ClarifySchemaField(column=column, label=label))
    return fields or None


async def is_identified(
    session: AsyncSession,
    ticket_id: int,
    *,
    source_channel: str | None,
    policy: TiqoraAiQueuePolicy,
) -> bool:
    """``identity_mode`` off/ticket_customer_id (or a non-Telegram source) ->
    no active check, always identified. ``clarify_schema`` on Telegram ->
    identified iff the ticket's Telegram contact already has a
    ``customer_user_login`` mapped."""
    if policy.identity_mode != IDENTITY_CLARIFY_SCHEMA:
        return True
    if (source_channel or "").strip().lower() != _TELEGRAM_CHANNEL:
        return True
    try:
        chat_id = await resolve_chat_id(session, ticket_id)
    except TelegramDeliveryError:
        return False
    contact = (
        await session.execute(
            select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == chat_id)
        )
    ).scalar_one_or_none()
    return bool(contact and contact.customer_user_login)


async def verify_identity_claim(
    session: AsyncSession,
    fields: list[ClarifySchemaField],
    values: dict[str, str],
) -> str | None:
    """Deterministic match: every configured field must equal (case/trim
    insensitive) the claimed value, against a *valid* (``valid_id = 1``)
    ``customer_user`` row. Returns the login on exactly one match, else
    ``None`` (no match or ambiguous)."""
    if not fields:
        return None
    conditions: list[str] = []
    params: dict[str, str] = {}
    for idx, field in enumerate(fields):
        if not valid_column_name(field.column):
            # Defensive — clarify_schema_json is validated at save time, but
            # never interpolate an unvalidated column name into SQL.
            return None
        value = values.get(field.column)
        if not isinstance(value, str) or not value.strip():
            return None
        conditions.append(f"TRIM(LOWER(cu.{field.column})) = TRIM(LOWER(:v{idx}))")
        params[f"v{idx}"] = value.strip()
    where_clause = " AND ".join(conditions)
    rows = (
        await session.execute(
            text(f"SELECT cu.login FROM customer_user cu WHERE {where_clause} AND cu.valid_id = 1"),
            params,
        )
    ).all()
    if len(rows) == 1:
        return str(rows[0][0])
    return None


async def get_customer_id_for_login(session: AsyncSession, login: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT customer_id FROM customer_user WHERE login = :login LIMIT 1"),
            {"login": login},
        )
    ).first()
    return str(row[0]) if row is not None and row[0] else None


async def record_identity_attempt(session: AsyncSession, ticket_state: TiqoraAiTicketState) -> int:
    """Increment ``identity_attempts`` and return the new value. Caller
    commits."""
    ticket_state.identity_attempts = (ticket_state.identity_attempts or 0) + 1
    return ticket_state.identity_attempts


__all__ = [
    "MAX_IDENTITY_ATTEMPTS",
    "ClarifySchemaField",
    "get_customer_id_for_login",
    "get_customer_user_columns",
    "is_identified",
    "parse_clarify_schema",
    "record_identity_attempt",
    "valid_column_name",
    "verify_identity_claim",
]
