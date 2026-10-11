"""Znuny's dialog field ``Attachments`` and attachment reuse in the mail / article actions.

Seed ids live in the 892500-892999 band. Znuny: ``AgentTicketProcess.pm:1878, 4447ff, 5513-5545,
6750-6810`` (the field), ``TransitionAction/Base.pm:145-243``, ``ArticleSend.pm:163-200`` and
``TicketArticleCreate.pm:297-330`` (the actions).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.actor import ProcessActor
from tiqora.process.attachments_field import (
    collect_reused_attachments,
    collect_std_attachments,
    decode_attachment_selection,
    encode_attachment_selection,
    format_attachment_label,
    list_ticket_attachment_options,
)
from tiqora.process.config import (
    ActivityDialogConfig,
    ProcessConfig,
    ProcessLayout,
    TransitionActionConfig,
)
from tiqora.process.dialog_form import build_dialog_form
from tiqora.process.engine import apply_activity_dialog, execute_transition_action
from tiqora.process.graph import ActivityDialogNode, ProcessGraph
from tiqora.storage.backend import DbMimeStorage
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)
MODULE_PREFIX = "Kernel::System::ProcessManagement::TransitionAction::"
AGENT = 892510
T_A, T_B = 892520, 892521
A1, A2, B1, B2 = 892530, 892531, 892540, 892541
STD_A, STD_B, STD_INVALID = 892560, 892561, 892562
DF_NAME = "ProcessManagementAttachment"

_CLEANUP = (
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 892500 AND 892999",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 892500 AND 892999",
    "DELETE FROM article_data_mime_attachment WHERE article_id IN"
    " (SELECT id FROM article WHERE ticket_id BETWEEN 892500 AND 892999)",
    "DELETE FROM article_data_mime WHERE article_id IN"
    " (SELECT id FROM article WHERE ticket_id BETWEEN 892500 AND 892999)",
    "DELETE FROM article WHERE ticket_id BETWEEN 892500 AND 892999",
    "DELETE FROM tiqora_mail_log WHERE ticket_id BETWEEN 892500 AND 892999",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 892500 AND 892999",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 892500 AND 892999",
    "DELETE FROM ticket WHERE id BETWEEN 892500 AND 892999",
    "DELETE FROM standard_attachment WHERE id BETWEEN 892560 AND 892569",
    "DELETE FROM users WHERE id BETWEEN 892510 AND 892519",
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


# (article id, ticket, [(filename, content type, disposition, bytes)] in INSERT order)
_ARTICLES: tuple[tuple[int, int, list[tuple[str, str, str, bytes]]], ...] = (
    (
        A1,
        T_A,
        [
            ("b.pdf", "application/pdf", "attachment", b"bee-bytes"),  # lower id, later name
            ("a.pdf", "application/pdf", "attachment", b"aye-bytes"),
            ("file-1", "text/plain; charset=utf-8", "inline", b"plain body"),
        ],
    ),
    (A2, T_A, [("offer.pdf", "application/pdf", "attachment", b"offer-bytes")]),
    (B1, T_B, [("secret.pdf", "application/pdf", "attachment", b"secret-bytes")]),
    (B2, T_B, [("smime.p7m", "application/pkcs7-mime", "attachment", b"enc")]),
)


def _df_id(conn: Any) -> int:
    """The initial insert ships ``ProcessManagementAttachment`` (Znuny's and Tiqora's schema)."""
    found = conn.execute(
        text("SELECT id FROM dynamic_field WHERE name = :n"), {"n": DF_NAME}
    ).scalar()
    assert found is not None, "ProcessManagementAttachment is part of the initial insert"
    return int(found)


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'wf3.att.agent', 'x', 'Wf3', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": T0},
        )
        for tid in (T_A, T_B):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'wf3 attachments', 1, 1, 1, 1, 1, 3, 4, NULL, NULL,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026110{tid}", "t": T0},
            )
        sender = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = 'agent' LIMIT 1")
        ).scalar()
        channel = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        ).scalar()
        for article_id, ticket_id, files in _ARTICLES:
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :t, :s, :c, 0, 0, :now, 1, :now, 1)"
                ),
                {"id": article_id, "t": ticket_id, "s": sender, "c": channel, "now": T0},
            )
            for name, ctype, disposition, content in files:
                conn.execute(
                    text(
                        "INSERT INTO article_data_mime_attachment (article_id, filename,"
                        " content_size, content_type, disposition, content, create_time,"
                        " create_by, change_time, change_by)"
                        " VALUES (:a, :fn, :sz, :ct, :d, :c, :now, 1, :now, 1)"
                    ),
                    {
                        "a": article_id,
                        "fn": name,
                        "sz": str(len(content)),
                        "ct": ctype,
                        "d": disposition,
                        "c": content,
                        "now": T0,
                    },
                )
        for sid, name, content, valid in (
            (STD_A, "wf3-std-a", b"std-a-bytes", 1),
            (STD_B, "wf3-std-b", b"std-b-bytes", 1),
            (STD_INVALID, "wf3-std-invalid", b"std-x-bytes", 2),
        ):
            conn.execute(
                text(
                    "INSERT INTO standard_attachment (id, name, content_type, content, filename,"
                    " valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 'text/plain', :c, :fn, :v, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": name, "c": content, "fn": f"{name}.txt", "v": valid, "t": T0},
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run(url, _CLEANUP)
    _URLS.clear()
    clear_revision_cache()


def _dialog(
    fields: dict[str, dict[str, Any]], interfaces: tuple[str, ...] = ("AgentInterface",)
) -> ActivityDialogConfig:
    return ActivityDialogConfig.from_dict(
        {"FieldOrder": list(fields), "Fields": fields, "Interface": list(interfaces)}
    )


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    settings: dict[str, Any]
    sync_url: str
    df_id: int

    @property
    def sysconfig(self) -> SysConfig:
        async def _fetch(name: str) -> Any:
            return self.settings.get(name)

        return SysConfig(fetch=_fetch)

    async def keys(self, ticket: int = T_A) -> dict[str, str]:
        """``{filename: option key}`` of the ticket's selectable attachments."""
        async with self.factory() as session:
            options = await list_ticket_attachment_options(session, ticket, self.sysconfig)
        return {o.label.split(": ", 1)[1]: o.value for o in options}

    async def apply(
        self,
        dialog: ActivityDialogConfig,
        values: dict[str, Any],
        *,
        ticket: int = T_A,
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

    async def run(self, module: str, config: dict[str, Any], *, ticket: int = T_A) -> None:
        async with self.factory() as session, session.begin():
            await execute_transition_action(
                session,
                action=TransitionActionConfig(module=MODULE_PREFIX + module, config=config),
                ticket_id=ticket,
                process_entity_id="P",
                activity_entity_id="A",
                transition_entity_id="T",
                user_id=AGENT,
                sysconfig=self.sysconfig,
            )

    async def df_value(self, ticket: int = T_A) -> str:
        async with self.factory() as session:
            value = (
                await session.execute(
                    text(
                        "SELECT value_text FROM dynamic_field_value"
                        " WHERE field_id = :f AND object_id = :o"
                    ),
                    {"f": self.df_id, "o": ticket},
                )
            ).scalar_one_or_none()
        return value or ""

    def set_df_value(self, ticket: int, value: str) -> None:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " VALUES (:f, :o, :v)"
                ),
                {"f": self.df_id, "o": ticket, "v": value},
            )
        engine.dispose()

    async def new_article_files(self, ticket: int = T_A) -> list[tuple[str, bytes]]:
        """Attachments of the newest article of *ticket* (not a seed article), via the storage."""
        async with self.factory() as session:
            newest = (
                await session.execute(
                    text(
                        "SELECT MAX(id) FROM article WHERE ticket_id = :t"
                        " AND id NOT IN (:a1, :a2, :b1, :b2)"
                    ),
                    {"t": ticket, "a1": A1, "a2": A2, "b1": B1, "b2": B2},
                )
            ).scalar_one()
            assert newest is not None, "no new article was stored"
            storage = DbMimeStorage(session)
            out: list[tuple[str, bytes]] = []
            for meta in await storage.list_attachments(int(newest)):
                att = await storage.get_attachment(meta.id)
                assert att is not None
                out.append((meta.filename or "", att.content))
        return out


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)
    sync_engine = create_engine(sync_url)
    with sync_engine.begin() as conn:
        df_id = _df_id(conn)
    sync_engine.dispose()
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory, {}, sync_url, df_id)
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


