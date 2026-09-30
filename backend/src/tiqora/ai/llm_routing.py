"""Which LLM models a queue uses for which task.

Four levels (migration ``20260930_0053``):

* provider (``tiqora_llm_provider``) — access: base URL, key, currency, budgets;
* model (``tiqora_llm_model``) — one model at one provider, with the
  provider's exact API model id, its capabilities, tool rounds and price;
* profile (``tiqora_llm_profile`` + entries) — an ordered fallback chain;
* task assignment — a global default per task (``tiqora_ai_task_default``)
  and per-queue overrides (``tiqora_ai_queue_task_profile``).

Resolution for (queue policy, task): the queue row if one exists (its
``profile_id`` NULL = "no own profile"), else the global default row (same
NULL meaning), else nothing. "Nothing" then means the task's fallback: triage,
summary and refine use the agent's profile; agent, final answer and vision
have none (AI unavailable / the agent answers itself / images are ignored).

At run time, entries whose model or provider is disabled, whose provider is
over its cost budget, or (vision) whose model cannot read images are skipped
and logged; the rest become one :class:`~tiqora.ai.llm_fallback.FallbackLlmClient`
(or a plain client for a single entry). If a profile resolves but every entry
is skipped, :class:`NoUsableModel` is raised — callers decide what that means
for their task.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.llm import LlmClient, OpenAiCompatLlmClient
from tiqora.ai.llm_fallback import FallbackEntry, FallbackLlmClient
from tiqora.ai.models import (
    TiqoraAiQueuePolicy,
    TiqoraAiQueueTaskProfile,
    TiqoraAiTaskDefault,
    TiqoraLlmModel,
    TiqoraLlmProfile,
    TiqoraLlmProfileEntry,
    TiqoraLlmProvider,
)
from tiqora.ai.usage import provider_budget_exceeded
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret

logger = structlog.get_logger(__name__)

TASK_AGENT = "agent"
TASK_FINAL_ANSWER = "final_answer"
TASK_TRIAGE = "triage"
TASK_SUMMARY = "summary"
TASK_REFINE = "refine"
TASK_VISION = "vision"
ALL_TASKS: tuple[str, ...] = (
    TASK_AGENT,
    TASK_FINAL_ANSWER,
    TASK_TRIAGE,
    TASK_SUMMARY,
    TASK_REFINE,
    TASK_VISION,
)

NEED_TOOLS = "tools"
NEED_VISION = "vision"
TASK_NEEDS: dict[str, frozenset[str]] = {
    TASK_AGENT: frozenset({NEED_TOOLS}),
    TASK_FINAL_ANSWER: frozenset({NEED_TOOLS}),
    TASK_TRIAGE: frozenset({NEED_TOOLS}),
    TASK_SUMMARY: frozenset(),
    TASK_REFINE: frozenset(),
    TASK_VISION: frozenset({NEED_VISION}),
}

# Tasks that use the agent's profile when they resolve to none of their own.
TASKS_FALLING_BACK_TO_AGENT = frozenset({TASK_TRIAGE, TASK_SUMMARY, TASK_REFINE})


@dataclass(frozen=True, slots=True)
class ChainModel:
    llm_model_id: int
    provider_id: int
    model: str  # the provider's API model id
    max_tool_rounds: int | None


@dataclass(frozen=True, slots=True)
class TaskLlm:
    client: LlmClient  # FallbackLlmClient if >1 usable entry, else plain client
    models: list[ChainModel]  # usable entries in order (after skips)
    profile_id: int
    profile_name: str


class NoUsableModel(Exception):  # noqa: N818 — name fixed by the routing design
    """A profile resolved, but every entry of it was skipped."""

    def __init__(self, profile_name: str, reasons: list[str]) -> None:
        self.profile_name = profile_name
        self.reasons = reasons
        detail = "; ".join(reasons) if reasons else "profile has no models"
        super().__init__(f"No usable model in profile {profile_name!r}: {detail}")


def make_llm_client(
    *, base_url: str, api_key: str | None, model: str, timeout_seconds: float
) -> LlmClient:
    """The one place a real client is constructed (tests replace it)."""
    return OpenAiCompatLlmClient(
        base_url=base_url, api_key=api_key, model=model, timeout_seconds=timeout_seconds
    )


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


async def _own_profile_id(
    session: AsyncSession, policy: TiqoraAiQueuePolicy | None, task: str
) -> int | None:
    """queue row > global default row > None, without the agent fallback.

    A row pointing at a disabled profile (``valid_id != 1``) counts as "no
    own profile" — exactly like a row with ``profile_id`` NULL.
    """

    def _usable(row: Any) -> int | None:
        profile_id, valid_id = row
        return int(profile_id) if profile_id is not None and valid_id == 1 else None

    if policy is not None:
        row = (
            await session.execute(
                select(TiqoraAiQueueTaskProfile.profile_id, TiqoraLlmProfile.valid_id)
                .outerjoin(
                    TiqoraLlmProfile, TiqoraLlmProfile.id == TiqoraAiQueueTaskProfile.profile_id
                )
                .where(
                    TiqoraAiQueueTaskProfile.queue_policy_id == policy.id,
                    TiqoraAiQueueTaskProfile.task == task,
                )
            )
        ).first()
        if row is not None:
            return _usable(row)
    default = (
        await session.execute(
            select(TiqoraAiTaskDefault.profile_id, TiqoraLlmProfile.valid_id)
            .outerjoin(TiqoraLlmProfile, TiqoraLlmProfile.id == TiqoraAiTaskDefault.profile_id)
            .where(TiqoraAiTaskDefault.task == task)
        )
    ).first()
    return _usable(default) if default is not None else None


async def resolve_task_profile_id(
    session: AsyncSession, policy: TiqoraAiQueuePolicy | None, task: str
) -> int | None:
    """The profile *task* runs on for *policy* (``None`` = no queue, global
    defaults only), including the fallback of triage/summary/refine to the
    agent's profile. ``None`` means the task has no profile at all."""
    if task not in ALL_TASKS:
        raise ValueError(f"Unknown AI task: {task!r}")
    profile_id = await _own_profile_id(session, policy, task)
    if profile_id is None and task in TASKS_FALLING_BACK_TO_AGENT:
        profile_id = await _own_profile_id(session, policy, TASK_AGENT)
    return profile_id


