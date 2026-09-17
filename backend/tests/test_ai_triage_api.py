"""HTTP tests for the ticket-side triage routes:
``POST /tickets/{id}/ai/triage/{triage_id}/accept`` and ``/reject``, plus the
``triage`` field on ``GET /tickets/{id}/ai``.

Reuses the seed/client helpers from ``test_ai_manual_draft_async.py`` (see its
docstring for the ``ns`` registry); ns=75–83 — 74 is taken by
``test_ai_resume.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_ai_manual_draft_async import (
    _cleanup_ticket,
    _client_for,
    _group_id,
    _to_async_url,
)
from tests.test_ai_runtime import _seed_ticket as _seed_ticket_raw
from tests.test_ai_runtime import _setup_policy
from tiqora.ai.models import (
    AUTONOMY_FULL,
    TRIAGE_STATUS_ACCEPTED,
    TRIAGE_STATUS_OPEN,
    TRIAGE_STATUS_REJECTED,
)

pytestmark = pytest.mark.db


async def _seed(
    mariadb_znuny_url: str, *, ns: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Any, Any]:
    seed = _seed_ticket_raw(mariadb_znuny_url, ns=ns)
    async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL, enabled_manual_assist=True)
    await async_engine.dispose()
    client, client_engine = await _client_for(
        mariadb_znuny_url,
        user_id=seed["agent_id"],
        login=f"agent.airuntime.96{ns}",
        monkeypatch=monkeypatch,
    )
    return seed, client, client_engine


async def _insert_triage(
    sync_url: str,
    *,
    ticket_id: int,
    source_queue_id: int,
    suggested_queue_id: int | None,
    status: str = TRIAGE_STATUS_OPEN,
) -> int:
    async_engine = create_async_engine(_to_async_url(sync_url))
    async with async_engine.connect() as conn:
        # The article the row points at only has to exist for the body guard;
        # the queue half does not read it.
        article_id = (
            await conn.execute(
                text("SELECT MIN(id) FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
            )
        ).scalar() or 0
        await conn.execute(
            text(
                "INSERT INTO tiqora_ai_triage (ticket_id, article_id, source_queue_id, status,"
                " suggested_queue_id, queue_confidence, queue_applied, customer_applied,"
                " create_time, change_time)"
                " VALUES (:tid, :aid, :sq, :st, :tq, 67, 0, 0,"
                " current_timestamp, current_timestamp)"
            ),
            {
                "tid": ticket_id,
                "aid": article_id,
                "sq": source_queue_id,
                "st": status,
                "tq": suggested_queue_id,
            },
        )
        await conn.commit()
        triage_id = (
            await conn.execute(
                text("SELECT id FROM tiqora_ai_triage WHERE ticket_id = :tid"), {"tid": ticket_id}
            )
        ).scalar()
    await async_engine.dispose()
    return int(triage_id or 0)


async def _triage_status(sync_url: str, triage_id: int) -> str | None:
    async_engine = create_async_engine(_to_async_url(sync_url))
    async with async_engine.connect() as conn:
        value = (
            await conn.execute(
                text("SELECT status FROM tiqora_ai_triage WHERE id = :tid"), {"tid": triage_id}
            )
        ).scalar()
    await async_engine.dispose()
    return str(value) if value is not None else None


async def _cleanup_triage(sync_url: str, ticket_id: int) -> None:
    async_engine = create_async_engine(_to_async_url(sync_url))
    async with async_engine.connect() as conn:
        await conn.execute(
            text("DELETE FROM tiqora_ai_triage WHERE ticket_id = :tid"), {"tid": ticket_id}
        )
        await conn.commit()
    await async_engine.dispose()


def _ticket_queue(sync_url: str, ticket_id: int) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        value = conn.execute(
            text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
        ).scalar()
    engine.dispose()
    return int(value or 0)


def _add_group(sync_url: str, *, group_id: int, name: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": group_id, "name": name},
        )
    engine.dispose()


def _add_queue(sync_url: str, *, queue_id: int, group_id: int, name: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": queue_id, "name": name, "gid": group_id},
        )
    engine.dispose()


def _delete_queue(sync_url: str, queue_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ticket_history WHERE queue_id = :qid"), {"qid": queue_id})
        conn.execute(
            text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :qid"),
            {"qid": queue_id},
        )
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
    engine.dispose()


def _delete_group(sync_url: str, group_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM group_user WHERE group_id = :gid"), {"gid": group_id})
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
    engine.dispose()


def _grant_move_into(sync_url: str, *, agent_id: int, group_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'move_into', current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"uid": agent_id, "gid": group_id},
        )
    engine.dispose()


def _set_ticket_queue(sync_url: str, ticket_id: int, queue_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ticket SET queue_id = :qid WHERE id = :tid"),
            {"qid": queue_id, "tid": ticket_id},
        )
    engine.dispose()


async def test_open_triage_is_exposed_on_the_state_route(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=75, monkeypatch=monkeypatch)
    try:
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=seed["queue_id"],
        )

        resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert resp.status_code == 200
        payload = resp.json()["triage"]
        assert payload is not None
        assert payload["id"] == triage_id
        assert payload["queue_confidence"] == 67
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_non_open_triage_is_not_exposed(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``open`` rows are a question for the agent; the rest are history."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=76, monkeypatch=monkeypatch)
    try:
        await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=seed["queue_id"],
            status=TRIAGE_STATUS_REJECTED,
        )

        resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert resp.status_code == 200
        assert resp.json()["triage"] is None
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_reject_marks_the_row_and_hides_it(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=77, monkeypatch=monkeypatch)
    try:
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=seed["queue_id"],
        )

        resp = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/reject",
            json={"note": "gehoert doch hierher"},
        )
        assert resp.status_code == 204
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_REJECTED

        state = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert state.json()["triage"] is None

        # A second reject is a no-op rather than an error.
        again = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/reject"
        )
        assert again.status_code == 204
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_accept_is_idempotent_and_marks_accepted(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suggested queue equals the source queue here, so apply_decision's
    no-op guard fires and nothing moves — what is under test is the status
    transition and that a double click cannot act twice."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=78, monkeypatch=monkeypatch)
    try:
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=seed["queue_id"],
        )

        resp = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept",
            json={"queue": True, "customer": False},
        )
        assert resp.status_code == 204
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_ACCEPTED

        again = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept",
            json={"queue": True, "customer": False},
        )
        assert again.status_code == 204
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_ACCEPTED
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_accept_requires_note_permission(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the worker, the accept path is permission-checked."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=79, monkeypatch=monkeypatch)
    try:
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=seed["queue_id"],
        )
        outsider, outsider_engine = await _client_for(
            mariadb_znuny_url,
            user_id=999998,
            login="agent.aitriage.outsider",
            monkeypatch=monkeypatch,
        )
        try:
            resp = await outsider.post(
                f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept"
            )
            assert resp.status_code == 403
            assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_OPEN
        finally:
            await outsider.aclose()
            await outsider_engine.dispose()
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_unknown_triage_id_is_404(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=80, monkeypatch=monkeypatch)
    try:
        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/987654/accept")
        assert resp.status_code == 404
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


async def test_accept_moves_into_a_queue_the_agent_can_enter(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=81, monkeypatch=monkeypatch)
    target_queue_id = seed["queue_id"] + 500
    try:
        _add_queue(
            mariadb_znuny_url,
            queue_id=target_queue_id,
            group_id=_group_id(seed),
            name=f"AiTriageTarget96{81}",
        )
        _grant_move_into(mariadb_znuny_url, agent_id=seed["agent_id"], group_id=_group_id(seed))
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=target_queue_id,
        )

        resp = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept",
            json={"queue": True, "customer": False},
        )
        assert resp.status_code == 204
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_ACCEPTED
        assert _ticket_queue(mariadb_znuny_url, seed["ticket_id"]) == target_queue_id
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _set_ticket_queue(mariadb_znuny_url, seed["ticket_id"], seed["queue_id"])
        _delete_queue(mariadb_znuny_url, target_queue_id)
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_accept_is_403_without_move_into_on_the_target(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rw`` on the source group implies move_into there; the target must
    live in a group the agent is not in."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=82, monkeypatch=monkeypatch)
    target_queue_id = seed["queue_id"] + 500
    other_group_id = _group_id(seed) + 50
    try:
        _add_group(mariadb_znuny_url, group_id=other_group_id, name=f"AiTriageOtherGrp96{82}")
        _add_queue(
            mariadb_znuny_url,
            queue_id=target_queue_id,
            group_id=other_group_id,
            name=f"AiTriageTarget96{82}",
        )
        _grant_move_into(mariadb_znuny_url, agent_id=seed["agent_id"], group_id=_group_id(seed))
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=target_queue_id,
        )

        resp = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept",
            json={"queue": True, "customer": False},
        )
        assert resp.status_code == 403
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_OPEN
        assert _ticket_queue(mariadb_znuny_url, seed["ticket_id"]) == seed["queue_id"]
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _delete_queue(mariadb_znuny_url, target_queue_id)
        _delete_group(mariadb_znuny_url, other_group_id)
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_accept_does_not_undo_a_later_human_move(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OPEN row can sit for hours; a later move must win."""
    seed, client, client_engine = await _seed(mariadb_znuny_url, ns=83, monkeypatch=monkeypatch)
    suggested_queue_id = seed["queue_id"] + 500
    later_queue_id = seed["queue_id"] + 501
    try:
        _add_queue(
            mariadb_znuny_url,
            queue_id=suggested_queue_id,
            group_id=_group_id(seed),
            name=f"AiTriageSuggested96{83}",
        )
        _add_queue(
            mariadb_znuny_url,
            queue_id=later_queue_id,
            group_id=_group_id(seed),
            name=f"AiTriageLater96{83}",
        )
        _grant_move_into(mariadb_znuny_url, agent_id=seed["agent_id"], group_id=_group_id(seed))
        triage_id = await _insert_triage(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            source_queue_id=seed["queue_id"],
            suggested_queue_id=suggested_queue_id,
        )
        _set_ticket_queue(mariadb_znuny_url, seed["ticket_id"], later_queue_id)

        resp = await client.post(
            f"/api/v1/tickets/{seed['ticket_id']}/ai/triage/{triage_id}/accept",
            json={"queue": True, "customer": False},
        )
        assert resp.status_code == 204
        assert await _triage_status(mariadb_znuny_url, triage_id) == TRIAGE_STATUS_ACCEPTED
        assert _ticket_queue(mariadb_znuny_url, seed["ticket_id"]) == later_queue_id
    finally:
        await _cleanup_triage(mariadb_znuny_url, seed["ticket_id"])
        await client.aclose()
        await client_engine.dispose()
        _set_ticket_queue(mariadb_znuny_url, seed["ticket_id"], seed["queue_id"])
        _delete_queue(mariadb_znuny_url, suggested_queue_id)
        _delete_queue(mariadb_znuny_url, later_queue_id)
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )
