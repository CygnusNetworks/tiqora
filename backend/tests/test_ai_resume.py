"""HTTP test for ``POST /tickets/{id}/ai/resume`` — the manual override that
clears the AI->human handoff flag (``tiqora_ai_ticket_state.ai_escalated_at``)
without requiring a customer-visible reply. See
:func:`tiqora.domain.ticket_write_service.resume_ai_automation`.

Reuses the seed/client helpers from ``test_ai_manual_draft_async.py``
(ns=74 — the highest ``ns`` taken in that file is 73; see its own docstring
for the registry of ranges already claimed elsewhere in the suite).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_ai_manual_draft_async import (
    _cleanup_ticket,
    _client_for,
    _group_id,
    _to_async_url,
)
from tests.test_ai_runtime import _seed_ticket as _seed_ticket_raw
from tests.test_ai_runtime import _setup_policy
from tiqora.ai.models import AUTONOMY_FULL

pytestmark = pytest.mark.db


async def _seed(mariadb_znuny_url: str, *, ns: int, monkeypatch: pytest.MonkeyPatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    seed = _seed_ticket_raw(mariadb_znuny_url, ns=ns)
    async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL, enabled_manual_assist=True)
    await async_engine.dispose()
    login = f"agent.airesume.96{ns}"
    client, client_engine = await _client_for(
        mariadb_znuny_url, user_id=seed["agent_id"], login=login, monkeypatch=monkeypatch
    )
    return seed, client, client_engine


async def test_resume_clears_flag_and_writes_internal_note(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=74, monkeypatch=monkeypatch)
    try:
        async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        async with async_engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at)"
                    " VALUES (:tid, :at)"
                ),
                {"tid": seed["ticket_id"], "at": datetime.now(UTC).replace(tzinfo=None)},
            )
            await conn.commit()
        await async_engine.dispose()

        # Flag is visible via GET before the fix.
        state_resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert state_resp.status_code == 200
        assert state_resp.json()["ai_escalated_at"] is not None

        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/resume")
        assert resp.status_code == 204

        state_resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert state_resp.status_code == 200
        assert state_resp.json()["ai_escalated_at"] is None

        async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        async with async_engine.connect() as conn:
            note = (
                await conn.execute(
                    text(
                        "SELECT is_visible_for_customer FROM article"
                        " WHERE ticket_id = :tid ORDER BY id DESC LIMIT 1"
                    ),
                    {"tid": seed["ticket_id"]},
                )
            ).first()
        await async_engine.dispose()
        assert note is not None
        assert note[0] == 0
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_resume_without_note_permission_is_forbidden(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user with no group membership on the ticket's queue is denied —
    proves the endpoint is actually gated, not an open reset button."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=74, monkeypatch=monkeypatch)
    try:
        outsider_client, outsider_engine = await _client_for(
            mariadb_znuny_url,
            user_id=999999,
            login="agent.airesume.outsider",
            monkeypatch=monkeypatch,
        )
        try:
            resp = await outsider_client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/resume")
            assert resp.status_code == 403
        finally:
            await outsider_client.aclose()
            await outsider_engine.dispose()
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )
