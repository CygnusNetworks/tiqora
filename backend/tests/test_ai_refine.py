"""Tests for tiqora.ai.refine — LLM polish of an agent's own composer text.

Seed ids use the 95xx range (unique per test, ``ns`` offset) — disjoint from
the 96xx (``test_ai_runtime.py``) and 97xx (``test_ai_summary.py``) ranges —
so the session-scoped testcontainer DB is shared safely.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage
from tiqora.ai.pii import PiiMapper
from tiqora.ai.refine import (
    _SYSTEM_PROMPT,
    TONE_CONCISE,
    TONE_FORMAL,
    TONE_STANDARD,
    RefineAclDeniedError,
    RefineEmptyOutputError,
    RefinePolicyDisabledError,
    Segment,
    _build_user_message,
    _parse_sections,
    _tone_instruction,
    own_section_ids,
    refine_text,
)
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

NOW = datetime(2024, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Pure unit tests: prompt shape, tone, response parsing
# ---------------------------------------------------------------------------


def test_system_prompt_forbids_inventing_or_dropping_content() -> None:
    assert "Do not add" in _SYSTEM_PROMPT
    assert "Do not remove" in _SYSTEM_PROMPT
    # Reply language is the agent's choice, not the model's.
    assert "same language" in _SYSTEM_PROMPT
    # Signature is appended by the send pipeline; the model must not add one.
    assert "signature" in _SYSTEM_PROMPT
    # Output contract that _parse_sections depends on.
    assert "JSON" in _SYSTEM_PROMPT


def test_tone_instruction_differs_per_tone() -> None:
    assert _tone_instruction(TONE_STANDARD) != _tone_instruction(TONE_FORMAL)
    assert _tone_instruction(TONE_FORMAL) != _tone_instruction(TONE_CONCISE)


def test_own_section_ids_skips_quotes_and_blank_own_segments() -> None:
    segments = [
        Segment(kind="quote", text="> Frage\n"),
        Segment(kind="own", text="Antwort\n"),
        Segment(kind="quote", text="> Noch eine\n"),
        Segment(kind="own", text="\n  \n"),
    ]
    assert own_section_ids(segments) == [1]


def test_build_user_message_numbers_own_sections_and_marks_quotes_read_only() -> None:
    segments = [
        Segment(kind="quote", text="> geht nicht\n"),
        Segment(kind="own", text="hab neu gestartet\n"),
        Segment(kind="quote", text="> und der router?\n"),
        Segment(kind="own", text="der auch\n"),
    ]
    message = _build_user_message(segments, [1, 3], pii=PiiMapper(), mask=False)

    assert "[SECTION 1]" in message
    assert "[SECTION 3]" in message
    assert "hab neu gestartet" in message
    # Quotes are present as context but explicitly not rewritable.
    assert "> und der router?" in message
    assert "[QUOTE" in message
    assert "context only" in message.lower()


def test_build_user_message_masks_pii_when_asked() -> None:
    segments = [Segment(kind="own", text="Bitte an anna.mueller@example.org schicken\n")]
    pii = PiiMapper()
    message = _build_user_message(segments, [0], pii=pii, mask=True)
    assert "anna.mueller@example.org" not in message


def test_parse_sections_accepts_a_fenced_json_object() -> None:
    content = '```json\n{"sections": [{"id": 1, "text": "Guten Tag."}]}\n```'
    assert _parse_sections(content, [1]) == {1: "Guten Tag."}


def test_parse_sections_rejects_a_dropped_section() -> None:
    content = '{"sections": [{"id": 1, "text": "Guten Tag."}]}'
    assert _parse_sections(content, [1, 3]) is None


def test_parse_sections_rejects_an_unknown_section_id() -> None:
    content = '{"sections": [{"id": 1, "text": "a"}, {"id": 9, "text": "b"}]}'
    assert _parse_sections(content, [1]) is None


def test_parse_sections_accepts_a_stringified_id() -> None:
    # Observed in prod from Qwen3-235B: the model answers correctly but types
    # the id as a JSON string. Rejecting that throws away a good rewrite.
    content = '{"sections": [{"id": "2", "text": "Guten Tag."}]}'
    assert _parse_sections(content, [2]) == {2: "Guten Tag."}


def test_parse_sections_rejects_an_id_that_is_not_a_number() -> None:
    assert _parse_sections('{"sections": [{"id": "zwei", "text": "x"}]}', [2]) is None


def test_parse_sections_rejects_non_json() -> None:
    assert _parse_sections("Sure! Here is your improved text.", [0]) is None


def test_parse_sections_rejects_a_blank_section_text() -> None:
    content = '{"sections": [{"id": 0, "text": "   "}]}'
    assert _parse_sections(content, [0]) is None


# ---------------------------------------------------------------------------
# DB-backed tests: policy gate, ACL, usage, PII round trip, fallback
# ---------------------------------------------------------------------------


class ScriptedLlm:
    """Returns one scripted :class:`LlmResponse` per call, in order."""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = list(contents)
        self.calls = 0
        self.user_messages: list[str] = []

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        self.calls += 1
        self.user_messages.append(
            next((m.content for m in reversed(messages) if m.role == "user"), "")
        )
        return LlmResponse(
            content=self._contents.pop(0),
            usage=LlmUsage(prompt_tokens=11, completion_tokens=7),
            model="fake-model",
        )


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


#: Every (url, ns) this module seeded, so the module fixture below can delete
#: exactly those rows again. Without it the module would leak into the shared
#: testcontainer DB and fail conftest's strict leak check (CI sets
#: TIQORA_STRICT_DB_LEAKS=1); db_leak_baseline.txt is a ratchet, not a target.
_SEEDED: list[tuple[str, int]] = []


def _seed_statements(*, ns: int) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The DELETEs that remove everything :func:`_seed` and the tests create.

    Used twice: before seeding (so a re-run is idempotent) and after the module
    (so nothing is left behind).
    """
    agent_id = 9500 + ns
    group_id = 9530 + ns
    queue_id = 9500 + ns
    return (
        ("DELETE FROM queue WHERE id = :id", {"id": queue_id}),
        (
            "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
            {"uid": agent_id, "gid": group_id},
        ),
        ("DELETE FROM permission_groups WHERE id = :id", {"id": group_id}),
        ("DELETE FROM users WHERE id = :id", {"id": agent_id}),
        ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :id", {"id": queue_id}),
        ("DELETE FROM tiqora_ai_acl WHERE subject_id = :id", {"id": agent_id}),
        ("DELETE FROM tiqora_ai_usage WHERE queue_id = :id", {"id": queue_id}),
        ("DELETE FROM tiqora_ai_audit_log WHERE queue_id = :id", {"id": queue_id}),
        (
            "DELETE FROM tiqora_llm_provider WHERE name = :n",
            {"n": f"fake-refine-provider-{queue_id}"},
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, ns in _SEEDED:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt, params in _seed_statements(ns=ns):
                conn.execute(text(stmt), params)
        engine.dispose()
    _SEEDED.clear()


def _seed(sync_url: str, *, ns: int) -> dict[str, Any]:
    agent_id = 9500 + ns
    group_id = 9530 + ns
    queue_id = 9500 + ns

    _SEEDED.append((sync_url, ns))
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt, params in _seed_statements(ns=ns):
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Refine', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": f"agent.airefine.95{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"airefine-grp-95{ns}", "t": NOW},
        )
        for key in ("ro", "rw", "note", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": agent_id, "gid": group_id, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": f"AiRefineQueue95{ns}", "gid": group_id, "t": NOW},
        )
    engine.dispose()
    return {"agent_id": agent_id, "queue_id": queue_id}