@dataclass(frozen=True, slots=True)
class _UsableEntry:
    chain: ChainModel
    provider: TiqoraLlmProvider


async def _usable_entries(
    session: AsyncSession, profile: TiqoraLlmProfile, task: str
) -> tuple[list[_UsableEntry], list[str]]:
    rows = (
        await session.execute(
            select(TiqoraLlmModel, TiqoraLlmProvider)
            .join(TiqoraLlmProfileEntry, TiqoraLlmProfileEntry.llm_model_id == TiqoraLlmModel.id)
            .join(TiqoraLlmProvider, TiqoraLlmProvider.id == TiqoraLlmModel.provider_id)
            .where(TiqoraLlmProfileEntry.profile_id == profile.id)
            .order_by(TiqoraLlmProfileEntry.position)
        )
    ).all()
    usable: list[_UsableEntry] = []
    reasons: list[str] = []
    budget_cache: dict[int, str | None] = {}
    for model, provider in rows:
        reason: str | None = None
        if model.valid_id != 1:
            reason = "model_disabled"
        elif provider.valid_id != 1:
            reason = "provider_disabled"
        elif task == TASK_VISION and not model.supports_vision:
            reason = "no_vision"
        else:
            if provider.id not in budget_cache:
                budget_cache[provider.id] = await provider_budget_exceeded(session, provider.id)
            window = budget_cache[provider.id]
            if window is not None:
                reason = f"provider_budget_{window}"
        if reason is not None:
            logger.warning(
                "ai_llm_chain_entry_skipped",
                task=task,
                profile_id=profile.id,
                llm_model_id=model.id,
                provider_id=provider.id,
                model=model.model_id,
                reason=reason,
            )
            reasons.append(f"{model.model_id} @ {provider.name}: {reason}")
            continue
        usable.append(
            _UsableEntry(
                chain=ChainModel(
                    llm_model_id=model.id,
                    provider_id=provider.id,
                    model=model.model_id,
                    max_tool_rounds=model.max_tool_rounds,
                ),
                provider=provider,
            )
        )
    return usable, reasons