# ---------------------------------------------------------------------------
# Options and keys
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_option_keys_and_labels_like_znuny(env: Env) -> None:
    async with env.factory() as session:
        options = await list_ticket_attachment_options(session, T_A, env.sysconfig)
    # FileID is the position in the index sorted by filename, not the row id: b.pdf was stored
    # first but is file 2. The plain-text body (file-1) is not offered (ExcludePlainText).
    assert [(o.value, o.label) for o in options] == [
        (f"ObjectType=Article;ObjectID={A1};ID=1", "Artikel#1: a.pdf"),
        (f"ObjectType=Article;ObjectID={A1};ID=2", "Artikel#1: b.pdf"),
        (f"ObjectType=Article;ObjectID={A2};ID=1", "Artikel#2: offer.pdf"),
    ]
    assert decode_attachment_selection(options[1].value + "," + options[2].value) == [
        {"ObjectType": "Article", "ObjectID": str(A1), "ID": "2"},
        {"ObjectType": "Article", "ObjectID": str(A2), "ID": "1"},
    ]
    assert encode_attachment_selection([o.value for o in options[:2]]) == (
        f"{options[0].value},{options[1].value}"
    )


def test_format_string_positional_and_fallback() -> None:
    args = (3, "x.pdf", "Artikel", "Anhang")
    assert format_attachment_label("%3$s#%1$d: %2$s", args) == "Artikel#3: x.pdf"
    assert format_attachment_label("%2$s (%1$d) 100%%", args) == "x.pdf (3) 100%"
    # Not portable (sequential %s, unknown argument): the default format, never None.
    assert format_attachment_label("%s %s", args) == "Artikel#3: x.pdf"
    assert format_attachment_label("%9$s", args) == "Artikel#3: x.pdf"


