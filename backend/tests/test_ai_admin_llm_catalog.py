"""DB tests for the admin API of models, profiles and task assignments
(``tiqora.api.v1.admin.ai_models`` + the provider/queue-policy parts of
``tiqora.api.v1.admin.ai``, logic in ``tiqora.ai.llm_catalog``).

Route functions are called directly against a real MariaDB session (same
pattern as ``test_ai_admin.py``). No network: provider HTTP goes through a
``MockTransport`` client injected via ``tiqora.ai.providers.http_client``.
Queue policies use queue ids in the 97xx range plus the seeded queue 4.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import providers as ai_providers
from tiqora.ai.llm_routing import (
    TASK_AGENT,
    TASK_SUMMARY,
    TASK_TRIAGE,
    TASK_VISION,
    resolve_task_profile_id,
)
from tiqora.ai.models import (
    TiqoraAiAuditLog,
    TiqoraAiQueuePolicy,
    TiqoraAiQueueTaskProfile,
    TiqoraLlmModel,
    TiqoraLlmProvider,
)
from tiqora.api.v1.admin import ai as admin_ai
from tiqora.api.v1.admin import ai_models
from tiqora.api.v1.admin.ai_schemas import (
    AiQueuePolicyCreate,
    AiQueuePolicyUpdate,
    AiTaskProfileItem,
    LlmModelIn,
    LlmModelOut,
    LlmProfileIn,
    LlmProfileOut,
    LlmProviderCreate,
    LlmProviderUpdate,
)
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

_PREFIX = "catalog-test-"
_QUEUE_BASE = 9700
_SEEDED_QUEUE = 4
_URLS: set[str] = set()
_BASE_URL = "http://10.20.30.40/v1"  # IP literal: no DNS in the SSRF pin


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


_OWN_PROFILES = "SELECT id FROM tiqora_llm_profile WHERE name LIKE :p"
_OWN_PROVIDERS = "SELECT id FROM tiqora_llm_provider WHERE name LIKE :p"


def _cleanup(sync_url: str) -> None:
    """Deletes only what this module created (prefixed names, 97xx queues)."""
    own = {"p": f"{_PREFIX}%"}
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM tiqora_ai_task_default"
                f" WHERE profile_id IN (SELECT id FROM ({_OWN_PROFILES}) AS x)"
            ),
            own,
        )
        conn.execute(
            text(
                "DELETE FROM tiqora_ai_queue_policy"
                " WHERE queue_id BETWEEN :a AND :b OR queue_id = :s"
            ),
            {"a": _QUEUE_BASE, "b": _QUEUE_BASE + 99, "s": _SEEDED_QUEUE},
        )
        conn.execute(text("DELETE FROM tiqora_llm_profile WHERE name LIKE :p"), own)
        conn.execute(
            text(
                "DELETE FROM tiqora_ai_audit_log WHERE feature = 'test'"
                f" AND provider_id IN (SELECT id FROM ({_OWN_PROVIDERS}) AS x)"
            ),
            own,
        )
        conn.execute(text("DELETE FROM tiqora_llm_provider WHERE name LIKE :p"), own)
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
    get_settings.cache_clear()
    async_engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    try:
        async with factory() as s:
            yield s
    finally:
        await async_engine.dispose()
        _cleanup(mariadb_znuny_url)
        get_settings.cache_clear()


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


async def _provider(session: AsyncSession, name: str, **kwargs: Any) -> int:
    created = await admin_ai.create_llm_provider(
        LlmProviderCreate(name=f"{_PREFIX}{name}", base_url=_BASE_URL, api_key="sk-test", **kwargs),
        _admin(),
        session,
    )
    return created.id


async def _model(
    session: AsyncSession, provider_id: int, model_id: str, **kwargs: Any
) -> LlmModelOut:
    return await ai_models.create_llm_model(
        LlmModelIn(provider_id=provider_id, model_id=model_id, **kwargs), _admin(), session
    )


async def _profile(
    session: AsyncSession, name: str, model_ids: list[int], **kw: Any
) -> LlmProfileOut:
    return await ai_models.create_llm_profile(
        LlmProfileIn(name=f"{_PREFIX}{name}", llm_model_ids=model_ids, **kw), _admin(), session
    )


async def _defaults(session: AsyncSession, **tasks: int | None) -> list[AiTaskProfileItem]:
    return await ai_models.put_ai_task_defaults(
        [AiTaskProfileItem(task=t, profile_id=p) for t, p in tasks.items()], _admin(), session
    )


async def _expect(status_code: int, coro: Any) -> str:
    with pytest.raises(HTTPException) as excinfo:
        await coro
    assert excinfo.value.status_code == status_code, excinfo.value.detail
    return str(excinfo.value.detail)


def _items(**tasks: int | None) -> list[AiTaskProfileItem]:
    return [AiTaskProfileItem(task=t, profile_id=p) for t, p in tasks.items()]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def test_provider_kind_other_than_openai_compat_is_rejected(session: AsyncSession) -> None:
    detail = await _expect(
        422,
        admin_ai.create_llm_provider(
            LlmProviderCreate(name=f"{_PREFIX}anthropic", kind="anthropic", base_url=_BASE_URL),
            _admin(),
            session,
        ),
    )
    assert detail == "Nur OpenAI-kompatible Provider werden unterstützt."
    provider_id = await _provider(session, "ok")
    detail = await _expect(
        422,
        admin_ai.update_llm_provider(
            provider_id, LlmProviderUpdate(kind="anthropic"), _admin(), session
        ),
    )
    assert detail == "Nur OpenAI-kompatible Provider werden unterstützt."


def _mock_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        ai_providers,
        "http_client",
        lambda timeout_seconds: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
    )
    return seen


async def test_remote_models_and_provider_test_read_the_model_list(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_id = await _provider(session, "remote")
    seen = _mock_http(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "zeta-large"},
                    {"id": "alpha-mini"},
                    {"id": "alpha-mini"},
                    {"object": "no id"},
                ]
            },
        ),
    )
    out = await admin_ai.list_llm_provider_remote_models(provider_id, _admin(), session)
    assert out.models == ["alpha-mini", "zeta-large"]
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-test"

    result = await admin_ai.test_llm_provider(provider_id, _admin(), session)
    assert (result.ok, result.detail, result.model_count) == (True, None, 2)


async def test_remote_models_provider_error_is_502_with_short_text(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_id = await _provider(session, "broken")
    _mock_http(monkeypatch, lambda _r: httpx.Response(401, text="invalid api key " + "x" * 600))
    detail = await _expect(
        502, admin_ai.list_llm_provider_remote_models(provider_id, _admin(), session)
    )
    assert detail.startswith("HTTP 401: invalid api key")
    assert len(detail) <= 300

    result = await admin_ai.test_llm_provider(provider_id, _admin(), session)
    assert result.ok is False and result.model_count is None
    assert result.detail is not None and "401" in result.detail and len(result.detail) <= 300

    _mock_http(monkeypatch, lambda _r: httpx.Response(200, json={"models": []}))
    detail = await _expect(
        502, admin_ai.list_llm_provider_remote_models(provider_id, _admin(), session)
    )
    assert "data[]" in detail
    await _expect(404, admin_ai.list_llm_provider_remote_models(999_999, _admin(), session))


async def test_provider_delete_409_when_its_models_are_in_profiles(session: AsyncSession) -> None:
    used = await _provider(session, "used")
    model = await _model(session, used, "m-used")
    await _profile(session, "holds-used", [model.id])
    detail = await _expect(409, admin_ai.delete_llm_provider(used, _admin(), session))
    assert (
        detail == f"Modelle dieses Providers werden in Profil(en) „{_PREFIX}holds-used“ verwendet."
    )

    free = await _provider(session, "free")
    await _model(session, free, "m-free")
    await admin_ai.delete_llm_provider(free, _admin(), session)
    remaining = (
        await session.execute(select(TiqoraLlmModel.id).where(TiqoraLlmModel.provider_id == free))
    ).all()
    assert remaining == []  # models cascade with their provider


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def test_model_crud_and_validation(session: AsyncSession) -> None:
    provider_id = await _provider(session, "models", price_currency="EUR")
    created = await _model(
        session,
        provider_id,
        "  vendor/big-1  ",
        display_name="Big",
        supports_vision=True,
        context_tokens=0,
        max_tool_rounds=0,
        price_input_per_1m=1.5,
        price_output_per_1m=6.0,
    )
    assert created.model_id == "vendor/big-1"
    assert created.label == "Big"
    assert created.provider_name == f"{_PREFIX}models"
    assert created.price_currency == "EUR"
    assert (created.context_tokens, created.max_tool_rounds) == (None, None)
    assert (created.supports_tools, created.supports_vision) == (True, True)
    assert created.used_in_profiles == []

    await _expect(409, _model(session, provider_id, "vendor/big-1"))
    await _expect(422, _model(session, 999_999, "x"))
    await _expect(422, _model(session, provider_id, "cheap", price_input_per_1m=-1))
    await _expect(422, _model(session, provider_id, "   "))

    updated = await ai_models.update_llm_model(
        created.id,
        LlmModelIn(provider_id=provider_id, model_id="vendor/big-2", max_tool_rounds=12),
        _admin(),
        session,
    )
    assert (updated.model_id, updated.display_name, updated.label) == (
        "vendor/big-2",
        None,
        "vendor/big-2",
    )
    assert updated.max_tool_rounds == 12
    await _profile(session, "uses-big", [created.id])
    listed = {m.id: m for m in await ai_models.list_llm_models(_admin(), session)}
    assert listed[created.id].used_in_profiles == [f"{_PREFIX}uses-big"]

    await _expect(
        404,
        ai_models.update_llm_model(
            999_999, LlmModelIn(provider_id=provider_id, model_id="x"), _admin(), session
        ),
    )
    await _expect(404, ai_models.delete_llm_model(999_999, _admin(), session))


async def test_model_delete_409_when_used_in_profiles(session: AsyncSession) -> None:
    provider_id = await _provider(session, "del")
    used = await _model(session, provider_id, "used")
    await _profile(session, "b-chain", [used.id])
    await _profile(session, "a-chain", [used.id])
    detail = await _expect(409, ai_models.delete_llm_model(used.id, _admin(), session))
    assert detail == (
        f"Modell wird in Profil(en) „{_PREFIX}a-chain“, „{_PREFIX}b-chain“ verwendet."
    )

    unused = await _model(session, provider_id, "unused")
    await ai_models.delete_llm_model(unused.id, _admin(), session)
    assert await session.get(TiqoraLlmModel, unused.id) is None


async def test_model_capability_loss_422_when_an_assigned_task_needs_it(
    session: AsyncSession,
) -> None:
    provider_id = await _provider(session, "caps")
    tools = await _model(session, provider_id, "tools")
    eye = await _model(session, provider_id, "eye", supports_vision=True, supports_tools=False)
    agent = await _profile(session, "agent", [tools.id])
    vision = await _profile(session, "vision", [eye.id])
    await _defaults(session, agent=agent.id, vision=vision.id)

    detail = await _expect(
        422,
        ai_models.update_llm_model(
            tools.id,
            LlmModelIn(provider_id=provider_id, model_id="tools", supports_tools=False),
            _admin(),
            session,
        ),
    )
    assert "Recherche und Werkzeuge (global)" in detail
    assert "kann keine Werkzeuge nutzen" in detail
    detail = await _expect(
        422,
        ai_models.update_llm_model(
            eye.id,
            LlmModelIn(provider_id=provider_id, model_id="eye", supports_tools=False),
            _admin(),
            session,
        ),
    )
    assert "Bilder beschreiben (global)" in detail
    # Losing a capability no assigned task needs is fine.
    ok = await ai_models.update_llm_model(
        tools.id,
        LlmModelIn(provider_id=provider_id, model_id="tools", display_name="Tools"),
        _admin(),
        session,
    )
    assert ok.label == "Tools"


async def test_model_test_probes_chat_with_tools_and_audits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_id = await _provider(session, "probe")
    model = await _model(session, provider_id, "probe-model")
    seen = _mock_http(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "model": "probe-model-2026",
                "choices": [
                    {"message": {"tool_calls": [{"id": "1", "function": {"name": "ping"}}]}}
                ],
            },
        ),
    )
    result = await ai_models.test_llm_model(model.id, _admin(), session)
    assert (result.ok, result.model, result.tool_calling_ok, result.error) == (
        True,
        "probe-model-2026",
        True,
        None,
    )
    body = seen[0].read().decode()
    assert '"model":"probe-model"' in body.replace(" ", "") and '"tools"' in body
    await session.commit()  # end the snapshot: the audit row has its own transaction
    audit = (
        await session.execute(
            select(TiqoraAiAuditLog).where(TiqoraAiAuditLog.llm_model_id == model.id)
        )
    ).scalar_one()
    assert audit.feature == "test" and audit.provider_id == provider_id

    _mock_http(monkeypatch, lambda _r: httpx.Response(404, text="model not found"))
    failed = await ai_models.test_llm_model(model.id, _admin(), session)
    assert failed.ok is False and "404" in (failed.error or "")

    # A JSON body that is not an object is a failed probe, not a 500.
    for body in ([{"model": "x"}], "pong"):
        _mock_http(monkeypatch, lambda _r, body=body: httpx.Response(200, json=body))
        odd = await ai_models.test_llm_model(model.id, _admin(), session)
        assert (odd.ok, odd.tool_calling_ok) == (False, False)
        assert "kein JSON-Objekt" in (odd.error or "")


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


async def test_profile_crud_entries_order_and_used_by(session: AsyncSession) -> None:
    p1 = await _provider(session, "prof-a")
    p2 = await _provider(session, "prof-b")
    first = await _model(session, p1, "first", display_name="Erstes")
    second = await _model(session, p2, "second", supports_vision=True)
    created = await _profile(session, "chain", [second.id, first.id], timeout_seconds=0)
    assert created.timeout_seconds is None
    assert [(e.llm_model_id, e.model_label, e.provider_name) for e in created.entries] == [
        (second.id, "second", f"{_PREFIX}prof-b"),
        (first.id, "Erstes", f"{_PREFIX}prof-a"),
    ]
    assert created.entries[0].supports_vision is True
    assert created.used_by == []

    await _expect(409, _profile(session, "chain", [first.id]))
    await _expect(422, _profile(session, "dupes", [first.id, first.id]))
    await _expect(422, _profile(session, "ghost", [999_999]))
    with pytest.raises(ValidationError):
        LlmProfileIn(name="empty", llm_model_ids=[])

    updated = await ai_models.update_llm_profile(
        created.id,
        LlmProfileIn(
            name=f"{_PREFIX}chain-2",
            description="  Hauptkette ",
            timeout_seconds=45,
            llm_model_ids=[first.id, second.id],
        ),
        _admin(),
        session,
    )
    assert [e.llm_model_id for e in updated.entries] == [first.id, second.id]
    assert (updated.name, updated.description, updated.timeout_seconds) == (
        f"{_PREFIX}chain-2",
        "Hauptkette",
        45,
    )

    queue_name = (
        await session.execute(text("SELECT name FROM queue WHERE id = :i"), {"i": _SEEDED_QUEUE})
    ).scalar_one()
    await _defaults(session, agent=created.id)
    policy = await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_SEEDED_QUEUE, task_profiles=_items(triage=created.id)),
        _admin(),
        session,
    )
    listed = {p.id: p for p in await ai_models.list_llm_profiles(_admin(), session)}
    assert [u.model_dump() for u in listed[created.id].used_by] == [
        {"task": TASK_AGENT, "queue_policy_id": None, "queue_name": None},
        {"task": TASK_TRIAGE, "queue_policy_id": policy.id, "queue_name": queue_name},
    ]
    await _expect(404, ai_models.delete_llm_profile(999_999, _admin(), session))


async def test_profile_delete_409_when_assigned(session: AsyncSession) -> None:
    provider_id = await _provider(session, "pdel")
    model = await _model(session, provider_id, "m")
    profile = await _profile(session, "assigned", [model.id])
    await _defaults(session, summary=profile.id)
    await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_QUEUE_BASE + 1, task_profiles=_items(agent=profile.id)),
        _admin(),
        session,
    )
    detail = await _expect(409, ai_models.delete_llm_profile(profile.id, _admin(), session))
    assert detail.startswith("Profil wird verwendet für: Zusammenfassen (global), ")
    assert "Recherche und Werkzeuge (Queue #" in detail  # queue 97xx has no Znuny row

    free = await _profile(session, "free", [model.id])
    await ai_models.delete_llm_profile(free.id, _admin(), session)
    assert all(p.id != free.id for p in await ai_models.list_llm_profiles(_admin(), session))


async def test_profile_edit_dropping_the_last_tools_model_while_agent_is_422(
    session: AsyncSession,
) -> None:
    provider_id = await _provider(session, "pedit")
    tools = await _model(session, provider_id, "tools")
    chat = await _model(session, provider_id, "chat-only", supports_tools=False)
    profile = await _profile(session, "agent-chain", [tools.id])
    await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_QUEUE_BASE + 2, task_profiles=_items(agent=profile.id)),
        _admin(),
        session,
    )
    detail = await _expect(
        422,
        ai_models.update_llm_profile(
            profile.id,
            LlmProfileIn(name=profile.name, llm_model_ids=[chat.id]),
            _admin(),
            session,
        ),
    )
    assert "Recherche und Werkzeuge (Queue #" in detail
    assert "„chat-only“ kann keine Werkzeuge nutzen" in detail
    # Adding the chat model as a fallback also breaks the needs (every entry).
    await _expect(
        422,
        ai_models.update_llm_profile(
            profile.id,
            LlmProfileIn(name=profile.name, llm_model_ids=[tools.id, chat.id]),
            _admin(),
            session,
        ),
    )
    # Unassigned, the same edit is fine.
    other = await _profile(session, "unassigned", [tools.id])
    ok = await ai_models.update_llm_profile(
        other.id, LlmProfileIn(name=other.name, llm_model_ids=[chat.id]), _admin(), session
    )
    assert [e.llm_model_id for e in ok.entries] == [chat.id]


async def test_disabling_the_profile_an_enabled_queue_runs_on_is_422(
    session: AsyncSession,
) -> None:
    provider_id = await _provider(session, "pdis")
    model = await _model(session, provider_id, "m")
    profile = await _profile(session, "only-agent", [model.id])
    spare = await _profile(session, "spare", [model.id])
    await _defaults(session, agent=profile.id)
    await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_QUEUE_BASE + 3, enabled_manual_assist=True),
        _admin(),
        session,
    )
    detail = await _expect(
        422,
        ai_models.update_llm_profile(
            profile.id,
            LlmProfileIn(name=profile.name, llm_model_ids=[model.id], valid_id=2),
            _admin(),
            session,
        ),
    )
    assert f"#{_QUEUE_BASE + 3} (KI-Entwurf)" in detail
    disabled = await ai_models.update_llm_profile(
        spare.id,
        LlmProfileIn(name=spare.name, llm_model_ids=[model.id], valid_id=2),
        _admin(),
        session,
    )
    assert disabled.valid_id == 2


# ---------------------------------------------------------------------------
# Global task defaults
# ---------------------------------------------------------------------------


async def test_task_defaults_get_put_and_validation(session: AsyncSession) -> None:
    provider_id = await _provider(session, "defaults")
    tools = await _model(session, provider_id, "tools")
    eye = await _model(session, provider_id, "eye", supports_vision=True)
    agent = await _profile(session, "d-agent", [tools.id])
    vision = await _profile(session, "d-vision", [eye.id])

    initial = await ai_models.get_ai_task_defaults(_admin(), session)
    assert [(i.task, i.profile_id) for i in initial] == [
        ("agent", None),
        ("final_answer", None),
        ("triage", None),
        ("summary", None),
        ("refine", None),
        ("vision", None),
    ]
    out = await _defaults(session, agent=agent.id, vision=vision.id, summary=None)
    assert {i.task: i.profile_id for i in out} == {
        "agent": agent.id,
        "final_answer": None,
        "triage": None,
        "summary": None,
        "refine": None,
        "vision": vision.id,
    }
    assert await resolve_task_profile_id(session, None, TASK_SUMMARY) == agent.id

    detail = await _expect(422, _defaults(session, agent=agent.id, vision=agent.id))
    assert "Bilder beschreiben" in detail and "kann keine Bilder lesen" in detail
    await _expect(422, _defaults(session, bogus=agent.id))
    await _expect(422, _defaults(session, agent=999_999))
    await _expect(
        422,
        ai_models.put_ai_task_defaults(
            _items(agent=agent.id) + _items(agent=None), _admin(), session
        ),
    )
    # Tasks left out are reset to "no profile".
    out = await _defaults(session, agent=agent.id)
    assert {i.task: i.profile_id for i in out}[TASK_VISION] is None


async def test_task_defaults_put_that_strands_an_enabled_queue_is_422(
    session: AsyncSession,
) -> None:
    provider_id = await _provider(session, "strand")
    model = await _model(session, provider_id, "m")
    agent = await _profile(session, "s-agent", [model.id])
    await _defaults(session, agent=agent.id)
    await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_QUEUE_BASE + 4, enabled_summary=True),
        _admin(),
        session,
    )
    # A queue that overrides the agent itself is not affected.
    await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(
            queue_id=_QUEUE_BASE + 5,
            enabled_manual_assist=True,
            task_profiles=_items(agent=agent.id),
        ),
        _admin(),
        session,
    )
    detail = await _expect(422, _defaults(session, agent=None, final_answer=agent.id))
    assert detail == (
        "Mit diesen Vorgaben hätten diese Queues kein Modell mehr für: "
        f"#{_QUEUE_BASE + 4} (Zusammenfassen)."
    )
    # Giving summary its own global profile keeps the queue served.
    out = await _defaults(session, summary=agent.id)
    assert {i.task: i.profile_id for i in out}[TASK_AGENT] is None


# ---------------------------------------------------------------------------
# Queue policies: task_profiles + "needs a profile"
# ---------------------------------------------------------------------------


async def test_queue_policy_task_profiles_roundtrip(session: AsyncSession) -> None:
    provider_id = await _provider(session, "qp")
    tools = await _model(session, provider_id, "tools")
    eye = await _model(session, provider_id, "eye", supports_vision=True)
    agent = await _profile(session, "q-agent", [tools.id])
    vision = await _profile(session, "q-vision", [eye.id])

    created = await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(
            queue_id=_QUEUE_BASE + 10,
            enabled_manual_assist=True,
            task_profiles=_items(vision=None, agent=agent.id),
        ),
        _admin(),
        session,
    )
    assert [i.model_dump() for i in created.task_profiles] == [
        {"task": "agent", "profile_id": agent.id},
        {"task": "vision", "profile_id": None},
    ]
    # Omitted task_profiles keep the overrides.
    kept = await admin_ai.update_queue_policy_route(
        created.id, AiQueuePolicyUpdate(system_prompt="x"), _admin(), session
    )
    assert len(kept.task_profiles) == 2
    replaced = await admin_ai.update_queue_policy_route(
        created.id,
        AiQueuePolicyUpdate(task_profiles=_items(agent=agent.id, vision=vision.id)),
        _admin(),
        session,
    )
    assert {i.task: i.profile_id for i in replaced.task_profiles} == {
        "agent": agent.id,
        "vision": vision.id,
    }
    listed = {p.id: p for p in await admin_ai.list_queue_policies_route(_admin(), session)}
    assert listed[created.id].task_profiles == replaced.task_profiles
    rows = (
        await session.execute(
            select(TiqoraAiQueueTaskProfile.task).where(
                TiqoraAiQueueTaskProfile.queue_policy_id == created.id
            )
        )
    ).all()
    assert sorted(r[0] for r in rows) == ["agent", "vision"]

    await _expect(
        422,
        admin_ai.update_queue_policy_route(
            created.id, AiQueuePolicyUpdate(task_profiles=_items(nope=None)), _admin(), session
        ),
    )
    detail = await _expect(
        422,
        admin_ai.update_queue_policy_route(
            created.id,
            AiQueuePolicyUpdate(task_profiles=_items(agent=agent.id, vision=agent.id)),
            _admin(),
            session,
        ),
    )
    assert "kann keine Bilder lesen" in detail
    await _expect(
        422,
        admin_ai.create_queue_policy_route(
            AiQueuePolicyCreate(
                queue_id=_QUEUE_BASE + 11, task_profiles=_items(final_answer=999_999)
            ),
            _admin(),
            session,
        ),
    )
    # Nothing was written for the rejected create.
    assert (
        await session.execute(
            select(TiqoraAiQueuePolicy.id).where(TiqoraAiQueuePolicy.queue_id == _QUEUE_BASE + 11)
        )
    ).first() is None


async def test_enabling_an_ai_feature_needs_a_profile(session: AsyncSession) -> None:
    detail = await _expect(
        422,
        admin_ai.create_queue_policy_route(
            AiQueuePolicyCreate(
                queue_id=_QUEUE_BASE + 20, enabled_manual_assist=True, enabled_refine=True
            ),
            _admin(),
            session,
        ),
    )
    assert detail == (
        "Kein Modellprofil für: KI-Entwurf, Text verfeinern. Bitte der Aufgabe "
        "„Recherche und Werkzeuge“ ein Profil zuweisen – in dieser Queue oder global "
        "unter KI → Modelle → Aufgaben."
    )

    provider_id = await _provider(session, "needs")
    model = await _model(session, provider_id, "m")
    summary = await _profile(session, "n-summary", [model.id])
    agent = await _profile(session, "n-agent", [model.id])
    # Summary with its own profile needs no agent profile.
    policy = await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(
            queue_id=_QUEUE_BASE + 20,
            enabled_summary=True,
            task_profiles=_items(summary=summary.id),
        ),
        _admin(),
        session,
    )
    # Enabling manual assist later: 422 until the agent resolves somewhere.
    await _expect(
        422,
        admin_ai.update_queue_policy_route(
            policy.id, AiQueuePolicyUpdate(enabled_manual_assist=True), _admin(), session
        ),
    )
    await _defaults(session, agent=agent.id)
    enabled = await admin_ai.update_queue_policy_route(
        policy.id, AiQueuePolicyUpdate(enabled_manual_assist=True), _admin(), session
    )
    assert enabled.enabled_manual_assist is True
    # An explicit "no own profile" override for the agent strands it again.
    detail = await _expect(
        422,
        admin_ai.update_queue_policy_route(
            policy.id,
            AiQueuePolicyUpdate(task_profiles=_items(agent=None, summary=summary.id)),
            _admin(),
            session,
        ),
    )
    assert detail.startswith("Kein Modellprofil für: KI-Entwurf.")
    # Disabling the summary profile is allowed: summary falls back to the
    # agent's profile, so the queue stays served.
    disabled = await ai_models.update_llm_profile(
        summary.id,
        LlmProfileIn(name=summary.name, llm_model_ids=[model.id], valid_id=2),
        _admin(),
        session,
    )
    assert disabled.valid_id == 2
    # Turning features off is never blocked.
    off = await admin_ai.update_queue_policy_route(
        policy.id,
        AiQueuePolicyUpdate(
            enabled_manual_assist=False,
            enabled_summary=False,
            task_profiles=_items(agent=None),
        ),
        _admin(),
        session,
    )
    assert off.enabled_summary is False
    assert [i.model_dump() for i in off.task_profiles] == [{"task": "agent", "profile_id": None}]
    row = await session.get(TiqoraAiQueuePolicy, policy.id)
    assert await resolve_task_profile_id(session, row, TASK_SUMMARY) is None


async def test_provider_row_kind_is_left_alone(session: AsyncSession) -> None:
    """Existing non-OpenAI rows stay readable and editable (kind untouched)."""
    provider_id = await _provider(session, "legacy")
    row = await session.get(TiqoraLlmProvider, provider_id)
    assert row is not None
    row.kind = "anthropic"
    await session.commit()
    updated = await admin_ai.update_llm_provider(
        provider_id, LlmProviderUpdate(eu_hosted=True), _admin(), session
    )
    assert (updated.kind, updated.eu_hosted) == ("anthropic", True)


async def test_queue_already_without_profile_still_accepts_unrelated_edits(
    session: AsyncSession,
) -> None:
    """Only a *new* gap is rejected: a queue that already had manual assist
    on without any profile (e.g. its profile was deleted via the DB) can be
    edited, but enabling another feature without a profile is still 422."""
    created = await admin_ai.create_queue_policy_route(
        AiQueuePolicyCreate(queue_id=_QUEUE_BASE + 30), _admin(), session
    )
    row = await session.get(TiqoraAiQueuePolicy, created.id)
    assert row is not None
    row.enabled_manual_assist = True
    await session.commit()

    edited = await admin_ai.update_queue_policy_route(
        created.id, AiQueuePolicyUpdate(system_prompt="Neu"), _admin(), session
    )
    assert (edited.system_prompt, edited.enabled_manual_assist) == ("Neu", True)

    detail = await _expect(
        422,
        admin_ai.update_queue_policy_route(
            created.id,
            AiQueuePolicyUpdate(enabled_auto_reply=True, service_user_id=1),
            _admin(),
            session,
        ),
    )
    assert detail.startswith("Kein Modellprofil für: Automatische Antworten.")
