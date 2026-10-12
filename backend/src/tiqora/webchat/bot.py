"""The AI first line of a chat topic: is the bot able to answer right now?

``bot_availability`` answers that for one topic on one queue. It decides whether a new chat
starts with the bot, whether a topic counts as online without a human, and (from the bot loop,
a later task) whether a running bot conversation may go on. It never raises: any failure is an
``ok=False`` result carrying the exception class name as the reason.

Checks in order, each with its reason when it fails:

1. ``ai_mode_off`` — the topic is not ``first_line``.
2. ``no_policy`` / ``auto_reply_off`` / ``no_service_user`` — the queue's AI policy must answer
   on its own (open question 2: no separate chat switch) and have a service user.
3. ``autonomy_off`` — autonomy ``off`` only drafts, so the bot would hand every chat off.
4. ``paused`` / ``gate`` — ``require_feature_allowed`` (``webchat`` is a Tiqora-only channel,
   so ``parallel`` operation passes; the kill switch never does).
5. ``worker_down`` — the AI worker's bot loop refreshes ``BOT_ALIVE_KEY`` every 10 s (TTL 30 s);
   no key or any Redis error means nobody would answer. The admin status checks it last
   (``worker_last``), so it does not hide steps 6 and 7.
6. ``queue_rate_limit`` / ``global_rate_limit`` / ``budget_tokens_day`` — the queue-level caps
   (``ai.auto_run.auto_reply_queue_cap_reason``); per-ticket caps need a ticket and are
   checked by the turn.
7. ``llm_unavailable`` — no agent profile, or every model of it is skipped.

The result is cached in Redis for ``BOT_AVAILABILITY_CACHE_SECONDS`` per config, topic and
queue. Callers that spend money on the answer (a bot turn, a handoff decision) pass
``use_cache=False``.

The second half of the module is the bot turn (C6): ``claim_bot_turn`` (an exclusive lease on
one session), ``run_chat_bot_turn`` (one AI answer, or a handoff), ``hand_off_chat`` (the bot
gives the chat to the team, or ends it when nobody is there) and ``close_bot_resolved_ticket``
(parks the ticket of a chat the bot solved). ``sessions`` imports this module at the top, and
``availability`` does too, so the helpers of those modules are imported inside the functions
that need them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai.auto_run import (
    QUEUE_CAP_REASONS,
    TICKET_CAP_REASONS,
    NoAgentProfile,
    auto_reply_cap_reason,
    auto_reply_queue_cap_reason,
    prepare_auto_run,
)
from tiqora.ai.context import get_or_create_state
from tiqora.ai.gate import AiAutoReplyPausedError, AiGateError, require_feature_allowed
from tiqora.ai.handoff import consume_ai_grant, mark_ai_escalated
from tiqora.ai.llm_routing import TASK_AGENT, NoUsableModel, build_task_llm
from tiqora.ai.models import (
    AUTONOMY_OFF,
    DRAFT_KIND_REPLY,
    FEATURE_AUTO_REPLY,
    TiqoraAiTicketState,
)
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.ai.runtime import (
    STATUS_DRAFTED,
    STATUS_ESCALATED,
    STATUS_SENT,
    STATUS_SUPERSEDED,
    TRIGGER_AUTO,
    LockHeldError,
    PolicyDisabledError,
    resolve_auto_state_id,
    run_ticket_agent,
)
from tiqora.ai.tools import resolve_allowed_state_types
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.domain.ticket_write_service import change_state as domain_change_state
from tiqora.jobs.service import utc_now
from tiqora.webchat.hub import publish_chat_typing
from tiqora.webchat.models import ChatTopic
from tiqora.webchat.notify import (
    BOT_QUEUED_PREFIX,
    flush_chat_notifications,
    queue_agent_event,
    queue_bot_turn,
    queue_wakeup,
)
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

BOT_ALIVE_KEY: Final = "tiqora:chat:bot:alive"  # SET EX 30 by the ai-worker bot loop every 10 s
BOT_AVAILABILITY_CACHE_PREFIX: Final = "tiqora:chat:botavail:"
BOT_AVAILABILITY_CACHE_SECONDS: Final = 10
WEBCHAT_SOURCE_CHANNEL: Final = "webchat"

#: A claim older than this belongs to a dead worker; the next turn may take it over.
BOT_CLAIM_STALE_SECONDS: Final = 180
#: Failed turns in a row before the chat goes to a human (``reason="error"``).
BOT_MAX_ATTEMPTS: Final = 3
BOT_TYPING_INTERVAL_SECONDS: Final = 3.0
#: ``bot_claim_owner`` is VARCHAR(64): host (cut to this) + ":" + pid + ":" + 8 hex chars.
BOT_OWNER_HOST_CHARS: Final = 40
#: Prefix of the AI run lock the bot takes (``worker_instance="chat-bot"``).
BOT_RUN_LOCK_PREFIX: Final = "chat-bot:"
BOT_WORKER_INSTANCE: Final = "chat-bot"

HandoffReason = Literal["visitor", "ai", "limit", "error", "unavailable"]
HANDOFF_NOTE_SUBJECT: Final = "KI-Übergabe im Chat"
NOTE_DRAFT_PREPARED: Final = (
    "Die KI hat einen Entwurf vorbereitet, aber nichts an den Besucher gesendet."
)
NOTE_NO_REPLY: Final = "Die KI hatte keine Antwort für den Besucher."


@dataclass(frozen=True, slots=True)
class BotAvailability:
    ok: bool
    # "ai_mode_off" | "no_policy" | "auto_reply_off" | "no_service_user" | "autonomy_off" |
    # "gate" | "paused" | "worker_down" | "queue_rate_limit" | "global_rate_limit" |
    # "budget_tokens_day" | "llm_unavailable" | an exception class name (unexpected failure)
    reason: str | None


def _cache_key(config_id: int, topic_key: str, queue_id: int) -> str:
    return f"{BOT_AVAILABILITY_CACHE_PREFIX}{config_id}:{topic_key}:{queue_id}"


async def _compute(
    db: AsyncSession,
    redis_client: Any,
    settings: Settings,
    *,
    topic: ChatTopic,
    queue_id: int,
    worker_last: bool = False,
) -> BotAvailability:
    if topic.ai_mode != "first_line":
        return BotAvailability(False, "ai_mode_off")
    policy = await get_queue_policy_by_queue(db, queue_id)
    if policy is None:
        return BotAvailability(False, "no_policy")
    if not policy.enabled_auto_reply:
        return BotAvailability(False, "auto_reply_off")
    if policy.service_user_id is None:
        return BotAvailability(False, "no_service_user")
    if policy.autonomy == AUTONOMY_OFF:
        return BotAvailability(False, "autonomy_off")
    try:
        await require_feature_allowed(db, FEATURE_AUTO_REPLY, source_channel=WEBCHAT_SOURCE_CHANNEL)
    except AiAutoReplyPausedError:
        return BotAvailability(False, "paused")
    except AiGateError:
        return BotAvailability(False, "gate")
    if not worker_last and not await _worker_alive(redis_client):
        return BotAvailability(False, "worker_down")
    cap = await auto_reply_queue_cap_reason(db, queue_id=queue_id, policy=policy)
    if cap is not None:
        return BotAvailability(False, cap)
    try:
        task_llm = await build_task_llm(db, settings, policy, TASK_AGENT)
    except NoUsableModel:
        return BotAvailability(False, "llm_unavailable")
    if task_llm is None:
        return BotAvailability(False, "llm_unavailable")
    if worker_last and not await _worker_alive(redis_client):
        return BotAvailability(False, "worker_down")
    return BotAvailability(True, None)


async def _worker_alive(redis_client: Any) -> bool:
    try:
        return bool(await redis_client.exists(BOT_ALIVE_KEY))
    except Exception:  # noqa: BLE001 - unknown worker state counts as down (fail closed)
        logger.warning("chat_bot_alive_check_failed")
        return False


async def bot_availability(
    db: AsyncSession,
    redis_client: Any,
    settings: Settings,
    *,
    config_id: int,
    topic: ChatTopic,
    queue_id: int,
    use_cache: bool = True,
    worker_last: bool = False,
) -> BotAvailability:
    """Whether the bot can answer on *topic* of *config_id* with the policy of *queue_id*.

    *queue_id* is the queue the chat is on NOW (R-QUEUE-SOT: at creation the topic's queue, in a
    running chat the ticket's current queue). Never raises.

    ``worker_last`` (the admin status, uncached) checks the AI worker after the caps and the
    model, so a stopped worker does not hide a missing model or a reached limit; the verdict
    (``ok``) is the same either way, only the reported reason differs.

    A failing check runs in a savepoint: on PostgreSQL a failed statement would otherwise
    abort the caller's transaction (``create_session``, ``GET /config``).
    """
    key = _cache_key(config_id, topic.key, queue_id)
    if use_cache:
        try:
            cached = await redis_client.get(key)
            if cached is not None:
                data = json.loads(cached)
                if isinstance(data, dict) and isinstance(data.get("ok"), bool):
                    reason = data.get("reason")
                    return BotAvailability(data["ok"], str(reason) if reason else None)
        except Exception:  # noqa: BLE001 - cache trouble must not hide the bot
            logger.warning("chat_bot_availability_cache_read_failed", config_id=config_id)
    if worker_last:
        use_cache = False  # a reason in a different order must not reach the public cache
    try:
        async with db.begin_nested():
            result = await _compute(
                db,
                redis_client,
                settings,
                topic=topic,
                queue_id=queue_id,
                worker_last=worker_last,
            )
    except Exception as exc:  # noqa: BLE001 - availability is advisory, never a 500
        logger.warning(
            "chat_bot_availability_failed", config_id=config_id, error=exc.__class__.__name__
        )
        return BotAvailability(False, exc.__class__.__name__)
    if use_cache:
        try:
            await redis_client.set(
                key,
                json.dumps({"ok": result.ok, "reason": result.reason}),
                ex=BOT_AVAILABILITY_CACHE_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.warning("chat_bot_availability_cache_write_failed", config_id=config_id)
    return result


# -- the bot turn (C6) ------------------------------------------------------------------------


def make_claim_owner() -> str:
    """``<host>:<pid>:<uuid8>``; the host is cut so the value fits ``bot_claim_owner`` (64)."""
    import os

    host = socket.gethostname()[:BOT_OWNER_HOST_CHARS]
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def claim_bot_turn(db: AsyncSession, *, session_id: str, owner: str, now: datetime) -> bool:
    """Take the exclusive lease on one bot session; commits. ``True`` when this caller owns it.

    One conditional ``UPDATE``: the session is still ``bot``, a visitor line is pending
    (``last_visitor_seq > bot_answered_seq``) and nobody holds a fresh claim. A claim older
    than ``BOT_CLAIM_STALE_SECONDS`` is taken over; the AI run lock that dead turn left
    behind (``chat-bot:`` owner) is released in the same transaction, so the new turn does
    not trip over the old turn's lock.

    Exactly one ai-worker runs the turns (docs/deployment.md): a turn that is still alive
    after ``BOT_CLAIM_STALE_SECONDS`` is indistinguishable from a dead one here, so a second
    replica would retake it and both would answer.
    """
    stale = now - timedelta(seconds=BOT_CLAIM_STALE_SECONDS)
    result = await db.execute(
        update(TiqoraChatSession)
        .where(
            TiqoraChatSession.id == session_id,
            TiqoraChatSession.status == "bot",
            TiqoraChatSession.last_visitor_seq > TiqoraChatSession.bot_answered_seq,
            (TiqoraChatSession.bot_claimed_at.is_(None))
            | (TiqoraChatSession.bot_claimed_at < stale),
        )
        .values(bot_claimed_at=now, bot_claim_owner=owner[:64])
        .execution_options(synchronize_session=False)
    )
    claimed = getattr(result, "rowcount", 0) == 1
    if claimed:
        ticket_id = (
            await db.execute(
                select(TiqoraChatSession.ticket_id).where(TiqoraChatSession.id == session_id)
            )
        ).scalar_one()
        await db.execute(
            update(TiqoraAiTicketState)
            .where(
                TiqoraAiTicketState.ticket_id == int(ticket_id),
                TiqoraAiTicketState.run_lock_owner.like(f"{BOT_RUN_LOCK_PREFIX}%"),
            )
            .values(run_lock_owner=None, run_lock_at=None)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    return claimed


async def _typing_pulse(redis_client: Any, session_id: str) -> None:
    """Show "the assistant is typing" to the visitor until cancelled."""
    while True:
        try:
            await publish_chat_typing(redis_client, session_id, who="bot")
        except Exception:  # noqa: BLE001 - a missed typing frame is cosmetic
            logger.warning("webchat_bot_typing_failed")
        await asyncio.sleep(BOT_TYPING_INTERVAL_SECONDS)


def _handoff_reason_for(cap: str) -> HandoffReason:
    """Why a block ends the bot's part: caps are ``limit``, the rest ``unavailable``."""
    if cap in TICKET_CAP_REASONS or cap in QUEUE_CAP_REASONS:
        return "limit"
    if cap == "escalated_to_human":
        return "ai"
    return "unavailable"


async def _hand_off_in_new_transaction(
    factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    *,
    session_id: str,
    reason: HandoffReason,
    note: str | None,
    now: datetime,
) -> str:
    async with factory() as db:
        result = await hand_off_chat(
            db,
            SysConfig(db),
            redis_client,
            session_id=session_id,
            reason=reason,
            note=note,
            now=now,
        )
        await db.commit()
        await flush_chat_notifications(db, redis_client)
    return result


async def _count_failed_turn(factory: async_sessionmaker[AsyncSession], session_id: str) -> int:
    async with factory() as db:
        await db.execute(
            update(TiqoraChatSession)
            .where(TiqoraChatSession.id == session_id)
            .values(bot_attempts=TiqoraChatSession.bot_attempts + 1)
            .execution_options(synchronize_session=False)
        )
        attempts = (
            await db.execute(
                select(TiqoraChatSession.bot_attempts).where(TiqoraChatSession.id == session_id)
            )
        ).scalar_one()
        await db.commit()
    return int(attempts)


async def _record_answer(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    ticket_id: int,
    article_id: int | None,
    fallback_seq: int,
) -> None:
    """The bot's line is stored: it covers every visitor line before it (``bot_answered_seq``).

    Not the seq read at claim time: a line that arrived while the turn was preparing is in
    the model's context too, and answering it twice would look like a bug to the visitor.
    """
    async with factory() as db:
        answered = fallback_seq
        if article_id is not None:
            bot_seq = (
                await db.execute(
                    select(TiqoraChatMessage.seq).where(
                        TiqoraChatMessage.session_id == session_id,
                        TiqoraChatMessage.article_id == article_id,
                    )
                )
            ).scalar_one_or_none()
            if bot_seq is not None:
                covered = (
                    await db.execute(
                        select(TiqoraChatMessage.seq)
                        .where(
                            TiqoraChatMessage.session_id == session_id,
                            TiqoraChatMessage.sender == "visitor",
                            TiqoraChatMessage.seq < int(bot_seq),
                        )
                        .order_by(TiqoraChatMessage.seq.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if covered is not None:
                    answered = int(covered)
        await db.execute(
            update(TiqoraChatSession)
            .where(TiqoraChatSession.id == session_id)
            .values(bot_answered_seq=answered, bot_attempts=0, bot_outcome="replied")
            .execution_options(synchronize_session=False)
        )
        # A release an agent granted by hand is spent one reply at a time.
        await consume_ai_grant(db, ticket_id)
        await db.commit()


async def _release_claim(
    factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    *,
    session_id: str,
    owner: str,
    requeue: bool,
) -> None:
    """Clear the claim (always) and queue the next turn when a visitor line is still pending.

    The dedupe key is dropped first: it was set by the push that started this turn (or by a
    line that arrived meanwhile and was skipped as claimed), and would swallow the push.
    """
    async with factory() as db:
        await db.execute(
            update(TiqoraChatSession)
            .where(
                TiqoraChatSession.id == session_id, TiqoraChatSession.bot_claim_owner == owner[:64]
            )
            .values(bot_claimed_at=None, bot_claim_owner=None)
            .execution_options(synchronize_session=False)
        )
        if requeue:
            row = (
                await db.execute(
                    select(
                        TiqoraChatSession.status,
                        TiqoraChatSession.last_visitor_seq,
                        TiqoraChatSession.bot_answered_seq,
                    ).where(TiqoraChatSession.id == session_id)
                )
            ).first()
            if row is not None and row[0] == "bot" and int(row[1]) > int(row[2]):
                with contextlib.suppress(Exception):
                    await redis_client.delete(f"{BOT_QUEUED_PREFIX}{session_id}")
                queue_bot_turn(db, session_id)
        await db.commit()
        await flush_chat_notifications(db, redis_client)


async def _bot_turn(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    redis_client: Any,
    *,
    session_id: str,
    owner: str,
    now_fn: Callable[[], datetime],
) -> tuple[str, str | None]:
    """Everything after the claim; returns ``(outcome, reason)``. Never leaves a claim behind
    to its caller: ``run_chat_bot_turn`` clears it."""
    async with factory() as db:
        chat = await get_chat_session(db, session_id)
        if chat is None or chat.purged_at is not None or chat.status != "bot":
            return "skipped", "not_bot"
        from tiqora.webchat.sessions import current_queue_id

        ticket_id = int(chat.ticket_id)
        target_seq = int(chat.last_visitor_seq)
        config_id = int(chat.config_id)
        queue_id = await current_queue_id(db, chat)
        cfg = await load_config(db, config_id)
        topic = None
        if cfg is not None:
            topic = next((t for t in config_values(cfg).topics if t.key == chat.topic_key), None)
        await db.commit()

        cap: str | None = None
        if topic is None:
            cap = "topic_gone"
        else:
            # R-QUEUE-SOT: the queue the ticket is on now; no cache, this spends money.
            availability = await bot_availability(
                db,
                redis_client,
                settings,
                config_id=config_id,
                topic=topic,
                queue_id=queue_id,
                use_cache=False,
            )
            if not availability.ok:
                cap = availability.reason or "unavailable"
            else:
                policy = await get_queue_policy_by_queue(db, queue_id)
                assert policy is not None  # availability.ok implies a policy
                state = await get_or_create_state(db, ticket_id)
                cap = await auto_reply_cap_reason(
                    db, ticket_id=ticket_id, queue_id=queue_id, policy=policy, state=state
                )
    if cap is not None:
        handoff = await _hand_off_in_new_transaction(
            factory,
            redis_client,
            session_id=session_id,
            reason=_handoff_reason_for(cap),
            note=None,
            now=now_fn(),
        )
        return ("handoff" if handoff != "noop" else "skipped"), cap

    typing_task = asyncio.create_task(_typing_pulse(redis_client, session_id))
    try:
        async with factory() as rdb:
            try:
                run_policy = await get_queue_policy_by_queue(rdb, queue_id)
                assert run_policy is not None
                prepared = await prepare_auto_run(rdb, settings, run_policy)
                result = await run_ticket_agent(
                    rdb,
                    settings=settings,
                    **prepared.as_kwargs(),
                    ticket_id=ticket_id,
                    trigger=TRIGGER_AUTO,
                    acting_user_id=None,
                    run_id=owner,
                    worker_instance=BOT_WORKER_INSTANCE,
                    source_channel=WEBCHAT_SOURCE_CHANNEL,
                )
            finally:
                # The runtime committed its wake / chat_message events before it raised or
                # returned; rolled-back work queued nothing.
                await flush_chat_notifications(rdb, redis_client)
    except LockHeldError:
        # An agent's manual draft holds the ticket: not the AI's failure, try again soon.
        return "retry", "lock_held"
    except (NoAgentProfile, NoUsableModel, PolicyDisabledError) as exc:
        handoff = await _hand_off_in_new_transaction(
            factory,
            redis_client,
            session_id=session_id,
            reason="unavailable",
            note=None,
            now=now_fn(),
        )
        return ("handoff" if handoff != "noop" else "skipped"), exc.__class__.__name__
    except Exception as exc:  # noqa: BLE001 - LlmError, AgentRunError, anything the run raised
        attempts = await _count_failed_turn(factory, session_id)
        logger.warning(
            "webchat_bot_turn_failed",
            session_id=session_id,
            error=exc.__class__.__name__,
            attempts=attempts,
        )
        if attempts < BOT_MAX_ATTEMPTS:
            return "retry", exc.__class__.__name__
        handoff = await _hand_off_in_new_transaction(
            factory,
            redis_client,
            session_id=session_id,
            reason="error",
            note=f"KI nicht erreichbar: {exc.__class__.__name__}",
            now=now_fn(),
        )
        return ("handoff" if handoff != "noop" else "skipped"), exc.__class__.__name__
    finally:
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    status = result.status
    if status == STATUS_SENT:
        await _record_answer(
            factory,
            session_id=session_id,
            ticket_id=ticket_id,
            article_id=result.article_id,
            fallback_seq=target_seq,
        )
        return "replied", None
    if status == STATUS_SUPERSEDED:
        return "superseded", None
    if status == STATUS_ESCALATED:
        # The escalate tool already wrote the AI's internal note: that is the summary.
        reason: HandoffReason = "ai"
        note = None
    elif status == STATUS_DRAFTED:
        reason, note = "ai", NOTE_DRAFT_PREPARED
    else:
        reason, note = "ai", NOTE_NO_REPLY
    handoff = await _hand_off_in_new_transaction(
        factory, redis_client, session_id=session_id, reason=reason, note=note, now=now_fn()
    )
    return ("handoff" if handoff != "noop" else "skipped"), status


async def run_chat_bot_turn(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    redis_client: Any,
    *,
    session_id: str,
    owner: str,
    now_fn: Callable[[], datetime] = utc_now,
) -> str:
    """One bot turn for *session_id*: ``replied`` | ``handoff`` | ``superseded`` | ``skipped`` |
    ``retry``.

    Claims the session (``skipped`` when it is not ``bot``, nothing is pending or somebody else
    holds the claim), checks availability and the per-ticket caps, runs the AI agent with the
    chat tone, and maps the result: an answer advances ``bot_answered_seq``; a limit, an
    escalation, a draft, no reply or three failures in a row hand the chat to the team. Each
    step is its own short transaction; none is open during the LLM call except the
    runtime's. The claim is cleared on every exit, and a visitor line that is still pending
    queues the next turn.
    """
    async with factory() as db:
        if not await claim_bot_turn(db, session_id=session_id, owner=owner, now=now_fn()):
            logger.info(
                "webchat_bot_turn", session_id=session_id, outcome="skipped", reason="claim"
            )
            return "skipped"
    # Lines that arrive from now on push again; ones that slip in before this delete are
    # found by the re-check in ``_release_claim``.
    with contextlib.suppress(Exception):
        await redis_client.delete(f"{BOT_QUEUED_PREFIX}{session_id}")
    outcome, reason = "retry", "crashed"
    try:
        outcome, reason_or_none = await _bot_turn(
            factory, settings, redis_client, session_id=session_id, owner=owner, now_fn=now_fn
        )
        reason = reason_or_none or ""
    finally:
        # ``retry`` waits for the loop's delay; every other exit may have a line pending.
        await _release_claim(
            factory,
            redis_client,
            session_id=session_id,
            owner=owner,
            requeue=outcome != "retry",
        )
    logger.info("webchat_bot_turn", session_id=session_id, outcome=outcome, reason=reason)
    return outcome


async def hand_off_chat(
    db: AsyncSession,
    sysconfig: SysConfig,
    redis_client: Any,
    *,
    session_id: str,
    reason: HandoffReason,
    note: str | None,
    now: datetime,
) -> Literal["waiting", "no_agent", "noop"]:
    """The bot gives the chat to the team (``waiting``, rings the agents) or, when nobody can
    take it now, ends it as ``no_agent`` (the visitor may then leave an address).

    Runs in the caller's transaction (R-LOCK-ORDER: session row first, then the ticket
    writes); the caller commits and calls ``flush_chat_notifications``. ``noop`` when the
    session is not ``bot`` any more (an agent was faster).
    """
    from tiqora.webchat.availability import eligible_agent_ids, topic_ref, within_business_hours
    from tiqora.webchat.sessions import add_system_message, current_queue_id

    locked = await get_chat_session(db, session_id, for_update=True)
    if locked is None or locked.purged_at is not None or locked.status != "bot":
        return "noop"
    queue_id = await current_queue_id(db, locked)
    cfg = await load_config(db, int(locked.config_id))
    values = config_values(cfg) if cfg is not None else None
    topic = None
    if values is not None:
        topic = next((t for t in values.topics if t.key == locked.topic_key), None)
    humans = False
    if values is not None and topic is not None:
        # R-QUEUE-SOT: the agents of the queue the ticket is on now.
        current_topic = topic.model_copy(update={"queue_id": queue_id})
        humans = await within_business_hours(sysconfig, values.business_calendar, now) and bool(
            await eligible_agent_ids(
                db, redis_client, config_id=int(locked.config_id), topic=current_topic
            )
        )

    locked.handoff_at = now
    locked.handoff_reason = reason
    locked.bot_outcome = "handoff"
    if humans:
        locked.status = "waiting"
        locked.waiting_since = now
    else:
        locked.status = "ended"
        locked.end_reason = "no_agent"
        locked.ended_at = now
    await db.flush([locked])

    ticket_id = int(locked.ticket_id)
    if note:
        policy = await get_queue_policy_by_queue(db, queue_id)
        author = policy.service_user_id if policy is not None and policy.service_user_id else 1
        await add_article(
            db,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject=HANDOFF_NOTE_SUBJECT,
                body=note,
                channel="note",
            ),
            user_id=int(author),
            sysconfig=sysconfig,
        )
    await mark_ai_escalated(db, ticket_id, reason=f"webchat_{reason}")
    await add_system_message(db, chat=locked, code="handoff", params={"reason": reason}, now=now)
    event: dict[str, Any] = {
        "session_id": locked.id,
        "ticket_id": ticket_id,
        "queue_id": queue_id,
    }
    if humans:
        event["type"] = "chat_waiting"
        event["topic_ref"] = topic_ref(int(locked.config_id), topic.key) if topic else None
        queue_agent_event(db, event)
        queue_wakeup(db, locked.id)
    else:
        await add_system_message(
            db,
            chat=locked,
            code="no_agent",
            params={"has_email": bool(locked.visitor_email)},
            now=now,
        )
        event["type"] = "chat_ended"
        queue_agent_event(db, event)
    logger.info(
        "webchat_handoff",
        session_id=locked.id,
        reason=reason,
        result="waiting" if humans else "no_agent",
    )
    return "waiting" if humans else "no_agent"


async def close_bot_resolved_ticket(
    db: AsyncSession, sysconfig: SysConfig, *, chat: TiqoraChatSession
) -> None:
    """The visitor (or an agent, or the idle sweep) ends a chat the bot answered alone: park
    the ticket in the state the queue policy allows after an automatic reply.

    Only for ``bot_outcome == "replied"`` without a handoff, and only when the policy has a
    service user and allows such a state (the gate the runtime applies after an e-mail
    auto-reply). Runs in the caller's transaction, after the session row lock.
    """
    if chat.bot_outcome != "replied" or chat.handoff_at is not None:
        return
    try:
        # A savepoint: a failing ticket write must never fail (or half-commit) the chat end.
        async with db.begin_nested():
            from tiqora.webchat.sessions import current_queue_id

            # R-QUEUE-SOT: the policy of the queue the ticket is on now, not the topic's.
            queue_id = await current_queue_id(db, chat)
            state_id = await _bot_park_state_id(db, chat, queue_id)
            if state_id is None:
                return
            policy = await get_queue_policy_by_queue(db, queue_id)
            if policy is None or policy.service_user_id is None:
                return
            await domain_change_state(
                db,
                ticket_id=int(chat.ticket_id),
                new_state_id=state_id,
                user_id=int(policy.service_user_id),
                sysconfig=sysconfig,
            )
    except Exception as exc:
        logger.warning(
            "webchat_bot_ticket_close_failed", session_id=chat.id, error=type(exc).__name__
        )
        return
    logger.info("webchat_bot_ticket_closed", session_id=chat.id, state_id=state_id)


async def _bot_park_state_id(
    db: AsyncSession, chat: TiqoraChatSession, queue_id: int
) -> int | None:
    """The state to park in, or ``None`` when the ticket is not in ``new``/``open`` any more
    (an agent or an AI tool moved it to pending/closed/merged after the last bot answer) or
    the queue policy allows no suitable state."""
    current = (
        await db.execute(
            text(
                "SELECT LOWER(tst.name) FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :t"
            ),
            {"t": int(chat.ticket_id)},
        )
    ).first()
    if current is None or str(current[0]) not in ("new", "open"):
        return None
    policy = await get_queue_policy_by_queue(db, queue_id)
    if policy is None or policy.service_user_id is None:
        return None
    return await resolve_auto_state_id(
        db,
        kind=DRAFT_KIND_REPLY,
        allowed_state_types=resolve_allowed_state_types(policy.allowed_state_types),
    )