async def _resolve_usable(
    session: AsyncSession, policy: TiqoraAiQueuePolicy | None, task: str
) -> tuple[TiqoraLlmProfile, list[_UsableEntry]] | None:
    profile_id = await resolve_task_profile_id(session, policy, task)
    if profile_id is None:
        return None
    profile = (
        await session.execute(select(TiqoraLlmProfile).where(TiqoraLlmProfile.id == profile_id))
    ).scalar_one_or_none()
    if profile is None or profile.valid_id != 1:  # changed since the lookup
        return None
    usable, reasons = await _usable_entries(session, profile, task)
    if not usable:
        raise NoUsableModel(profile.name, reasons)
    return profile, usable


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


def _entry_factory(
    entry: _UsableEntry, *, settings: Settings, timeout_seconds: float
) -> Callable[[], LlmClient]:
    provider = entry.provider
    api_key = (
        decrypt_secret(settings.secret_key, provider.api_key_enc) if provider.api_key_enc else None
    )
    base_url = provider.base_url
    model = entry.chain.model

    def _factory() -> LlmClient:
        return make_llm_client(
            base_url=base_url, api_key=api_key, model=model, timeout_seconds=timeout_seconds
        )

    return _factory


def _chain_client(
    usable: list[_UsableEntry], factories: list[Callable[[], LlmClient]]
) -> LlmClient:
    if len(usable) == 1:
        return factories[0]()
    return FallbackLlmClient(
        [
            FallbackEntry(
                llm_model_id=u.chain.llm_model_id,
                provider_id=u.chain.provider_id,
                model=u.chain.model,
                factory=factory,
            )
            for u, factory in zip(usable, factories, strict=True)
        ]
    )


def _timeout(profile: TiqoraLlmProfile, settings: Settings) -> float:
    if profile.timeout_seconds is not None and profile.timeout_seconds > 0:
        return float(profile.timeout_seconds)
    return float(settings.llm_timeout_seconds)


async def build_task_llm(
    session: AsyncSession,
    settings: Settings,
    policy: TiqoraAiQueuePolicy | None,
    task: str,
) -> TaskLlm | None:
    """The client for *task*. ``None`` → the task has no profile after the
    fallback rules (the caller applies its "no profile" behaviour); raises
    :class:`NoUsableModel` when a profile resolves but every entry is
    skipped."""
    resolved = await _resolve_usable(session, policy, task)
    if resolved is None:
        return None
    profile, usable = resolved
    timeout = _timeout(profile, settings)
    factories = [_entry_factory(u, settings=settings, timeout_seconds=timeout) for u in usable]
    return TaskLlm(
        client=_chain_client(usable, factories),
        models=[u.chain for u in usable],
        profile_id=profile.id,
        profile_name=profile.name,
    )


async def require_task_llm(
    session: AsyncSession,
    settings: Settings,
    policy: TiqoraAiQueuePolicy | None,
    task: str,
) -> TaskLlm:
    """:func:`build_task_llm` for tasks that cannot run without a model:
    no profile or no usable model → HTTP 409 (safe to call from a route; the
    workers catch it like any other exception)."""
    try:
        task_llm = await build_task_llm(session, settings, policy, task)
    except NoUsableModel as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task_llm is None:
        raise no_profile_error(task)
    return task_llm


