"""CTI incoming-call popup (spec A2): agent extensions, call state, push.

A PBX reports ``ringing`` / ``answered`` / ``hangup`` per call to
``POST /api/v1/channels/phone/events``. The extension in each event is mapped
to agents through the Znuny ``user_preferences`` key
:data:`PHONE_EXTENSION_PREF` (comma-separated, several per agent allowed; one
extension may belong to several agents, e.g. a shared desk phone).

Call state lives in Redis only — one JSON document per call under
``tiqora:call:<call_id>`` with a 2 h TTL — because it is transient UI state,
not ticket data: nothing is written to the database until the agent logs the
call through the existing phone-call flows. Each change is published as a
``call_event`` on the SSE channel, addressed to explicit ``user_ids``; the SSE
endpoint forwards it only to those agents. The caller lookup (customers, open
tickets) is deliberately *not* part of the event: the frontend asks
``/reference/caller`` so the queue permission logic stays in one place.

State machine per call:

* ``ringing`` — agents of the ringing extension are added (a ring group sends
  one ``ringing`` per extension with the same ``call_id``; the audience grows).
* ``answered`` — the answering extension's agents become the call's owners;
  everybody notified before still receives this event so their card can go.
  When that extension maps to exactly one agent, they are recorded as
  ``answered_by_user_id`` (owner prefill of the phone-ticket form).
* ``hangup`` — the call is ``ended``; the popup keeps offering "log this call"
  for :data:`RECENT_AFTER_HANGUP_SECONDS`.
* ``handled`` — the PBX took the call over itself (secretary, IVR, voicemail):
  the call is ``ended`` and dismissed for everybody notified, so it is neither
  shown as missed nor offered for logging. Ignored for unknown calls and for
  calls an agent already answered.

Events are applied read-modify-write without a Redis transaction: a PBX
reports one call's events in order (the dialplan ``CURL()`` calls are
synchronous), so concurrent writers for the same call id are not expected.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import redis.asyncio as redis
import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.auth import decode_preference_value
from tiqora.domain.schemas import UtcDateTime
from tiqora.events.pubsub import publish_call_event

logger = structlog.get_logger(__name__)

PHONE_EXTENSION_PREF = "TiqoraPhoneExtension"
CALL_KEY_PREFIX = "tiqora:call:"
CALL_TTL_SECONDS = 2 * 60 * 60
RECENT_AFTER_HANGUP_SECONDS = 15 * 60

_EXTENSION_RE = re.compile(r"^[A-Za-z0-9*#+_.@/:-]{1,64}$")
_SPLIT_RE = re.compile(r"[,;\s]+")

CallEventType = Literal["ringing", "answered", "hangup", "handled"]
CallStateName = Literal["ringing", "answered", "ended"]
CallDirection = Literal["inbound", "outbound"]


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


def parse_extensions(raw: str | None) -> list[str]:
    """Split a stored/typed extension list (comma, semicolon or blank separated)."""
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in _SPLIT_RE.split(raw):
        item = part.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def normalize_extensions(raw: str | None) -> str | None:
    """Canonical stored form (``"100,101"``) or ``None`` for "no extension".

    Raises :class:`ValueError` naming the first invalid entry.
    """
    items = parse_extensions(raw)
    for item in items:
        if not _EXTENSION_RE.match(item):
            raise ValueError(f"invalid extension: {item!r}")
    return ",".join(items) or None


def extension_matches(stored: str | None, extension: str) -> bool:
    wanted = extension.strip().lower()
    return bool(wanted) and any(e.lower() == wanted for e in parse_extensions(stored))


async def users_for_extension(session: AsyncSession, extension: str) -> list[int]:
    """Valid agents whose ``TiqoraPhoneExtension`` lists *extension*.

    The preference table is small per key, and values are free text that has to
    be split anyway, so all rows of the key are read and matched here.
    """
    if not extension.strip():
        return []
    rows = await session.execute(
        text(
            "SELECT p.user_id, p.preferences_value FROM user_preferences p"
            " JOIN users u ON u.id = p.user_id"
            " WHERE p.preferences_key = :k AND u.valid_id = 1"
        ),
        {"k": PHONE_EXTENSION_PREF},
    )
    return sorted(
        {
            int(uid)
            for uid, raw in rows.all()
            if extension_matches(decode_preference_value(raw), extension)
        }
    )


# ---------------------------------------------------------------------------
# Call state
# ---------------------------------------------------------------------------


class CallEventIn(BaseModel):
    """Webhook body of ``POST /channels/phone/events``."""

    event: CallEventType
    #: PBX-unique id of the call (Asterisk: ``${UNIQUEID}`` of the caller leg).
    call_id: str = Field(min_length=1, max_length=128)
    #: The other party's number; may be omitted on answered/hangup.
    caller_number: str | None = Field(default=None, max_length=100)
    #: The agent's extension that rings / answered; optional on hangup.
    extension: str | None = Field(default=None, max_length=64)
    direction: CallDirection = "inbound"
    #: When it happened (ISO 8601 or epoch seconds; naive = UTC). Default: now.
    timestamp: datetime | None = None


class ActiveCall(BaseModel):
    """One call as the popup sees it."""

    call_id: str
    state: CallStateName
    number: str
    extension: str | None
    direction: CallDirection
    #: Agents currently handling the call (after ``answered``: the answering ones).
    user_ids: list[int]
    #: The agent who took the call: set on ``answered`` when the answering
    #: extension maps to exactly one agent (a shared desk phone names nobody).
    #: The new-ticket form preselects them as owner.
    answered_by_user_id: int | None = None
    ringing_at: UtcDateTime | None
    answered_at: UtcDateTime | None
    ended_at: UtcDateTime | None


class _StoredCall(ActiveCall):
    #: Everybody who was ever shown this call (receives follow-up events).
    notified_user_ids: list[int] = Field(default_factory=list)
    dismissed_user_ids: list[int] = Field(default_factory=list)

    def public(self) -> ActiveCall:
        return ActiveCall.model_validate(self.model_dump(include=set(ActiveCall.model_fields)))


@dataclass(frozen=True)
class CallEventResult:
    #: Agents the event was published to (empty = ignored).
    recipients: list[int]


def _key(call_id: str) -> str:
    return f"{CALL_KEY_PREFIX}{call_id}"


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _load(redis_client: redis.Redis, call_id: str) -> _StoredCall | None:
    raw = await redis_client.get(_key(call_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return _StoredCall.model_validate_json(raw)
    except ValidationError:
        logger.warning("cti.call_state_unreadable", call_id=call_id)
        return None


async def _store(redis_client: redis.Redis, call: _StoredCall) -> None:
    await redis_client.set(_key(call.call_id), call.model_dump_json(), ex=CALL_TTL_SECONDS)


def _union(a: Sequence[int], b: Sequence[int]) -> list[int]:
    return sorted(set(a) | set(b))


async def _publish(
    redis_client: redis.Redis, recipients: list[int], event: str, call: _StoredCall
) -> None:
    await publish_call_event(
        redis_client,
        user_ids=recipients,
        event=event,
        call=call.public().model_dump(mode="json"),
    )


async def apply_call_event(
    redis_client: redis.Redis,
    body: CallEventIn,
    *,
    resolve_users: Callable[[str], Awaitable[list[int]]],
) -> CallEventResult:
    """Apply one PBX event to the call state and push it to the agents.

    Redis errors propagate (the webhook answers 503); an event for an unknown
    call whose extension maps to nobody is ignored.
    """
    at = _utc(body.timestamp)
    users = await resolve_users(body.extension) if body.extension else []
    call = await _load(redis_client, body.call_id)

    if body.event == "handled" and (call is None or call.state == "answered"):
        logger.debug("cti.event_ignored", call_id=body.call_id, call_event=body.event)
        return CallEventResult(recipients=[])

    if call is None:
        if not users:
            logger.debug(
                "cti.event_ignored",
                call_id=body.call_id,
                extension=body.extension,
                call_event=body.event,
            )
            return CallEventResult(recipients=[])
        call = _StoredCall(
            call_id=body.call_id,
            state="ringing",
            number=(body.caller_number or "").strip(),
            extension=body.extension,
            direction=body.direction,
            user_ids=users,
            ringing_at=None,
            answered_at=None,
            ended_at=None,
        )
    elif body.caller_number and not call.number:
        call.number = body.caller_number.strip()

    if body.event == "ringing":
        if call.state == "ringing":
            call.user_ids = _union(call.user_ids, users)
            call.ringing_at = call.ringing_at or at
    elif body.event == "answered":
        if call.state != "ended":
            call.state = "answered"
            call.answered_at = call.answered_at or at
            if users:
                call.user_ids = users
                call.extension = body.extension
                call.answered_by_user_id = users[0] if len(users) == 1 else None
    elif body.event == "handled":
        if call.state != "ended":
            call.state = "ended"
            call.ended_at = at
        everybody = _union(call.notified_user_ids, call.user_ids)
        pending = [u for u in everybody if u not in call.dismissed_user_ids]
        call.dismissed_user_ids = everybody
        await _store(redis_client, call)
        await _publish(redis_client, pending, "dismissed", call)
        return CallEventResult(recipients=pending)
    else:  # hangup
        if call.state != "ended":
            call.state = "ended"
            call.ended_at = at

    call.notified_user_ids = _union(call.notified_user_ids, call.user_ids)
    # An agent who dismissed the card (or already acted on it) is not
    # re-notified by later events of the same call.
    recipients = [u for u in call.notified_user_ids if u not in call.dismissed_user_ids]
    await _store(redis_client, call)
    await _publish(redis_client, recipients, body.event, call)
    return CallEventResult(recipients=recipients)


def _is_current(call: _StoredCall, now: datetime) -> bool:
    if call.state != "ended":
        return True
    ended = call.ended_at
    if ended is None:
        return False
    return _utc(ended) >= now - timedelta(seconds=RECENT_AFTER_HANGUP_SECONDS)


def _sort_key(call: ActiveCall) -> datetime:
    stamp = call.ringing_at or call.answered_at or call.ended_at
    return _utc(stamp) if stamp else datetime.min.replace(tzinfo=UTC)


async def list_active_calls(
    redis_client: redis.Redis, user_id: int, *, now: datetime | None = None
) -> list[ActiveCall]:
    """Running calls of *user_id* plus those that ended in the last 15 minutes
    and were not dismissed — newest first. Used to restore popups on reload."""
    moment = _utc(now)
    out: list[ActiveCall] = []
    async for key in redis_client.scan_iter(match=f"{CALL_KEY_PREFIX}*"):
        raw_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        call = await _load(redis_client, raw_key.removeprefix(CALL_KEY_PREFIX))
        if call is None or user_id not in call.user_ids or user_id in call.dismissed_user_ids:
            continue
        if _is_current(call, moment):
            out.append(call.public())
    out.sort(key=_sort_key, reverse=True)
    return out


async def dismiss_call(redis_client: redis.Redis, call_id: str, user_id: int) -> bool:
    """Hide a call's card for *user_id* (all their tabs). False if unknown."""
    call = await _load(redis_client, call_id)
    if call is None or user_id not in _union(call.notified_user_ids, call.user_ids):
        return False
    if user_id not in call.dismissed_user_ids:
        call.dismissed_user_ids = _union(call.dismissed_user_ids, [user_id])
        await _store(redis_client, call)
    await _publish(redis_client, [user_id], "dismissed", call)
    return True


__all__ = [
    "CALL_TTL_SECONDS",
    "PHONE_EXTENSION_PREF",
    "RECENT_AFTER_HANGUP_SECONDS",
    "ActiveCall",
    "CallEventIn",
    "CallEventResult",
    "apply_call_event",
    "dismiss_call",
    "extension_matches",
    "list_active_calls",
    "normalize_extensions",
    "parse_extensions",
    "users_for_extension",
]
