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
   no key or any Redis error means nobody would answer.
6. ``queue_rate_limit`` / ``global_rate_limit`` / ``budget_tokens_day`` — the queue-level caps
   (``ai.auto_run.auto_reply_queue_cap_reason``); per-ticket caps need a ticket and are
   checked by the turn.
7. ``llm_unavailable`` — no agent profile, or every model of it is skipped.

The result is cached in Redis for ``BOT_AVAILABILITY_CACHE_SECONDS`` per config, topic and
queue. Callers that spend money on the answer (a bot turn, a handoff decision) pass
``use_cache=False``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.auto_run import auto_reply_queue_cap_reason
from tiqora.ai.gate import AiAutoReplyPausedError, AiGateError, require_feature_allowed
from tiqora.ai.llm_routing import TASK_AGENT, NoUsableModel, build_task_llm
from tiqora.ai.models import AUTONOMY_OFF, FEATURE_AUTO_REPLY
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.config import Settings
from tiqora.webchat.models import ChatTopic

logger = structlog.get_logger(__name__)

BOT_ALIVE_KEY: Final = "tiqora:chat:bot:alive"  # SET EX 30 by the ai-worker bot loop every 10 s
BOT_AVAILABILITY_CACHE_PREFIX: Final = "tiqora:chat:botavail:"
BOT_AVAILABILITY_CACHE_SECONDS: Final = 10
WEBCHAT_SOURCE_CHANNEL: Final = "webchat"


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
    try:
        alive = await redis_client.exists(BOT_ALIVE_KEY)
    except Exception:  # noqa: BLE001 - unknown worker state counts as down (fail closed)
        logger.warning("chat_bot_alive_check_failed")
        return BotAvailability(False, "worker_down")
    if not alive:
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
    return BotAvailability(True, None)


async def bot_availability(
    db: AsyncSession,
    redis_client: Any,
    settings: Settings,
    *,
    config_id: int,
    topic: ChatTopic,
    queue_id: int,
    use_cache: bool = True,
) -> BotAvailability:
    """Whether the bot can answer on *topic* of *config_id* with the policy of *queue_id*.

    *queue_id* is the queue the chat is on NOW (R-QUEUE-SOT: at creation the topic's queue, in a
    running chat the ticket's current queue). Never raises.
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
    try:
        result = await _compute(db, redis_client, settings, topic=topic, queue_id=queue_id)
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
