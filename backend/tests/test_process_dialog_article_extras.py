"""Activity dialog article extras: booked time, uploads and the customer value (plan Task 4, D6).

Both dialects. Seed band 892000-892499: agent 892010, customer users 892001/892002, tickets
892040-892043. Znuny references: ``AgentTicketProcess.pm:3067-3082`` (time units field),
``:5069-5081`` (``TimeUnits: 2`` is mandatory), ``:5686-5723`` (article, files, time accounting),
``CustomerTicketProcess.pm:2224`` (files of the customer article).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.portal import process as portal_process
from tiqora.api.v1 import process as agent_process
from tiqora.api.v1.tickets import ArticleAttachmentIn, decode_attachments
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.domain import attachments_in
from tiqora.domain.attachments_in import AttachmentInvalid, AttachmentTooLarge
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.actor import ProcessActor
from tiqora.process.config import ActivityDialogConfig, ProcessConfig, ProcessLayout
from tiqora.process.dialog_form import build_dialog_form
from tiqora.process.engine import MAX_DIALOG_ATTACHMENTS, apply_activity_dialog
from tiqora.process.exceptions import RequiredFieldMissing
from tiqora.process.fields import time_units_mode
from tiqora.process.graph import ActivityDialogNode, ProcessGraph
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

T0 = datetime(2026, 3, 4, 5, 6, 7)
AGENT = 892010
CU_A_ROW, CU_B_ROW = 892001, 892002
CU_A, CU_B = "wf3-cu-892a@example.org", "wf3-cu-892b@example.org"
CO_A, CO_B = "wf3-co-892a", "wf3-co-892b"
T_AGENT, T_CUSTOMER, T_CUSTOMER_TARGET = 892040, 892041, 892042

_CLEANUP = (
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN 892000 AND 892499",
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 892000 AND 892499",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 892000 AND 892499",
    "DELETE FROM article_data_mime_attachment WHERE article_id IN"
    " (SELECT id FROM article WHERE ticket_id BETWEEN 892000 AND 892499)",
    "DELETE FROM article_data_mime WHERE article_id IN"
    " (SELECT id FROM article WHERE ticket_id BETWEEN 892000 AND 892499)",
    "DELETE FROM article WHERE ticket_id BETWEEN 892000 AND 892499",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 892000 AND 892499",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 892000 AND 892499",
    "DELETE FROM ticket WHERE id BETWEEN 892000 AND 892499",
    "DELETE FROM customer_user WHERE id BETWEEN 892001 AND 892009",
    "DELETE FROM users WHERE id BETWEEN 892010 AND 892019",
    "DELETE FROM sysconfig_modified WHERE id BETWEEN 892460 AND 892469",
    "DELETE FROM sysconfig_default WHERE id BETWEEN 892460 AND 892469",
)
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    engine.dispose()


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'wf3.art.agent', 'x', 'Wf3', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": T0},
        )
        for row, login, company in ((CU_A_ROW, CU_A, CO_A), (CU_B_ROW, CU_B, CO_B)):
            conn.execute(
                text(
                    "INSERT INTO customer_user (id, login, email, customer_id, first_name,"
                    " last_name, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, :login, :cust, 'Wf3', 'Cust', 1, :t, 1, :t, 1)"
                ),
                {"id": row, "login": login, "cust": company, "t": T0},
            )
        for tid, customer, user in (
            (T_AGENT, None, None),
            (T_CUSTOMER, CO_A, CU_A),
            (T_CUSTOMER_TARGET, None, None),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'wf3 article extras', 1, 1, 1, 1, 1, 3, 4, :c, :u,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026110{tid}", "c": customer, "u": user, "t": T0},
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run(url, _CLEANUP)
    _URLS.clear()
    clear_revision_cache()


def _set_account_time(conn: Any, value: str) -> None:
    """One effective ``Ticket::Frontend::AccountTime`` value. ``tests/_sysconfig_rows`` binds
    bytes, which PostgreSQL stores as the text ``\\x2730...``; this binds the decoded YAML."""
    effective = yaml_encode_effective(value).decode()
    for table in ("sysconfig_modified", "sysconfig_default"):
        conn.execute(text(f"DELETE FROM {table} WHERE id = 892460"))
    conn.execute(
        text(
            "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
            " is_readonly, is_required, is_valid, has_configlevel, user_modification_possible,"
            " user_modification_active, xml_content_raw, xml_content_parsed, xml_filename,"
            " effective_value, is_dirty, exclusive_lock_guid, create_time, create_by,"
            " change_time, change_by) VALUES (892460, 'Ticket::Frontend::AccountTime', 'x', 'x',"
            " 0, 0, 1, 1, 0, 0, 0, 'x', 'x', 'Ticket.xml', :e, 0, '0', current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"e": effective},
    )
    conn.execute(
        text(
            "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id, is_valid,"
            " user_modification_active, effective_value, is_dirty, reset_to_default, create_time,"
            " create_by, change_time, change_by) VALUES (892460, 892460,"
            " 'Ticket::Frontend::AccountTime', NULL, 1, 0, :e, 0, 0, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"e": effective},
    )


def _dialog(
    fields: dict[str, dict[str, Any]], interfaces: tuple[str, ...] = ("AgentInterface",)
) -> ActivityDialogConfig:
    return ActivityDialogConfig.from_dict(
        {"FieldOrder": list(fields), "Fields": fields, "Interface": list(interfaces)}
    )


def _article(time_units: str | None = None, **extra: Any) -> dict[str, Any]:
    config = {"CommunicationChannel": "Internal", **extra}
    if time_units is not None:
        config["TimeUnits"] = time_units
    return {"Display": "1", "Config": config}


def _file(name: str, content: bytes) -> dict[str, str]:
    return {
        "filename": name,
        "content_type": "text/plain",
        "content_base64": base64.b64encode(content).decode(),
    }


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    settings: dict[str, Any]
    sync_url: str

    @property
    def sysconfig(self) -> SysConfig:
        async def _fetch(name: str) -> Any:
            return self.settings.get(name)

        return SysConfig(fetch=_fetch)

    async def apply(
        self,
        dialog: ActivityDialogConfig,
        values: dict[str, Any],
        *,
        ticket: int = T_AGENT,
        actor: ProcessActor | None = None,
    ) -> None:
        async with self.factory() as session, session.begin():
            await apply_activity_dialog(
                session,
                ticket_id=ticket,
                dialog=dialog,
                field_values=values,
                actor=actor or ProcessActor.agent(AGENT),
                sysconfig=self.sysconfig,
            )

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.factory() as session:
            return list((await session.execute(text(sql), params)).all())

    async def articles(self, ticket: int) -> list[Any]:
        return await self.rows(
            "SELECT id, article_sender_type_id FROM article WHERE ticket_id = :t ORDER BY id",
            t=ticket,
        )

    async def files(self, ticket: int) -> list[tuple[str, int]]:
        rows = await self.rows(
            "SELECT m.filename, m.content_size FROM article_data_mime_attachment m"
            " JOIN article a ON a.id = m.article_id WHERE a.ticket_id = :t ORDER BY m.filename",
            t=ticket,
        )
        return [(str(r[0]), int(r[1])) for r in rows]

    async def time_rows(self, ticket: int) -> list[tuple[int | None, Decimal]]:
        rows = await self.rows(
            "SELECT article_id, time_unit FROM time_accounting WHERE ticket_id = :t ORDER BY id",
            t=ticket,
        )
        return [(r[0], Decimal(str(r[1]))) for r in rows]

    async def customer_of(self, ticket: int) -> tuple[str | None, str | None]:
        row = (
            await self.rows(
                "SELECT customer_id, customer_user_id FROM ticket WHERE id = :t", t=ticket
            )
        )[0]
        return row[0], row[1]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory, {}, sync_url)
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


# ---------------------------------------------------------------------------
# Time units
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_time_units_display_matrix(env: Env) -> None:
    agent, customer = "AgentInterface", "CustomerInterface"
    both = ("AgentInterface", "CustomerInterface")
    for config, interface, account_time, expected in (
        ("0", agent, True, "0"),  # 0: not in the form
        (None, agent, True, "0"),
        ("1", agent, True, "1"),  # 1: optional
        ("2", agent, True, "2"),  # 2: required
        (2, agent, True, "2"),  # YAML may hand over a number
        ("2", agent, False, "0"),  # AccountTime off: whatever the config says
        ("1", customer, True, "0"),  # CustomerTicketProcess has no time units
    ):
        dialog = _dialog({"Article": _article(config)}, both)
        assert time_units_mode(dialog, interface, account_time=account_time) == expected, (
            config,
            interface,
            account_time,
        )

    # The form carries the mode and the upload flag on the article field only.
    graph = ProcessGraph(
        id=1,
        entity_id="Process-wf3892",
        name="wf3",
        state_entity_id="S1",
        config=ProcessConfig.from_dict({}),
        layout=ProcessLayout(positions={}),
    )
    user = AuthenticatedUser(
        id=AGENT, login="wf3.art.agent", first_name="Wf3", last_name="Agent", auth_method="session"
    )
    for config, account_time, expected in (("2", "1", "2"), ("1", "1", "1"), ("2", "0", "0")):
        sync_engine = create_engine(env.sync_url)
        with sync_engine.begin() as conn:
            _set_account_time(conn, account_time)
        sync_engine.dispose()
        clear_revision_cache()
        node = ActivityDialogNode(
            id=1,
            entity_id="ActivityDialog-wf3892",
            name="wf3",
            config=_dialog({"Article": _article(config)}),
        )
        async with env.factory() as session:
            form = await build_dialog_form(
                session,
                actor=ProcessActor.agent(AGENT),
                user=user,
                graph=graph,
                dialog=node,
                ticket_id=T_AGENT,
            )
        (field,) = form.fields
        assert (field.kind, field.time_units, field.uploads) == ("article", expected, True)


@pytest.mark.db
async def test_time_units_required_when_two_and_ignored_when_zero(env: Env) -> None:
    required = _dialog({"Article": _article("2")})
    body = {"Subject": "Hello", "Body": "World"}
    with pytest.raises(RequiredFieldMissing, match="TimeUnits"):
        await env.apply(required, body)
    with pytest.raises(RequiredFieldMissing, match="TimeUnits"):
        await env.apply(required, {**body, "TimeUnits": "0"})  # Perl-false
    assert await env.articles(T_AGENT) == []

    await env.apply(required, {**body, "TimeUnits": "3"})
    assert [t for _, t in await env.time_rows(T_AGENT)] == [Decimal("3")]

    # TimeUnits: 0 hides the field, so a submitted value is dropped; AccountTime off does too.
    await env.apply(_dialog({"Article": _article("0")}), {**body, "TimeUnits": "9"})
    env.settings["Ticket::Frontend::AccountTime"] = "0"
    await env.apply(required, {**body, "TimeUnits": "9"})
    assert [t for _, t in await env.time_rows(T_AGENT)] == [Decimal("3")]
    assert len(await env.articles(T_AGENT)) == 3


@pytest.mark.db
async def test_time_units_booked_with_article(env: Env) -> None:
    dialog = _dialog({"Article": _article("1")})
    await env.apply(dialog, {"Subject": "Work", "Body": "Done", "TimeUnits": "1,257"})
    (article_id,) = [int(r[0]) for r in await env.articles(T_AGENT)]
    # TicketAccountTime input handling: comma to dot, cut (not rounded) after two decimals.
    assert await env.time_rows(T_AGENT) == [(article_id, Decimal("1.25"))]
    history = await env.rows(
        "SELECT name FROM ticket_history WHERE ticket_id = :t AND article_id = :a AND name LIKE :p",
        t=T_AGENT,
        a=article_id,
        p="%%1.25%%",
    )
    assert history, "TimeAccounting history row"

    # Optional and empty: nothing booked. Time without an article is not booked either.
    await env.apply(dialog, {"Subject": "More", "Body": "Work", "TimeUnits": ""})
    await env.apply(dialog, {"TimeUnits": "5"})
    assert len(await env.time_rows(T_AGENT)) == 1
    nested = {"Article": {"Subject": "Nested", "Body": "Shape", "TimeUnits": 2}}
    await env.apply(dialog, nested)
    assert [t for _, t in await env.time_rows(T_AGENT)] == [Decimal("1.25"), Decimal("2")]


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_uploads_attached_to_dialog_article(env: Env) -> None:
    dialog = _dialog({"Article": _article()})
    files = [_file("a.txt", b"alpha"), _file("b.txt", b"bravo-bravo")]
    await env.apply(dialog, {"Subject": "Files", "Body": "Attached", "ArticleAttachments": files})
    assert len(await env.articles(T_AGENT)) == 1
    assert await env.files(T_AGENT) == [("a.txt", 5), ("b.txt", 11)]


@pytest.mark.db
async def test_uploads_without_article_are_ignored(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = _dialog({"Article": _article()})
    with caplog.at_level(logging.WARNING, logger="tiqora.process.engine"):
        await env.apply(dialog, {"Subject": "No body", "ArticleAttachments": [_file("x", b"1")]})
    assert await env.articles(T_AGENT) == []
    assert await env.files(T_AGENT) == []
    assert any("uploads ignored" in r.getMessage() for r in caplog.records)
    # A dialog without a visible article never even sees the key.
    plain = _dialog({"Title": {"Display": "1"}})
    await env.apply(plain, {"Subject": "S", "Body": "B", "ArticleAttachments": [_file("x", b"1")]})
    assert await env.articles(T_AGENT) == []


@pytest.mark.db
async def test_upload_limit_413(env: Env) -> None:
    dialog = _dialog({"Article": _article()})
    big = _file("big.bin", bytes(attachments_in.MAX_ATTACHMENT_BYTES + 1))
    with pytest.raises(AttachmentTooLarge):
        await env.apply(dialog, {"Subject": "S", "Body": "B", "ArticleAttachments": [big]})
    with pytest.raises(AttachmentInvalid):
        await env.apply(
            dialog,
            {"Subject": "S", "Body": "B", "ArticleAttachments": [_file("x", b"1")] * 11},
        )
    with pytest.raises(AttachmentInvalid):
        bad = {"filename": "x", "content_type": "t/p", "content_base64": "***"}
        await env.apply(dialog, {"Subject": "S", "Body": "B", "ArticleAttachments": [bad]})
    with pytest.raises(AttachmentInvalid):
        await env.apply(dialog, {"Subject": "S", "Body": "B", "ArticleAttachments": "nope"})
    assert MAX_DIALOG_ATTACHMENTS == 10
    assert await env.articles(T_AGENT) == []
    assert await env.files(T_AGENT) == []


def test_routes_map_attachment_errors_to_413_and_422() -> None:
    for module in (agent_process, portal_process):
        too_large = module._map_exc(AttachmentTooLarge("too big"))
        invalid = module._map_exc(AttachmentInvalid("not base64"))
        assert (too_large.status_code, invalid.status_code) == (413, 422)
        assert AttachmentTooLarge in module_mapped(module)
        assert AttachmentInvalid in module_mapped(module)


def module_mapped(module: Any) -> tuple[type[Exception], ...]:
    return tuple(getattr(module, "_MAPPED_ERRORS", None) or module._MAPPED)


def test_reply_dialog_decoder_keeps_its_http_status() -> None:
    ok = ArticleAttachmentIn(filename="a", content_base64=base64.b64encode(b"abc").decode())
    assert decode_attachments([ok]) == [("a", "application/octet-stream", b"abc")]
    with pytest.raises(HTTPException) as bad:
        decode_attachments([ArticleAttachmentIn(filename="a", content_base64="***")])
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException) as big:
        decode_attachments(
            [
                ArticleAttachmentIn(
                    filename="a",
                    content_base64=base64.b64encode(
                        bytes(attachments_in.MAX_ATTACHMENT_BYTES + 1)
                    ).decode(),
                )
            ]
        )
    assert big.value.status_code == 413


# ---------------------------------------------------------------------------
# Customer value
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_customer_id_derived_from_customer_user_when_empty(env: Env) -> None:
    dialog = _dialog({"CustomerUserID": {"Display": "1"}, "CustomerID": {"Display": "1"}})
    await env.apply(dialog, {"CustomerUserID": CU_A, "CustomerID": ""})
    assert await env.customer_of(T_AGENT) == (CO_A, CU_A)

    # A company typed by the agent wins; an unrelated customer user keeps its own company.
    await env.apply(dialog, {"CustomerUserID": CU_B, "CustomerID": "wf3-typed"})
    assert await env.customer_of(T_AGENT) == ("wf3-typed", CU_B)
    await env.apply(dialog, {"CustomerUserID": CU_B, "CustomerID": " "})
    assert await env.customer_of(T_AGENT) == (CO_B, CU_B)


@pytest.mark.db
async def test_portal_forged_customer_and_attachments_ignored(env: Env) -> None:
    dialog = _dialog(
        {
            "CustomerUserID": {"Display": "1"},
            "CustomerID": {"Display": "1"},
            "Attachments": {"Display": "1"},
            "Article": _article("2"),
        },
        ("CustomerInterface",),
    )
    actor = ProcessActor.customer(login=CU_A, customer_id=CO_A)
    await env.apply(
        dialog,
        {
            "CustomerUserID": CU_B,
            "CustomerID": CO_B,
            "Attachments": "ObjectType=Article;ObjectID=1;ID=1",
            "Subject": "Hello",
            "Body": "From the portal",
            "TimeUnits": "7",
            "ArticleAttachments": [_file("mine.txt", b"own file")],
        },
        ticket=T_CUSTOMER,
        actor=actor,
    )
    assert await env.customer_of(T_CUSTOMER) == (CO_A, CU_A)
    # The customer's own upload belongs to the article; time accounting does not exist there.
    assert await env.files(T_CUSTOMER) == [("mine.txt", 8)]
    assert await env.time_rows(T_CUSTOMER) == []
    forged_df = await env.rows(
        "SELECT v.id FROM dynamic_field_value v JOIN dynamic_field f ON f.id = v.field_id"
        " WHERE f.name = 'ProcessManagementAttachment' AND v.object_id = :t",
        t=T_CUSTOMER,
    )
    assert forged_df == []