def no_profile_error(task: str) -> HTTPException:
    """The 409 for a task that resolves to no profile at all."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Queue AI policy has no LLM profile for task {task!r}",
    )


async def build_agent_llm(
    session: AsyncSession, settings: Settings, policy: TiqoraAiQueuePolicy | None
) -> TaskLlm:
    """The agent chain; no profile or no usable model → HTTP 409."""
    return await require_task_llm(session, settings, policy, TASK_AGENT)


@dataclass(frozen=True, slots=True)
class VisionSetup:
    enabled: bool  # the queue has a vision profile (images are processed)
    factory: Callable[[], LlmClient] | None  # None: no profile or nothing usable


async def resolve_vision(
    session: AsyncSession,
    settings: Settings,
    policy: TiqoraAiQueuePolicy | None,
    *,
    audit: AuditContext | None = None,
) -> VisionSetup:
    """Vision profile + client factory for the attachment pre-pass
    (:mod:`tiqora.ai.attachment_context`) in one resolution. Never raises.

    ``enabled`` is False when the queue has no vision profile (images are
    ignored). ``factory`` is a sync ``() -> LlmClient`` over the usable
    entries (with fallback), or None when there is no profile or every entry
    is skipped. With ``audit``, every call is written to
    ``tiqora_ai_audit_log`` with ``feature="vision"`` and the provider/model
    of the entry that made it (see :mod:`tiqora.ai.audit`) — image data-URLs
    are never persisted.
    """
    try:
        resolved = await _resolve_usable(session, policy, TASK_VISION)
    except NoUsableModel:
        return VisionSetup(enabled=True, factory=None)
    if resolved is None:
        return VisionSetup(enabled=False, factory=None)
    profile, usable = resolved
    timeout = _timeout(profile, settings)

    def _audited(entry: _UsableEntry) -> Callable[[], LlmClient]:
        inner = _entry_factory(entry, settings=settings, timeout_seconds=timeout)
        if audit is None:
            return inner

        def _factory() -> LlmClient:
            return AuditingLlmClient(
                inner(),
                settings=settings,
                context=replace(
                    audit,
                    feature="vision",
                    provider_id=entry.chain.provider_id,
                    model=entry.chain.model,
                    llm_model_id=entry.chain.llm_model_id,
                ),
                session=session,
            )

        return _factory

    factories = [_audited(u) for u in usable]

    def _vision_factory() -> LlmClient:
        return _chain_client(usable, factories)

    return VisionSetup(enabled=True, factory=_vision_factory)


async def build_vision_llm_factory(
    session: AsyncSession,
    settings: Settings,
    policy: TiqoraAiQueuePolicy | None,
    *,
    audit: AuditContext | None = None,
) -> Callable[[], LlmClient] | None:
    """Only the factory of :func:`resolve_vision` (None = images ignored)."""
    return (await resolve_vision(session, settings, policy, audit=audit)).factory


def primary_of(models: Sequence[ChainModel]) -> ChainModel | None:
    """The chain's first usable model, if any."""
    return models[0] if models else None


def served_identity(
    raw_llm: object, primary: ChainModel | None
) -> tuple[int | None, str | None, int | None]:
    """``(provider_id, configured model, llm_model_id)`` of the entry that
    served a call: the fallback client's active entry, else *primary*."""
    active_model_id = getattr(raw_llm, "active_llm_model_id", None)
    if active_model_id is not None:
        return (
            getattr(raw_llm, "active_provider_id", None),
            getattr(raw_llm, "active_model", None),
            active_model_id,
        )
    if primary is None:
        return None, None, None
    return primary.provider_id, primary.model, primary.llm_model_id


__all__ = [
    "ALL_TASKS",
    "NEED_TOOLS",
    "NEED_VISION",
    "TASK_AGENT",
    "TASK_FINAL_ANSWER",
    "TASK_NEEDS",
    "TASK_REFINE",
    "TASK_SUMMARY",
    "TASK_TRIAGE",
    "TASKS_FALLING_BACK_TO_AGENT",
    "TASK_VISION",
    "ChainModel",
    "NoUsableModel",
    "TaskLlm",
    "VisionSetup",
    "build_agent_llm",
    "build_task_llm",
    "build_vision_llm_factory",
    "make_llm_client",
    "no_profile_error",
    "primary_of",
    "require_task_llm",
    "resolve_task_profile_id",
    "resolve_vision",
    "served_identity",
]
