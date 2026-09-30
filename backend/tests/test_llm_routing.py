"""DB tests for ``tiqora.ai.llm_routing`` — which models a queue uses for
which task.

Real MariaDB testcontainer, no network: ``make_llm_client`` is replaced by
a recorder, so building a client never opens a connection. Queue policies
are plain ORM rows (``queue_id`` has no FK), ids in the 98xx range.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._llm_routing_helpers import (
    assign_task,
    make_model,
    make_profile,
    make_provider,
    routing_cleanup_statements,
)
from tiqora.ai import llm_routing
from tiqora.ai import usage as ai_usage
from tiqora.ai.audit import AuditContext
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage
from tiqora.ai.llm_fallback import FallbackLlmClient, reset_cooldowns
from tiqora.ai.llm_routing import (
    TASK_AGENT,
    TASK_FINAL_ANSWER,
    TASK_REFINE,
    TASK_SUMMARY,
    TASK_TRIAGE,
    TASK_VISION,
    NoUsableModel,
    build_agent_llm,
    build_task_llm,
    build_vision_llm_factory,
    resolve_task_profile_id,
)
from tiqora.ai.models import TiqoraAiQueuePolicy, TiqoraLlmProvider
from tiqora.ai.pii import PiiMapper
from tiqora.ai.runtime import _resolve_final_answer_llm
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PROVIDER_PREFIX = "routing-test-"
_QUEUE_BASE = 9800
_URLS: set[str] = set()


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tiqora_ai_task_default"))
        conn.execute(
            text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id BETWEEN :a AND :b"),
            {"a": _QUEUE_BASE, "b": _QUEUE_BASE + 99},
        )
        conn.execute(
            text("DELETE FROM tiqora_ai_usage WHERE queue_id BETWEEN :a AND :b"),
            {"a": _QUEUE_BASE, "b": _QUEUE_BASE + 99},
        )
        names = [
            r[0]
            for r in conn.execute(
                text("SELECT name FROM tiqora_llm_provider WHERE name LIKE :p"),
                {"p": f"{_PROVIDER_PREFIX}%"},
            )
        ]
        for name in names:
            for stmt, params in routing_cleanup_statements(name):
                conn.execute(text(stmt), params)
        conn.execute(
            text("DELETE FROM tiqora_llm_provider WHERE name LIKE :p"),
            {"p": f"{_PROVIDER_PREFIX}%"},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows_after_module() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _cleanup(url)


@pytest.fixture
async def session(mariadb_znuny_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(engine)
    engine.dispose()
    _URLS.add(mariadb_znuny_url)
    _cleanup(mariadb_znuny_url)
    reset_cooldowns()
    async_engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    try:
        async with factory() as s:
            yield s
    finally:
        await async_engine.dispose()
        _cleanup(mariadb_znuny_url)
        reset_cooldowns()


class _RecordedClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def chat(self, **_kwargs: Any) -> LlmResponse:
        return LlmResponse(
            content="ok", usage=LlmUsage(prompt_tokens=1, completion_tokens=1), model="x"
        )


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_RecordedClient]:
    clients: list[_RecordedClient] = []

    def _make(**kwargs: Any) -> _RecordedClient:
        client = _RecordedClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(llm_routing, "make_llm_client", _make)
    return clients


async def _provider(session: AsyncSession, suffix: str, **kwargs: Any) -> TiqoraLlmProvider:
    return await make_provider(session, name=f"{_PROVIDER_PREFIX}{suffix}", **kwargs)


async def _policy(session: AsyncSession, n: int) -> TiqoraAiQueuePolicy:
    row = TiqoraAiQueuePolicy(queue_id=_QUEUE_BASE + n, create_by=1, change_by=1)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------


async def test_queue_row_beats_global_default_beats_nothing(session: AsyncSession) -> None:
    provider = await _provider(session, "a")
    global_profile = await make_profile(session, provider, ["global-model"])
    queue_profile = await make_profile(session, provider, ["queue-model"])
    inheriting = await _policy(session, 1)
    overriding = await _policy(session, 2)

    assert await resolve_task_profile_id(session, inheriting, TASK_AGENT) is None

    await assign_task(session, None, TASK_AGENT, global_profile)
    await assign_task(session, overriding, TASK_AGENT, queue_profile)

    assert await resolve_task_profile_id(session, inheriting, TASK_AGENT) == global_profile.id
    assert await resolve_task_profile_id(session, overriding, TASK_AGENT) == queue_profile.id
    assert await resolve_task_profile_id(session, None, TASK_AGENT) == global_profile.id


async def test_explicit_null_override_beats_global_default(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    """``profile_id: null`` for vision on the queue means "ignore images"
    even though a global vision profile exists."""
    provider = await _provider(session, "v")
    vision = await make_profile(session, provider, ["eye"], supports_vision=True)
    await assign_task(session, None, TASK_VISION, vision)
    policy = await _policy(session, 3)
    await assign_task(session, policy, TASK_VISION, None)

    assert await resolve_task_profile_id(session, policy, TASK_VISION) is None
    assert await build_vision_llm_factory(session, get_settings(), policy) is None
    other = await _policy(session, 4)
    assert await resolve_task_profile_id(session, other, TASK_VISION) == vision.id


@pytest.mark.parametrize("task", [TASK_TRIAGE, TASK_SUMMARY, TASK_REFINE])
async def test_triage_summary_refine_fall_back_to_the_agent_profile(
    session: AsyncSession, built: list[_RecordedClient], task: str
) -> None:
    provider = await _provider(session, f"fb-{task}")
    agent = await make_profile(session, provider, ["agent-model"])
    own = await make_profile(session, provider, ["own-model"])
    policy = await _policy(session, 5)
    await assign_task(session, policy, TASK_AGENT, agent)

    task_llm = await build_task_llm(session, get_settings(), policy, task)
    assert task_llm is not None and task_llm.profile_id == agent.id

    # An explicit "no own profile" also falls back to the agent.
    await assign_task(session, policy, task, None)
    assert await resolve_task_profile_id(session, policy, task) == agent.id

    await assign_task(session, policy, task, own)
    task_llm = await build_task_llm(session, get_settings(), policy, task)
    assert task_llm is not None and task_llm.profile_id == own.id
    assert [m.model for m in task_llm.models] == ["own-model"]


async def test_agent_final_answer_and_vision_do_not_fall_back(session: AsyncSession) -> None:
    provider = await _provider(session, "nofb")
    agent = await make_profile(session, provider, ["agent-model"])
    policy = await _policy(session, 6)
    await assign_task(session, policy, TASK_AGENT, agent)
    for task in (TASK_FINAL_ANSWER, TASK_VISION):
        assert await build_task_llm(session, get_settings(), policy, task) is None


# ---------------------------------------------------------------------------
# Chain building and skips
# ---------------------------------------------------------------------------


async def test_models_order_single_and_fallback_clients(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    a = await _provider(session, "order-a")
    b = await _provider(session, "order-b")
    single = await make_profile(session, a, ["one"])
    chain = await make_profile(session, a, ["first", (b, "second"), "third"])
    policy = await _policy(session, 7)

    await assign_task(session, policy, TASK_AGENT, single)
    task_llm = await build_task_llm(session, get_settings(), policy, TASK_AGENT)
    assert task_llm is not None
    assert isinstance(task_llm.client, _RecordedClient)  # plain client, no wrapper
    assert task_llm.client.kwargs["model"] == "one"

    await assign_task(session, policy, TASK_AGENT, chain)
    task_llm = await build_task_llm(session, get_settings(), policy, TASK_AGENT)
    assert task_llm is not None
    assert [(m.provider_id, m.model) for m in task_llm.models] == [
        (a.id, "first"),
        (b.id, "second"),
        (a.id, "third"),
    ]
    assert isinstance(task_llm.client, FallbackLlmClient)
    assert [e.llm_model_id for e in task_llm.client._entries] == [  # noqa: SLF001
        m.llm_model_id for m in task_llm.models
    ]
    assert task_llm.profile_name == chain.name


async def test_timeout_comes_from_the_profile(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    provider = await _provider(session, "timeout")
    slow = await make_profile(session, provider, ["slow"], timeout_seconds=17)
    default = await make_profile(session, provider, ["normal"])
    policy = await _policy(session, 8)

    await assign_task(session, policy, TASK_AGENT, slow)
    await build_task_llm(session, get_settings(), policy, TASK_AGENT)
    await assign_task(session, policy, TASK_AGENT, default)
    await build_task_llm(session, get_settings(), policy, TASK_AGENT)

    assert built[0].kwargs["timeout_seconds"] == 17.0
    assert built[1].kwargs["timeout_seconds"] == float(get_settings().llm_timeout_seconds)


async def test_disabled_model_disabled_provider_and_budget_are_skipped(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    """Bug fixes: disabled providers were used anyway, and an over-budget
    primary returned 409 instead of falling back."""
    good = await _provider(session, "skip-good")
    off = await _provider(session, "skip-off")
    broke = await _provider(session, "skip-broke", budget_cost_day=1.0)
    await make_model(session, good, "retired", valid_id=2)
    profile = await make_profile(
        session, good, ["retired", (off, "off-model"), (broke, "broke-model"), "working"]
    )
    off.valid_id = 2
    await session.commit()
    policy = await _policy(session, 9)
    await ai_usage.record_usage(
        session, queue_id=policy.queue_id, feature="auto_reply", provider_id=broke.id, cost_hint=5.0
    )
    await assign_task(session, policy, TASK_AGENT, profile)

    task_llm = await build_task_llm(session, get_settings(), policy, TASK_AGENT)
    assert task_llm is not None
    assert [m.model for m in task_llm.models] == ["working"]
    assert isinstance(task_llm.client, _RecordedClient)


async def test_every_entry_skipped_agent_409_final_answer_none_vision_none(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    broke = await _provider(session, "allbroke", budget_cost_day=1.0)
    agent = await make_profile(session, broke, ["agent-model"])
    final = await make_profile(session, broke, ["final-model"])
    vision = await make_profile(session, broke, ["eye"], supports_vision=True)
    policy = await _policy(session, 10)
    await ai_usage.record_usage(
        session, queue_id=policy.queue_id, feature="auto_reply", provider_id=broke.id, cost_hint=5.0
    )
    await assign_task(session, policy, TASK_AGENT, agent)
    await assign_task(session, policy, TASK_FINAL_ANSWER, final)
    await assign_task(session, policy, TASK_VISION, vision)

    with pytest.raises(NoUsableModel) as excinfo:
        await build_task_llm(session, get_settings(), policy, TASK_AGENT)
    assert excinfo.value.reasons == [f"agent-model @ {broke.name}: provider_budget_day"]

    with pytest.raises(HTTPException) as http_exc:
        await build_agent_llm(session, get_settings(), policy)
    assert http_exc.value.status_code == 409
    assert "provider_budget_day" in str(http_exc.value.detail)
    assert agent.name in str(http_exc.value.detail)

    final_llm = await _resolve_final_answer_llm(
        session,
        get_settings(),
        policy,
        None,
        audit_context=AuditContext(feature="draft", run_id="r"),
        pii=PiiMapper(),
    )
    assert final_llm is None

    assert await build_vision_llm_factory(session, get_settings(), policy) is None


async def test_agent_without_profile_is_409(session: AsyncSession) -> None:
    policy = await _policy(session, 11)
    with pytest.raises(HTTPException) as excinfo:
        await build_agent_llm(session, get_settings(), policy)
    assert excinfo.value.status_code == 409


async def test_vision_skips_models_that_cannot_read_images(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    provider = await _provider(session, "vis")
    await make_model(session, provider, "blind")
    await make_model(session, provider, "eye", supports_vision=True)
    profile = await make_profile(session, provider, ["blind", "eye"])
    policy = await _policy(session, 12)
    await assign_task(session, policy, TASK_VISION, profile)

    factory = await build_vision_llm_factory(session, get_settings(), policy)
    assert factory is not None
    client = factory()
    assert isinstance(client, _RecordedClient)
    assert client.kwargs["model"] == "eye"


async def test_vision_factory_audits_with_the_serving_model(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    provider = await _provider(session, "vis-audit")
    profile = await make_profile(session, provider, ["eye"], supports_vision=True)
    policy = await _policy(session, 13)
    await assign_task(session, policy, TASK_VISION, profile)

    factory = await build_vision_llm_factory(
        session,
        get_settings(),
        policy,
        audit=AuditContext(feature="draft", run_id="r", queue_id=policy.queue_id),
    )
    assert factory is not None
    client = factory()
    context = client._context  # type: ignore[attr-defined]  # noqa: SLF001
    assert (context.feature, context.provider_id, context.model) == ("vision", provider.id, "eye")


async def test_fallback_serves_when_first_model_errors(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.ai.llm import LlmTimeoutError

    class _Client:
        def __init__(self, model: str) -> None:
            self.model = model

        async def chat(self, **_kwargs: Any) -> LlmResponse:
            if self.model == "flaky":
                raise LlmTimeoutError("slow")
            return LlmResponse(content="ok", usage=LlmUsage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_routing, "make_llm_client", lambda **kw: _Client(kw["model"]))
    provider = await _provider(session, "flaky")
    profile = await make_profile(session, provider, ["flaky", "steady"])
    policy = await _policy(session, 14)
    await assign_task(session, policy, TASK_AGENT, profile)

    task_llm = await build_agent_llm(session, get_settings(), policy)
    response = await task_llm.client.chat(messages=[LlmMessage(role="user", content="hi")])
    assert response.content == "ok"
    assert isinstance(task_llm.client, FallbackLlmClient)
    assert task_llm.client.active_model == "steady"
    assert task_llm.client.active_llm_model_id == task_llm.models[1].llm_model_id


async def test_disabled_profile_behaves_like_no_profile(
    session: AsyncSession, built: list[_RecordedClient]
) -> None:
    """valid_id != 1 on a profile = "no profile" for the task: triage falls
    back to the agent chain, final answer hands over to nothing, vision is
    off, and an agent without a usable profile is 409."""
    provider = await _provider(session, "disabled")
    agent = await make_profile(session, provider, ["agent-model"])
    triage = await make_profile(session, provider, ["triage-model"])
    final = await make_profile(session, provider, ["final-model"])
    vision = await make_profile(session, provider, ["eye"], supports_vision=True)
    policy = await _policy(session, 15)
    await assign_task(session, policy, TASK_AGENT, agent)
    await assign_task(session, policy, TASK_TRIAGE, triage)
    await assign_task(session, None, TASK_FINAL_ANSWER, final)
    await assign_task(session, policy, TASK_VISION, vision)
    for profile in (triage, final, vision):
        profile.valid_id = 2
    await session.commit()

    triage_llm = await build_task_llm(session, get_settings(), policy, TASK_TRIAGE)
    assert triage_llm is not None and triage_llm.profile_id == agent.id
    assert await build_task_llm(session, get_settings(), policy, TASK_FINAL_ANSWER) is None
    setup = await llm_routing.resolve_vision(session, get_settings(), policy)
    assert (setup.enabled, setup.factory) == (False, None)

    agent.valid_id = 2
    await session.commit()
    with pytest.raises(HTTPException) as excinfo:
        await build_agent_llm(session, get_settings(), policy)
    assert excinfo.value.status_code == 409
    assert await resolve_task_profile_id(session, policy, TASK_SUMMARY) is None