@pytest.mark.db
async def test_forged_keys_are_dropped(env: Env) -> None:
    keys = await env.keys(T_A)
    forged = f"ObjectType=Article;ObjectID={B1};ID=1"  # an article of another ticket
    dialog = _dialog({"Attachments": {"Display": "1"}})
    await env.apply(dialog, {"Attachments": [keys["a.pdf"], forged, "junk", "ObjectType=Article"]})
    assert await env.df_value() == keys["a.pdf"]
    # Nothing valid left: Znuny sets the field only for a non-empty selection.
    await env.apply(dialog, {"Attachments": [forged]})
    assert await env.df_value() == keys["a.pdf"]


@pytest.mark.db
async def test_missing_df_definition_skips_silently(env: Env) -> None:
    env.settings["Process::DynamicFieldProcessManagementAttachment"] = "Wf3NoSuchField"
    keys = await env.keys(T_A)
    # Znuny: `next DIALOGFIELD if !IsHashRefWithData($DynamicFieldConfig)`.
    await env.apply(_dialog({"Attachments": {"Display": "1"}}), {"Attachments": [keys["a.pdf"]]})
    async with env.factory() as session:
        assert (
            await collect_reused_attachments(session, ticket_id=T_A, sysconfig=env.sysconfig) == []
        )


# ---------------------------------------------------------------------------
# Reuse in ArticleSend / TicketArticleCreate
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_attachments_field_stores_df_and_article_send_reuses(env: Env) -> None:
    keys = await env.keys(T_A)
    selected = [keys["offer.pdf"], keys["b.pdf"]]
    await env.apply(_dialog({"Attachments": {"Display": "1"}}), {"Attachments": selected})
    assert await env.df_value() == ",".join(selected)

    await env.run(
        "ArticleSend",
        {
            "SenderType": "agent",
            "IsVisibleForCustomer": "1",
            "To": "customer@example.org",
            "Subject": "Your offer",
            "Body": "See the files.",
            "AttachmentsReuse": "1",
        },
    )
    assert await env.new_article_files() == [
        ("offer.pdf", b"offer-bytes"),
        ("b.pdf", b"bee-bytes"),
    ]
    assert await env.df_value() == ""  # cleared unless ...::Keep


@pytest.mark.db
async def test_reuse_off_attaches_nothing_and_keeps_df(env: Env) -> None:
    keys = await env.keys(T_A)
    env.set_df_value(T_A, keys["a.pdf"])
    await env.run(
        "TicketArticleCreate",
        {
            "SenderType": "agent",
            "IsVisibleForCustomer": "0",
            "Subject": "note",
            "Body": "no reuse",
        },
    )
    assert await env.new_article_files() == []
    assert await env.df_value() == keys["a.pdf"]


@pytest.mark.db
async def test_keep_setting_keeps_df(env: Env) -> None:
    keys = await env.keys(T_A)
    env.settings["Process::DynamicFieldProcessManagementAttachment::Keep"] = "1"
    env.set_df_value(T_A, keys["a.pdf"])
    await env.run(
        "TicketArticleCreate",
        {
            "SenderType": "agent",
            "IsVisibleForCustomer": "0",
            "CommunicationChannel": "Internal",
            "Subject": "note",
            "Body": "reuse",
            "AttachmentsReuse": "1",
        },
    )
    assert await env.new_article_files() == [("a.pdf", b"aye-bytes")]
    assert await env.df_value() == keys["a.pdf"]  # kept


