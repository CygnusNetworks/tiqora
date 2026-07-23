"""DB tests for the queue-policy prompt-parts admin API ("Prompt-Bausteine").

Follows the direct-router-call pattern from ``test_ai_admin.py``: local
testcontainer only (never Prod), call router functions directly against a
real async session.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.api.v1.admin import ai as admin_ai
from tiqora.api.v1.admin.ai_schemas import (
    AiPromptPartCreate,
    AiPromptPartReorder,
    AiPromptPartUpdate,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        for table in ("tiqora_ai_prompt_part", "tiqora_ai_queue_policy"):
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


async def _create_policy(session: object, *, queue_id: int) -> int:
    row = await ai_policies.create_queue_policy(session, change_by=1, queue_id=queue_id)  # type: ignore[arg-type]
    return row.id


async def test_prompt_part_create_appends_at_end_and_lists_in_position_order(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89201)

            first = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="First", content="Alpha content"),
                _root_user(),
                session,
            )
            second = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="file", title="Second", content="Beta content"),
                _root_user(),
                session,
            )
            assert first.position == 0
            assert second.position == 1
            assert first.enabled is True

            listed = await admin_ai.list_prompt_parts_route(policy_id, _root_user(), session)
            assert [p.id for p in listed] == [first.id, second.id]
            assert [p.title for p in listed] == ["First", "Second"]
    finally:
        await engine.dispose()


async def test_prompt_part_update_title_content_enabled(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89202)
            part = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="Orig", content="Orig content"),
                _root_user(),
                session,
            )

            updated = await admin_ai.update_prompt_part_route(
                policy_id,
                part.id,
                AiPromptPartUpdate(title="Changed", content="Changed content", enabled=False),
                _root_user(),
                session,
            )
            assert updated.title == "Changed"
            assert updated.content == "Changed content"
            assert updated.enabled is False
    finally:
        await engine.dispose()


async def test_prompt_part_delete(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89203)
            part = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="ToDelete", content="x"),
                _root_user(),
                session,
            )
            await admin_ai.delete_prompt_part_route(policy_id, part.id, _root_user(), session)
            listed = await admin_ai.list_prompt_parts_route(policy_id, _root_user(), session)
            assert listed == []
    finally:
        await engine.dispose()


async def test_prompt_part_reorder(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89204)
            a = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="A", content="a"),
                _root_user(),
                session,
            )
            b = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="B", content="b"),
                _root_user(),
                session,
            )
            c = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="C", content="c"),
                _root_user(),
                session,
            )

            reordered = await admin_ai.reorder_prompt_parts_route(
                policy_id,
                AiPromptPartReorder(ordered_ids=[c.id, a.id, b.id]),
                _root_user(),
                session,
            )
            assert [p.id for p in reordered] == [c.id, a.id, b.id]
            assert [p.position for p in reordered] == [0, 1, 2]

            listed = await admin_ai.list_prompt_parts_route(policy_id, _root_user(), session)
            assert [p.id for p in listed] == [c.id, a.id, b.id]
    finally:
        await engine.dispose()


async def test_prompt_part_reorder_rejects_id_set_mismatch(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89205)
            a = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="A", content="a"),
                _root_user(),
                session,
            )
            b = await admin_ai.create_prompt_part_route(
                policy_id,
                AiPromptPartCreate(kind="note", title="B", content="b"),
                _root_user(),
                session,
            )

            # Missing id.
            with pytest.raises(HTTPException) as exc_missing:
                await admin_ai.reorder_prompt_parts_route(
                    policy_id, AiPromptPartReorder(ordered_ids=[a.id]), _root_user(), session
                )
            assert exc_missing.value.status_code == 422

            # Extra/unknown id.
            with pytest.raises(HTTPException) as exc_extra:
                await admin_ai.reorder_prompt_parts_route(
                    policy_id,
                    AiPromptPartReorder(ordered_ids=[a.id, b.id, 999_999]),
                    _root_user(),
                    session,
                )
            assert exc_extra.value.status_code == 422

            # Duplicate id.
            with pytest.raises(HTTPException) as exc_dup:
                await admin_ai.reorder_prompt_parts_route(
                    policy_id,
                    AiPromptPartReorder(ordered_ids=[a.id, a.id]),
                    _root_user(),
                    session,
                )
            assert exc_dup.value.status_code == 422
    finally:
        await engine.dispose()


async def test_prompt_part_unknown_policy_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.list_prompt_parts_route(999_999, _root_user(), session)
            assert exc_info.value.status_code == 404

            with pytest.raises(HTTPException) as exc_info2:
                await admin_ai.create_prompt_part_route(
                    999_999,
                    AiPromptPartCreate(kind="note", title="x", content="y"),
                    _root_user(),
                    session,
                )
            assert exc_info2.value.status_code == 404
    finally:
        await engine.dispose()


async def test_prompt_part_unknown_part_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89206)
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.update_prompt_part_route(
                    policy_id, 999_999, AiPromptPartUpdate(title="x"), _root_user(), session
                )
            assert exc_info.value.status_code == 404

            with pytest.raises(HTTPException) as exc_info2:
                await admin_ai.delete_prompt_part_route(policy_id, 999_999, _root_user(), session)
            assert exc_info2.value.status_code == 404
    finally:
        await engine.dispose()


async def test_prompt_part_part_from_other_policy_is_404(mariadb_znuny_url: str) -> None:
    """A part belonging to a different policy must not be reachable through
    another policy's sub-resource path."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_a = await _create_policy(session, queue_id=89207)
            policy_b = await _create_policy(session, queue_id=89208)
            part = await admin_ai.create_prompt_part_route(
                policy_a,
                AiPromptPartCreate(kind="note", title="A-part", content="x"),
                _root_user(),
                session,
            )
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.update_prompt_part_route(
                    policy_b, part.id, AiPromptPartUpdate(title="hijack"), _root_user(), session
                )
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_prompt_part_invalid_kind_and_oversized_content_422(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            policy_id = await _create_policy(session, queue_id=89209)

            with pytest.raises(ai_policies.PromptPartValidationError):
                await ai_policies.create_prompt_part(
                    session,
                    change_by=1,
                    policy_id=policy_id,
                    kind="bogus",
                    title="x",
                    content="y",
                )

            with pytest.raises(ai_policies.PromptPartValidationError):
                await ai_policies.create_prompt_part(
                    session,
                    change_by=1,
                    policy_id=policy_id,
                    kind="note",
                    title="x",
                    content="a" * (ai_policies.PROMPT_PART_CONTENT_MAX_LEN + 1),
                )
    finally:
        await engine.dispose()
