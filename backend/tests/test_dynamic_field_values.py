"""Znuny-identical dynamic-field writes (typed columns, no-op, change_time) + PATCH.

Seed ids use the 925xx band (users 9250x, group 92510, queue 92520, ticket
92530, dynamic fields 9254x).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.dynamic_field_values import (
    DynamicFieldValueError,
    load_ticket_dynamic_field,
    normalize_df_values,
    readable_history_value,
    set_ticket_dynamic_field,
    truncate_history_parts,
)

# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ftype", "raw", "expected"),
    [
        ("Date", ["2026-02-01"], ["2026-02-01 00:00:00"]),
        ("Date", ["2026-02-01 00:00:00"], ["2026-02-01 00:00:00"]),
        ("DateTime", ["2026-02-01T10:30"], ["2026-02-01 10:30:00"]),
        ("DateTime", ["2026-02-01 10:30:15"], ["2026-02-01 10:30:15"]),
        ("Checkbox", ["1"], ["1"]),
        ("Checkbox", [""], ["0"]),
        ("Checkbox", [], []),
        ("Multiselect", ["a", "", "b"], ["a", "b"]),
        ("Text", [""], []),
        # Process-management fields etc. keep working as free text.
        ("ProcessID", ["P1"], ["P1"]),
    ],
)
def test_normalize_ok(ftype: str, raw: list[str], expected: list[str]) -> None:
    assert normalize_df_values(ftype, raw) == expected


@pytest.mark.parametrize(
    ("ftype", "raw"),
    [
        ("Date", ["2026-02-01 10:00:00"]),
        ("Date", ["01.02.2026"]),
        ("Date", ["2026-13-45"]),
        ("Checkbox", ["yes"]),
        ("Dropdown", ["a", "b"]),
        ("DateTime", ["morgen"]),
        ("DateTime", ["2026-02-01"]),
    ],
)
def test_normalize_rejects(ftype: str, raw: list[str]) -> None:
    with pytest.raises(DynamicFieldValueError):
        normalize_df_values(ftype, raw)


def test_readable_history_value() -> None:
    assert readable_history_value("Date", ["2026-02-01 00:00:00"]) == "2026-02-01"
    assert readable_history_value("DateTime", ["2026-02-01 10:30:00"]) == "2026-02-01 10:30:00"
    assert readable_history_value("Multiselect", ["a", "b"]) == "a, b"
    assert readable_history_value("Text", []) == ""


# Expected values computed by hand from ObjectType/Ticket.pm PostValueSet (NoCharacters = 166):
@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("F" * 20, "v" * 200, "o" * 80), ("F" * 20, "v" * 86 + "[...]", "o" * 50 + "[...]")),
        (("F" * 100, "v" * 100, ""), ("F" * 78 + "[...]", "v" * 78 + "[...]", "")),
        (("F" * 170, "v", ""), ("F" * 160 + "[...]", "v", "")),
        (("Feld", "neu", "alt"), ("Feld", "neu", "alt")),
    ],
)
def test_truncate_history_parts_matches_perl(
    parts: tuple[str, str, str], expected: tuple[str, str, str]
) -> None:
    assert truncate_history_parts(*parts) == expected


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

NOW = datetime(2026, 2, 1, 12, 0, 0)
AGENT_RW = 92500
AGENT_RO = 92501
GROUP_ID = 92510
QUEUE_ID = 92520
TICKET_ID = 92530
DFS = {
    "WaveDue": (92540, "Date"),
    "WaveWhen": (92541, "DateTime"),
    "WaveFlag": (92542, "Checkbox"),
    "WaveDrop": (92543, "Dropdown"),
    "WaveMulti": (92544, "Multiselect"),
    "WaveText": (92545, "Text"),
}
DF_IDS = tuple(i for i, _ in DFS.values())


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup(conn: Any) -> None:
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM dynamic_field_value WHERE object_id = {TICKET_ID}",
        f"DELETE FROM dynamic_field WHERE id IN ({', '.join(map(str, DF_IDS))})",
        f"DELETE FROM ticket WHERE id = {TICKET_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id IN ({AGENT_RW}, {AGENT_RO})",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        for uid, login in ((AGENT_RW, "dfval.rw"), (AGENT_RO, "dfval.ro")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:i, :l, 'x', 'Df', 'Val', 1, :t, 1, :t, 1)"
                ),
                {"i": uid, "l": login, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'dfval-grp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP_ID, "t": NOW},
        )
        for uid, keys in ((AGENT_RW, ("ro", "rw")), (AGENT_RO, ("ro",))):
            for k in keys:
                conn.execute(
                    text(
                        "INSERT INTO group_user (user_id, group_id, permission_key,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                    ),
                    {"u": uid, "g": GROUP_ID, "k": k, "t": NOW},
                )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'DfValQueue', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_ID, "g": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20260201925301', 'DF ticket', :q, 1, 1, :rw, 1, 3, 4,"
                " 'CUST1', 'alice@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "q": QUEUE_ID, "rw": AGENT_RW, "t": NOW},
        )
        for name, (fid, ftype) in DFS.items():
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :o, :ft, 'Ticket', '---', 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "n": name, "o": fid, "ft": ftype, "t": NOW},
            )
    engine.dispose()


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        rows = result.fetchall() if result.returns_rows else []
    engine.dispose()
    return rows


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    try:
        yield sync_url, factory
    finally:
        await engine.dispose()
        e = create_engine(sync_url)
        with e.begin() as conn:
            _cleanup(conn)
        e.dispose()


async def _set(
    factory: async_sessionmaker[AsyncSession], name: str, values: list[str], user_id: int = 1
) -> bool:
    async with factory() as session, session.begin():
        field = await load_ticket_dynamic_field(session, name)
        assert field is not None
        return await set_ticket_dynamic_field(
            session, ticket_id=TICKET_ID, field=field, values=values, user_id=user_id
        )


def _rows(url: str, name: str) -> list[Any]:
    return _query(
        url,
        "SELECT value_text, value_date, value_int FROM dynamic_field_value"
        " WHERE field_id = :f AND object_id = :o ORDER BY id",
        f=DFS[name][0],
        o=TICKET_ID,
    )


def _history(url: str) -> list[str]:
    return [
        str(r[0])
        for r in _query(
            url,
            "SELECT h.name FROM ticket_history h"
            " JOIN ticket_history_type t ON t.id = h.history_type_id"
            " WHERE h.ticket_id = :t AND t.name = 'TicketDynamicFieldUpdate' ORDER BY h.id",
            t=TICKET_ID,
        )
    ]


def _change_time(url: str) -> datetime:
    return _query(url, "SELECT change_time FROM ticket WHERE id = :t", t=TICKET_ID)[0][0]


@pytest.mark.db
async def test_date_is_written_to_value_date(env: Any) -> None:
    url, factory = env
    assert await _set(factory, "WaveDue", ["2026-02-01"]) is True
    assert _rows(url, "WaveDue") == [(None, datetime(2026, 2, 1), None)]


@pytest.mark.db
async def test_datetime_is_written_to_value_date(env: Any) -> None:
    url, factory = env
    await _set(factory, "WaveWhen", ["2026-02-01 10:30:00"])
    assert _rows(url, "WaveWhen") == [(None, datetime(2026, 2, 1, 10, 30), None)]
    assert _history(url) == ["%%FieldName%%WaveWhen%%Value%%2026-02-01 10:30:00%%OldValue%%"]


@pytest.mark.db
async def test_checkbox_is_written_to_value_int(env: Any) -> None:
    url, factory = env
    await _set(factory, "WaveFlag", ["0"])
    assert _rows(url, "WaveFlag") == [(None, None, 0)]
    await _set(factory, "WaveFlag", ["1"])
    assert _rows(url, "WaveFlag") == [(None, None, 1)]


@pytest.mark.db
async def test_text_and_multiselect_use_value_text(env: Any) -> None:
    url, factory = env
    await _set(factory, "WaveMulti", ["a", "b"])
    assert _rows(url, "WaveMulti") == [("a", None, None), ("b", None, None)]


@pytest.mark.db
async def test_unchanged_value_writes_nothing(env: Any) -> None:
    url, factory = env
    assert await _set(factory, "WaveText", ["a"]) is True
    hist = _history(url)
    _query(url, "UPDATE ticket SET change_time = :n WHERE id = :t", n=NOW, t=TICKET_ID)
    assert await _set(factory, "WaveText", ["a"]) is False
    assert await _set(factory, "WaveDue", []) is False  # empty -> empty
    assert _history(url) == hist
    assert _change_time(url) == NOW


@pytest.mark.db
async def test_change_bumps_change_time_and_writes_history(env: Any) -> None:
    url, factory = env
    before = _change_time(url)
    await _set(factory, "WaveDue", ["2026-02-01"], user_id=AGENT_RW)
    assert _history(url) == ["%%FieldName%%WaveDue%%Value%%2026-02-01%%OldValue%%"]
    assert _change_time(url) > before
    row = _query(url, "SELECT change_by FROM ticket WHERE id = :t", t=TICKET_ID)
    assert row[0][0] == AGENT_RW
    await _set(factory, "WaveDue", ["2026-03-04"])
    assert _history(url)[-1] == ("%%FieldName%%WaveDue%%Value%%2026-03-04%%OldValue%%2026-02-01")


@pytest.mark.db
async def test_empty_value_deletes_rows(env: Any) -> None:
    url, factory = env
    await _set(factory, "WaveText", ["a"])
    assert await _set(factory, "WaveText", []) is True
    assert _rows(url, "WaveText") == []
    assert _history(url)[-1] == "%%FieldName%%WaveText%%Value%%%%OldValue%%a"


@pytest.mark.db
async def test_invalid_value_raises_and_writes_nothing(env: Any) -> None:
    url, factory = env
    with pytest.raises(DynamicFieldValueError):
        await _set(factory, "WaveDue", ["2026-02-01 10:00:00"])
    assert _rows(url, "WaveDue") == []
    assert _history(url) == []


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    user = AuthenticatedUser(
        id=user_id,
        login=f"agent{user_id}",
        first_name="Df",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.db
async def test_patch_dynamic_fields_writes_several(env: Any) -> None:
    url, factory = env
    async with _client(factory, AGENT_RW) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}",
            json={"dynamic_fields": {"WaveText": ["neu"], "WaveDue": ["2026-02-01"]}},
        )
    assert resp.status_code == 204, resp.text
    assert _rows(url, "WaveText") == [("neu", None, None)]
    assert _rows(url, "WaveDue") == [(None, datetime(2026, 2, 1), None)]


@pytest.mark.db
async def test_patch_dynamic_fields_atomic_422(env: Any) -> None:
    url, factory = env
    async with _client(factory, AGENT_RW) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}",
            json={"dynamic_fields": {"WaveText": ["neu"], "WaveDue": ["2026-02-01 10:00:00"]}},
        )
    assert resp.status_code == 422, resp.text
    assert _rows(url, "WaveText") == []
    assert _history(url) == []


@pytest.mark.db
async def test_patch_dynamic_fields_needs_rw(env: Any) -> None:
    url, factory = env
    async with _client(factory, AGENT_RO) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}", json={"dynamic_fields": {"WaveText": ["neu"]}}
        )
    assert resp.status_code == 403, resp.text
    assert _rows(url, "WaveText") == []


@pytest.mark.db
async def test_patch_single_field_name_with_bad_value_is_422(env: Any) -> None:
    url, factory = env
    async with _client(factory, AGENT_RW) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}",
            json={"field_name": "WaveFlag", "field_values": ["yes"]},
        )
    assert resp.status_code == 422, resp.text
    assert _rows(url, "WaveFlag") == []


def test_process_surfaces_map_invalid_input_to_400() -> None:
    """Process engine writes DFs from user input; both surfaces must map the
    resulting InvalidInput (never a 500)."""
    from tiqora.api.portal import process as portal_process
    from tiqora.api.v1 import process as v1_process
    from tiqora.domain.ticket_write_service import InvalidInput

    for mod, mapper, tuple_name in (
        (v1_process, v1_process._map_exc, "_MAPPED_ERRORS"),
        (portal_process, portal_process._map_exc, "_MAPPED"),
    ):
        assert mapper(InvalidInput("x")).status_code == 400
        assert InvalidInput in getattr(mod, tuple_name)


@pytest.mark.db
async def test_checkbox_empty_value_is_stored_as_zero(env: Any) -> None:
    url, factory = env
    assert await _set(factory, "WaveFlag", [""]) is True
    assert _rows(url, "WaveFlag") == [(None, None, 0)]
    assert _history(url) == ["%%FieldName%%WaveFlag%%Value%%0%%OldValue%%"]
    assert await _set(factory, "WaveFlag", []) is True
    assert _rows(url, "WaveFlag") == []


@pytest.mark.db
async def test_znuny_format_readers_return_space_separated_dates(env: Any) -> None:
    """Compat GI and process conditions read value_date as "YYYY-MM-DD HH:MM:SS"."""
    from tiqora.api.compat.operations import _load_dynamic_fields_gi
    from tiqora.process.engine import _evaluate_field, get_ticket_attrs

    url, factory = env
    await _set(factory, "WaveDue", ["2026-02-01"])
    await _set(factory, "WaveWhen", ["2026-02-01 10:30:00"])
    async with factory() as session:
        gi = {d["Name"]: d["Value"] for d in await _load_dynamic_fields_gi(session, TICKET_ID)}
        attrs = await get_ticket_attrs(session, TICKET_ID)
    assert gi["WaveDue"] == "2026-02-01 00:00:00"
    assert gi["WaveWhen"] == "2026-02-01 10:30:00"
    assert attrs["DynamicField_WaveDue"] == "2026-02-01 00:00:00"
    assert _evaluate_field("2026-02-01 00:00:00", "String", attrs["DynamicField_WaveDue"])
    assert _evaluate_field("2026-01-31 00:00:00", "GreaterThan", attrs["DynamicField_WaveDue"])