@pytest.mark.db
async def test_reuse_ignores_articles_of_other_tickets(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    keys = await env.keys(T_A)
    foreign = f"ObjectType=Article;ObjectID={B1};ID=1"
    env.set_df_value(T_A, f"{foreign},{keys['a.pdf']}")
    with caplog.at_level(logging.WARNING, logger="tiqora.process.attachments_field"):
        async with env.factory() as session, session.begin():
            files = await collect_reused_attachments(
                session, ticket_id=T_A, sysconfig=env.sysconfig
            )
    assert [(n, c) for n, _t, c in files] == [("a.pdf", b"aye-bytes")]
    assert "another ticket" in caplog.text


@pytest.mark.db
async def test_reuse_skips_encrypted_blobs(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.set_df_value(
        T_B, f"ObjectType=Article;ObjectID={B2};ID=1,ObjectType=Article;ObjectID={B1};ID=1"
    )
    with caplog.at_level(logging.WARNING, logger="tiqora.process.attachments_field"):
        async with env.factory() as session, session.begin():
            files = await collect_reused_attachments(
                session, ticket_id=T_B, sysconfig=env.sysconfig
            )
    assert [n for n, _t, _c in files] == ["secret.pdf"]
    assert "encrypted" in caplog.text


@pytest.mark.db
async def test_std_attachments_by_name_and_id_invalid_skipped(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    config = {
        "Attachments": "wf3-std-a, wf3-std-missing",
        "AttachmentIDs": f"{STD_B},{STD_INVALID},999999,junk",
    }
    with caplog.at_level(logging.ERROR, logger="tiqora.process.attachments_field"):
        async with env.factory() as session:
            files = await collect_std_attachments(session, config)
    # IDs first, then names (ArticleSend.pm:168-200); the invalid one is logged and skipped.
    assert [(n, c) for n, _t, c in files] == [
        ("wf3-std-b.txt", b"std-b-bytes"),
        ("wf3-std-a.txt", b"std-a-bytes"),
    ]
    assert "Skip Attachment" in caplog.text

    await env.run(
        "TicketArticleCreate",
        {
            "SenderType": "agent",
            "IsVisibleForCustomer": "0",
            "Subject": "std",
            "Body": "with std attachments",
            **config,
        },
    )
    assert await env.new_article_files() == [
        ("wf3-std-b.txt", b"std-b-bytes"),
        ("wf3-std-a.txt", b"std-a-bytes"),
    ]


# ---------------------------------------------------------------------------
# The form
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_field_absent_on_start_form_and_portal(env: Env) -> None:
    keys = await env.keys(T_A)
    env.set_df_value(T_A, f"{keys['a.pdf']},ObjectType=Article;ObjectID=1;ID=9")
    graph = ProcessGraph(
        id=1,
        entity_id="Process-wf3892",
        name="wf3",
        state_entity_id="S1",
        config=ProcessConfig.from_dict({}),
        layout=ProcessLayout(positions={}),
    )
    node = ActivityDialogNode(
        id=1,
        entity_id="ActivityDialog-wf3892",
        name="wf3",
        config=_dialog(
            {"Attachments": {"Display": "2"}, "Title": {"Display": "1"}},
            ("AgentInterface", "CustomerInterface"),
        ),
    )
    user = AuthenticatedUser(
        id=AGENT, login="wf3.att.agent", first_name="Wf3", last_name="Agent", auth_method="session"
    )

    async def names(actor: ProcessActor, ticket: int | None) -> list[str]:
        async with env.factory() as session:
            form = await build_dialog_form(
                session,
                actor=actor,
                user=user if actor.interface == "AgentInterface" else None,
                graph=graph,
                dialog=node,
                ticket_id=ticket,
            )
        return [f.name for f in form.fields]

    assert await names(ProcessActor.agent(AGENT), None) == ["Title"]  # start form: no ticket
    customer = ProcessActor.customer(login="wf3-cu@example.org", customer_id="wf3-co")
    assert await names(customer, T_A) == ["Title"]  # portal

    async with env.factory() as session:
        form = await build_dialog_form(
            session,
            actor=ProcessActor.agent(AGENT),
            user=user,
            graph=graph,
            dialog=node,
            ticket_id=T_A,
        )
    field = next(f for f in form.fields if f.name == "Attachments")
    assert (field.kind, field.required) == ("ticket_attachments", True)
    assert [o.label for o in field.options] == [
        "Artikel#1: a.pdf",
        "Artikel#1: b.pdf",
        "Artikel#2: offer.pdf",
    ]
    # The stored selection preselects; the stale key is not offered any more.
    assert field.default == [keys["a.pdf"]]