async def _setup_policy(
    session: AsyncSession,
    *,
    seed: dict[str, Any],
    enabled_refine: bool = True,
    pii_masking: bool = False,
) -> None:
    provider = await ai_providers.create_provider(
        session,
        settings=get_settings(),
        change_by=1,
        name=f"fake-refine-provider-{seed['queue_id']}",
        kind="openai_compat",
        base_url="https://llm.example/v1",
        default_model="fake-model",
        api_key=None,
        extra_json=None,
        supports_tools=True,
        supports_streaming=False,
        eu_hosted=True,
    )
    await ai_policies.create_queue_policy(
        session,
        change_by=1,
        queue_id=seed["queue_id"],
        enabled_refine=enabled_refine,
        llm_provider_id=provider.id,
        pii_masking=pii_masking,
    )


pytestmark = pytest.mark.db


async def test_refine_returns_refined_sections_and_leaves_quotes_out_of_the_result(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=1)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        segments = [
            Segment(kind="quote", text="> internet geht nicht\n"),
            Segment(kind="own", text="hab den port neu geschaltet, geht wieder\n"),
        ]
        llm = ScriptedLlm(
            [json.dumps({"sections": [{"id": 1, "text": "Der Port wurde neu geschaltet."}]})]
        )
        async with factory() as session:
            result = await refine_text(
                session,
                llm=llm,
                queue_id=seed["queue_id"],
                segments=segments,
                tone=TONE_STANDARD,
                acting_user_id=seed["agent_id"],
            )

        assert result.sections == {1: "Der Port wurde neu geschaltet."}
        assert llm.calls == 1
        # The quote reached the model as context...
        assert "internet geht nicht" in llm.user_messages[0]

        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT feature, user_id, prompt_tokens, completion_tokens, success"
                        " FROM tiqora_ai_usage WHERE queue_id = :q"
                    ),
                    {"q": seed["queue_id"]},
                )
            ).all()
        assert [(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows] == [
            ("refine", seed["agent_id"], 11, 7, True)
        ]
    finally:
        await engine.dispose()


async def test_refine_falls_back_to_one_call_per_section_when_the_model_drops_one(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=2)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        segments = [
            Segment(kind="own", text="erstens\n"),
            Segment(kind="quote", text="> frage\n"),
            Segment(kind="own", text="zweitens\n"),
        ]
        llm = ScriptedLlm(
            [
                # Batch call drops section 2 → unusable, fallback kicks in.
                json.dumps({"sections": [{"id": 0, "text": "Erstens."}]}),
                json.dumps({"sections": [{"id": 0, "text": "Erstens."}]}),
                json.dumps({"sections": [{"id": 2, "text": "Zweitens."}]}),
            ]
        )
        async with factory() as session:
            result = await refine_text(
                session,
                llm=llm,
                queue_id=seed["queue_id"],
                segments=segments,
                tone=TONE_STANDARD,
                acting_user_id=seed["agent_id"],
            )

        assert result.sections == {0: "Erstens.", 2: "Zweitens."}
        assert llm.calls == 3
    finally:
        await engine.dispose()


async def test_refine_raises_when_the_model_returns_nothing_usable(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=3)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        segments = [Segment(kind="own", text="irgendwas\n")]
        llm = ScriptedLlm(["not json at all", "still not json"])
        async with factory() as session:
            with pytest.raises(RefineEmptyOutputError):
                await refine_text(
                    session,
                    llm=llm,
                    queue_id=seed["queue_id"],
                    segments=segments,
                    tone=TONE_STANDARD,
                    acting_user_id=seed["agent_id"],
                )

        async with factory() as session:
            success = (
                (
                    await session.execute(
                        text("SELECT success FROM tiqora_ai_usage WHERE queue_id = :q"),
                        {"q": seed["queue_id"]},
                    )
                )
                .scalars()
                .all()
            )
        assert [bool(s) for s in success] == [False]
    finally:
        await engine.dispose()


async def test_refine_is_refused_when_the_queue_policy_disables_it(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=4)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, enabled_refine=False)

        async with factory() as session:
            with pytest.raises(RefinePolicyDisabledError):
                await refine_text(
                    session,
                    llm=ScriptedLlm([]),
                    queue_id=seed["queue_id"],
                    segments=[Segment(kind="own", text="text\n")],
                    tone=TONE_STANDARD,
                    acting_user_id=seed["agent_id"],
                )
    finally:
        await engine.dispose()


async def test_refine_is_refused_when_the_agent_acl_denies_the_feature(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=5)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            await session.execute(
                text(
                    "INSERT INTO tiqora_ai_acl (subject_type, subject_id, feature, allowed)"
                    " VALUES ('user', :uid, 'refine', 0)"
                ),
                {"uid": seed["agent_id"]},
            )
            await session.commit()

        async with factory() as session:
            with pytest.raises(RefineAclDeniedError):
                await refine_text(
                    session,
                    llm=ScriptedLlm([]),
                    queue_id=seed["queue_id"],
                    segments=[Segment(kind="own", text="text\n")],
                    tone=TONE_STANDARD,
                    acting_user_id=seed["agent_id"],
                )
    finally:
        await engine.dispose()


async def test_refine_masks_pii_towards_the_model_and_unmasks_the_result(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed(mariadb_znuny_url, ns=6)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, pii_masking=True)

        segments = [Segment(kind="own", text="schreib an anna.mueller@example.org\n")]
        # The model echoes back whatever placeholder it was given.
        llm = ScriptedLlm([None])

        async with factory() as session:
            # Learn the placeholder the mapper produces, then script the model
            # to return it inside a well-formed response.
            probe = PiiMapper()
            masked = probe.mask("schreib an anna.mueller@example.org")
            placeholder = masked.split("an ", 1)[1].strip()
            llm._contents = [
                json.dumps(
                    {"sections": [{"id": 0, "text": f"Bitte senden Sie es an {placeholder}."}]}
                )
            ]
            result = await refine_text(
                session,
                llm=llm,
                queue_id=seed["queue_id"],
                segments=segments,
                tone=TONE_STANDARD,
                acting_user_id=seed["agent_id"],
            )

        assert "anna.mueller@example.org" not in llm.user_messages[0]
        assert result.sections[0] == "Bitte senden Sie es an anna.mueller@example.org."
    finally:
        await engine.dispose()
