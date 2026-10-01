"""Test helpers for the LLM routing tables (see ``tiqora.ai.llm_routing``).

Tests that used to set ``llm_provider_id``/``model_override`` on a queue
policy now build a model + profile and assign it to a task:

    provider = await make_provider(session, name="fake-provider-42")
    profile = await make_profile(session, provider, ["fake-model"])
    await assign_task(session, policy, "agent", profile)

``routing_cleanup_statements(provider_name)`` returns the DELETEs that remove
every profile using that provider's models (and the task assignments that
point at them) — needed before the provider row itself can be deleted,
because profile entries RESTRICT the deletion of their models.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import providers as ai_providers
from tiqora.ai.models import (
    TiqoraAiQueuePolicy,
    TiqoraAiQueueTaskProfile,
    TiqoraAiTaskDefault,
    TiqoraLlmModel,
    TiqoraLlmProfile,
    TiqoraLlmProfileEntry,
    TiqoraLlmProvider,
)
from tiqora.config import get_settings

ModelSpec = str | tuple[TiqoraLlmProvider, str]


async def make_provider(session: AsyncSession, *, name: str, **kwargs: Any) -> TiqoraLlmProvider:
    params: dict[str, Any] = {
        "kind": "openai_compat",
        "base_url": "https://llm.example/v1",
        "api_key": None,
        "eu_hosted": True,
    }
    params.update(kwargs)
    return await ai_providers.create_provider(
        session, settings=get_settings(), change_by=1, name=name, **params
    )


async def make_model(
    session: AsyncSession,
    provider: TiqoraLlmProvider,
    model_id: str,
    *,
    supports_tools: bool = True,
    supports_vision: bool = False,
    max_tool_rounds: int | None = None,
    price_input_per_1m: float | None = None,
    price_output_per_1m: float | None = None,
    valid_id: int = 1,
) -> TiqoraLlmModel:
    """Get-or-create the model row ``model_id @ provider`` (updates flags)."""
    row = (
        await session.execute(
            select(TiqoraLlmModel).where(
                TiqoraLlmModel.provider_id == provider.id, TiqoraLlmModel.model_id == model_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TiqoraLlmModel(provider_id=provider.id, model_id=model_id, create_by=1, change_by=1)
        session.add(row)
    row.supports_tools = supports_tools
    row.supports_vision = supports_vision
    row.max_tool_rounds = max_tool_rounds
    row.price_input_per_1m = price_input_per_1m
    row.price_output_per_1m = price_output_per_1m
    row.valid_id = valid_id
    await session.commit()
    await session.refresh(row)
    return row


async def make_profile(
    session: AsyncSession,
    provider: TiqoraLlmProvider,
    models: list[ModelSpec],
    *,
    name: str | None = None,
    timeout_seconds: int | None = None,
    supports_vision: bool = False,
) -> TiqoraLlmProfile:
    """A profile whose chain is *models* in order. A plain string is a model
    at *provider*; ``(other_provider, "model")`` places one elsewhere.
    Missing model rows are created (tools on, vision per *supports_vision*)."""
    model_rows: list[TiqoraLlmModel] = []
    for spec in models:
        target, model_id = (provider, spec) if isinstance(spec, str) else spec
        existing = (
            await session.execute(
                select(TiqoraLlmModel).where(
                    TiqoraLlmModel.provider_id == target.id, TiqoraLlmModel.model_id == model_id
                )
            )
        ).scalar_one_or_none()
        model_rows.append(
            existing
            if existing is not None
            else await make_model(session, target, model_id, supports_vision=supports_vision)
        )
    profile = TiqoraLlmProfile(
        name=name or f"{provider.name}: {' > '.join(m.model_id for m in model_rows)}",
        timeout_seconds=timeout_seconds,
        create_by=1,
        change_by=1,
    )
    session.add(profile)
    await session.flush()
    for position, model in enumerate(model_rows):
        session.add(
            TiqoraLlmProfileEntry(profile_id=profile.id, position=position, llm_model_id=model.id)
        )
    await session.commit()
    await session.refresh(profile)
    return profile


async def assign_task(
    session: AsyncSession,
    policy: TiqoraAiQueuePolicy | None,
    task: str,
    profile: TiqoraLlmProfile | None,
) -> None:
    """Set *task*'s profile for *policy* (a queue override; ``profile=None``
    = explicit "no own profile"), or the global default when *policy* is
    ``None``."""
    profile_id = profile.id if profile is not None else None
    if policy is None:
        row = await session.get(TiqoraAiTaskDefault, task)
        if row is None:
            session.add(TiqoraAiTaskDefault(task=task, profile_id=profile_id))
        else:
            row.profile_id = profile_id
    else:
        existing = (
            await session.execute(
                select(TiqoraAiQueueTaskProfile).where(
                    TiqoraAiQueueTaskProfile.queue_policy_id == policy.id,
                    TiqoraAiQueueTaskProfile.task == task,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                TiqoraAiQueueTaskProfile(
                    queue_policy_id=policy.id, task=task, profile_id=profile_id
                )
            )
        else:
            existing.profile_id = profile_id
    await session.commit()


async def setup_agent_llm(
    session: AsyncSession,
    policy: TiqoraAiQueuePolicy,
    provider: TiqoraLlmProvider,
    model_id: str = "fake-model",
    **model_kwargs: Any,
) -> TiqoraLlmProfile:
    """The common case: *policy*'s agent runs on ``model_id @ provider``.
    An existing model row is reused as is; *model_kwargs* only apply when
    the row has to be created."""
    exists = (
        await session.execute(
            select(TiqoraLlmModel.id).where(
                TiqoraLlmModel.provider_id == provider.id, TiqoraLlmModel.model_id == model_id
            )
        )
    ).first()
    if exists is None:
        await make_model(session, provider, model_id, **model_kwargs)
    profile = await make_profile(session, provider, [model_id])
    await assign_task(session, policy, "agent", profile)
    return profile


_PROFILES_OF_PROVIDER = (
    "SELECT e.profile_id FROM tiqora_llm_profile_entry e"
    " JOIN tiqora_llm_model m ON m.id = e.llm_model_id"
    " JOIN tiqora_llm_provider p ON p.id = m.provider_id"
    " WHERE p.name = :n"
)


def routing_cleanup_statements(provider_name: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    """DELETEs for every profile (and its task assignments) that uses a model
    of *provider_name*. Run them before deleting the provider row."""
    params = {"n": provider_name}
    return (
        (
            f"DELETE FROM tiqora_ai_task_default WHERE profile_id IN ({_PROFILES_OF_PROVIDER})",
            params,
        ),
        (
            "DELETE FROM tiqora_ai_queue_task_profile"
            f" WHERE profile_id IN ({_PROFILES_OF_PROVIDER})",
            params,
        ),
        # Materialised first: MySQL rejects a DELETE whose subquery reads a
        # table the DELETE cascades into.
        (
            "DELETE FROM tiqora_llm_profile WHERE id IN"
            f" (SELECT profile_id FROM ({_PROFILES_OF_PROVIDER}) AS x)",
            params,
        ),
    )


def delete_limit_alerts(conn: Any) -> None:
    """Remove what :func:`tiqora.ai.limits.announce_exhausted_limits` wrote
    when a test pushed a queue/provider over its budget: the webhook outbox
    rows and the "announced in this window" markers."""
    from sqlalchemy import delete

    from tiqora.ai.limits import EVENT_AI_LIMIT_REACHED
    from tiqora.db.tiqora.models import TiqoraSettings

    conn.execute(
        text("DELETE FROM tiqora_event_outbox WHERE event_type = :e"),
        {"e": EVENT_AI_LIMIT_REACHED},
    )
    # Via the model: ``key`` is reserved in MySQL and needs dialect quoting.
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.like("ai.limit_alert.%")))
