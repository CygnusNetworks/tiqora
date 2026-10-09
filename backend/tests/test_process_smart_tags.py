"""Smart tags in TransitionAction config (``smart_tags.py``) on a real DB.

Seed ids live in the 887500-887999 band (rulings F8). Znuny references:
``TransitionAction/Base.pm:339-615`` (``_ReplaceTicketAttributes``), ``:636-810``
(``_ReplaceAdditionalAttributes``, ``_ConvertScalar2ArrayRef``), ``TemplateGenerator.pm``
(``_Replace``, ``_ReplaceDynamicField``, unknown tags -> ``-``).

A dynamic-field tag yields Znuny's readable value, so a Multiselect ``["a", "b"]`` is the string
``"a, b"`` (``Multiselect.pm:757-804``), which ``DynamicFieldSet`` splits again
(``DynamicFieldSet.pm:132-143``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tiqora.channels.email.placeholder import PlaceholderContext, expand_placeholders
from tiqora.deployed import clear_revision_cache
from tiqora.domain.dynamic_field_values import df_display_value
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action, run_transitions
from tiqora.process.smart_tags import (
    build_tag_context,
    render_tags,
    render_ticketless,
    replace_action_config,
    split_scalar_list,
)
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT = 887510
DF_REGION, DF_TAGS, DF_PLAIN, DF_FLAG = 887520, 887521, 887522, 887523
TICKET_MAIN, TICKET_ONE, TICKET_PROC, TICKET_SET = 887540, 887541, 887542, 887543
TN_MAIN = "2026030488754"
ARTICLE_BASE = 887560  # article ids 887560..887564
PROC = 887700  # pm_process 887700, dialog 887710, activities 887720/887721, transition 887730

_SEEDED_URLS: list[str] = []

_DF_CONFIG = {
    DF_REGION: "---\nPossibleValues:\n  eu: Europa\n  us: Amerika\n",
    DF_TAGS: "---\nPossibleValues:\n  a: Alpha\n  b: Beta\n",
    DF_PLAIN: "---",
    DF_FLAG: "---",
}


def _cleanup(conn: Any) -> None:
    for stmt in (
        "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 887540 AND 887549",
        "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 887540 AND 887549",
        "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 887540 AND 887549",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 887540 AND 887549",
        "DELETE FROM article_data_mime WHERE article_id BETWEEN 887560 AND 887569",
        "DELETE FROM article WHERE id BETWEEN 887560 AND 887569",
        "DELETE FROM ticket WHERE id BETWEEN 887540 AND 887549",
        "DELETE FROM dynamic_field WHERE id BETWEEN 887520 AND 887529",
        "DELETE FROM users WHERE id = 887510",
        "DELETE FROM pm_process WHERE id BETWEEN 887700 AND 887709",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 887710 AND 887719",
        "DELETE FROM pm_activity WHERE id BETWEEN 887720 AND 887729",
        "DELETE FROM pm_transition WHERE id BETWEEN 887730 AND 887739",
        "DELETE FROM pm_transition_action WHERE id BETWEEN 887740 AND 887749",
    ):
        conn.execute(text(stmt))


_TIQORA_DDL = (
    """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        ticket_id BIGINT NOT NULL,
        payload TEXT,
        created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        processed TINYINT(1) NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        ticket_id BIGINT NULL,
        cache_type VARCHAR(100) NULL,
        created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)

_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)


def _add_ticket(conn: Any, tid: int, tn: str, title: str, *, customer_user: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
            " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:id, :tn, :title, 1, 1, 1, :u, :u, 3, 4, NULL, :cuid, 0, 0, 0, 0, 0, 0, 0,"
            " :t, 1, :t, 1)"
        ),
        {"id": tid, "tn": tn, "title": title, "u": AGENT, "cuid": customer_user, "t": T0},
    )


def _add_article(
    conn: Any, article_id: int, ticket_id: int, sender: str, subject: str, body: str
) -> None:
    conn.execute(
        text(
            "INSERT INTO article (id, ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tid, (SELECT id FROM article_sender_type WHERE name = :s),"
            " (SELECT MIN(id) FROM communication_channel), 1, :t, 1, :t, 1)"
        ),
        {"id": article_id, "tid": ticket_id, "s": sender, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_to, a_subject, a_body,"
            " a_content_type, incoming_time, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :frm, 'support@example.org', :subj, :body, 'text/plain', 0,"
            " :t, 1, :t, 1)"
        ),
        {
            "id": article_id,
            "frm": f"{sender}@example.org",
            "subj": subject,
            "body": body,
            "t": T0,
        },
    )


def _seed(conn: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:id, 'agent-8875', 'x', 'Ava', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"id": AGENT, "t": T0},
    )
    for fid, name, ftype in (
        (DF_REGION, "Region887", "Dropdown"),
        (DF_TAGS, "Tags887", "Multiselect"),
        (DF_PLAIN, "Plain887", "Text"),
        (DF_FLAG, "Flag887", "Checkbox"),
    ):
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:id, 0, :n, :n, :id, :ft, 'Ticket', :cfg, 1, :t, 1, :t, 1)"
            ),
            {"id": fid, "n": name, "ft": ftype, "cfg": _DF_CONFIG[fid], "t": T0},
        )
    _add_ticket(conn, TICKET_MAIN, TN_MAIN, "Printer", customer_user="cora@example.org")
    for fid, vt, vi in (
        (DF_REGION, "eu", None),
        (DF_TAGS, "a", None),
        (DF_TAGS, "b", None),
        (DF_FLAG, None, 1),
    ):
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text, value_int)"
                " VALUES (:f, :o, :vt, :vi)"
            ),
            {"f": fid, "o": TICKET_MAIN, "vt": vt, "vi": vi},
        )
    _add_article(
        conn, ARTICLE_BASE, TICKET_MAIN, "customer", f"[Ticket#{TN_MAIN}] Hilfe", "Drucker kaputt"
    )
    _add_article(conn, ARTICLE_BASE + 1, TICKET_MAIN, "agent", "Re: Hilfe", "Wir kuemmern uns")
    _add_ticket(conn, TICKET_ONE, "2026030488754A", "One article", customer_user=None)
    _add_article(conn, ARTICLE_BASE + 2, TICKET_ONE, "customer", "Only", "only body")
    _add_ticket(conn, TICKET_PROC, "2026030488754P", "Order ticket", customer_user=None)
    _add_ticket(conn, TICKET_SET, "2026030488754S", "Set ticket", customer_user=None)


def _add_process(conn: Any) -> None:
    """Activity A -> B on a transition that matches the title and runs TicketTitleSet."""
    act_a, act_b = "Activity-887720", "Activity-887721"
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, 'Process-887700', 'P',"
            " 'S1', '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": PROC,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: ActivityDialog-887710\n"
                f"Path:\n  {act_a}:\n    Transition-887730:\n      ActivityEntityID: {act_b}\n"
                "      TransitionAction:\n      - TransitionAction-887740\n"
                f"  {act_b}: {{}}\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {
            "id": 887710,
            "eid": "ActivityDialog-887710",
            "name": "D",
            "cfg": (
                "DescriptionShort: d\nFieldOrder: []\nFields: {}\nInterface:\n- AgentInterface\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {
            "id": 887720,
            "eid": act_a,
            "name": "A",
            "cfg": "ActivityDialog:\n  '1': ActivityDialog-887710\n",
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {"id": 887721, "eid": act_b, "name": "B", "cfg": "ActivityDialog: {}\n"},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {
            "id": 887730,
            "eid": "Transition-887730",
            "name": "T",
            "cfg": (
                "Condition:\n  '1':\n    Fields:\n      Title:\n        Match: Order ticket\n"
                "        Type: String\n    Type: and\n  Type: and\nConditionLinking: and\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition_action")),
        {
            "id": 887740,
            "eid": "TransitionAction-887740",
            "name": "TA",
            "cfg": (
                "Config:\n  Title: Order <OTRS_TICKET_TicketNumber>\n"
                "Module: Kernel::System::ProcessManagement::TransitionAction::TicketTitleSet\n"
            ),
        },
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in set(_SEEDED_URLS):
        engine = create_engine(url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED_URLS.clear()
    clear_revision_cache()


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> str:
    _SEEDED_URLS.append(mariadb_znuny_url)
    clear_revision_cache()
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        for ddl in _TIQORA_DDL:
            conn.execute(text(ddl))
        _cleanup(conn)
        _seed(conn)
        _add_process(conn)
    engine.dispose()
    return mariadb_znuny_url


@pytest.fixture
async def factory(sync_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng: AsyncEngine = create_async_engine(
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    )
    yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    await eng.dispose()


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _replace(
    factory: async_sessionmaker[AsyncSession], config: dict[str, Any], ticket_id: int = TICKET_MAIN
) -> dict[str, Any]:
    async with factory() as session:
        return await replace_action_config(
            config, session=session, ticket_id=ticket_id, user_id=AGENT, sysconfig=_sysconfig()
        )


@pytest.mark.db
async def test_ticket_attribute_and_df_tags_in_action_config(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = await _replace(
        factory,
        {
            "Title": "<OTRS_TICKET_Title> / <OTRS_TICKET_DynamicField_Region887> / "
            "<OTRS_TICKET_DynamicField_Region887_Value>",
            "Note": 5,
            "Queue": "<OTRS_TICKET_Queue>/<OTRS_TICKET_Owner>/<OTRS_TICKET_TicketNumber>",
        },
    )
    assert cfg == {
        "Title": "Printer / eu / Europa",
        "Note": 5,
        # Owner is the login (TicketGet), not the full name an e-mail template shows
        "Queue": f"Postmaster/agent-8875/{TN_MAIN}",
    }


@pytest.mark.db
async def test_multiselect_and_checkbox_dynamic_field_tags(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = await _replace(
        factory,
        {
            "Value": "<OTRS_TICKET_DynamicField_Tags887>",
            "Shown": "<OTRS_TICKET_DynamicField_Tags887_Value>",
            "Flag": "<OTRS_TICKET_DynamicField_Flag887>|<OTRS_TICKET_DynamicField_Flag887_Value>",
            "Unset": "[<OTRS_TICKET_DynamicField_Plain887>]",
        },
    )
    # Multiselect.pm:757-804 joins with ", "; DynamicFieldSet splits it again
    assert cfg["Value"] == "a, b"
    assert cfg["Shown"] == "Alpha, Beta"
    assert cfg["Flag"] == "1|Checked"
    assert cfg["Unset"] == "[]"
    assert split_scalar_list(cfg["Value"]) == ["a", "b"]


@pytest.mark.db
async def test_customer_and_agent_article_tags(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = await _replace(
        factory,
        {
            "Subject": "<OTRS_CUSTOMER_SUBJECT>",
            "Body": "<OTRS_AGENT_BODY>",
            "Who": "<OTRS_CURRENT_UserLogin> <OTRS_CURRENT_UserFullname>",
            "Customer": "<OTRS_CUSTOMER_BODY> from <OTRS_CUSTOMER_From>",
            "Agent": "<OTRS_AGENT_SUBJECT>",
        },
    )
    assert cfg == {
        "Subject": "Hilfe",  # TicketSubjectClean strips the ticket hook
        "Body": "Wir kuemmern uns",
        "Who": "agent-8875 Ava Agent",
        "Customer": "Drucker kaputt from customer@example.org",
        "Agent": "Hilfe",  # "Re: " is cleaned too
    }


@pytest.mark.db
async def test_config_tag_and_first_article(factory: async_sessionmaker[AsyncSession]) -> None:
    cfg = await _replace(
        factory,
        {
            "Hook": "<OTRS_CONFIG_Ticket::Hook>",
            "First": "<OTRS_FIRST_ARTICLE_Subject> / <OTRS_FIRST_ARTICLE_From>",
            "Last": "<OTRS_LAST_ARTICLE_Subject>",
        },
    )
    assert cfg == {
        "Hook": "Ticket#",
        "First": f"[Ticket#{TN_MAIN}] Hilfe / customer@example.org",
        "Last": "Re: Hilfe",
    }


@pytest.mark.db
async def test_last_article_body_of_a_single_article_ticket_is_dropped(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Base.pm:517-526: LAST_ARTICLE_Body is removed when the ticket has one article."""
    one = await _replace(
        factory,
        {"A": "[<OTRS_LAST_ARTICLE_Body>]", "B": "<OTRS_FIRST_ARTICLE_Body>"},
        TICKET_ONE,
    )
    assert one == {"A": "[]", "B": "only body"}
    many = await _replace(factory, {"A": "[<OTRS_LAST_ARTICLE_Body>]"})
    assert many == {"A": "[Wir kuemmern uns]"}


@pytest.mark.db
async def test_unknown_tags_render_dash_like_znuny(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = await _replace(
        factory,
        {
            # step 1 blanks an unknown OTRS_TICKET_ attribute (Base.pm:450-456, ``// ''``)
            "Ticket": "a<OTRS_TICKET_Nope>b",
            # unknown dynamic field and unknown tags reach TemplateGenerator -> "-"
            "Df": "<OTRS_TICKET_DynamicField_Nope887>",
            "Cur": "<OTRS_CURRENT_Nope>",
            "Any": "<OTRS_FOO_BAR>",
            # OTRS_TA_ markers are left alone (TemplateGenerator.pm:1592-1600)
            "Ta": "<OTRS_TA_Marker>",
            # an unset ticket attribute is blank in step 1, an empty hash value is "-" in step 2
            "Empty": "<OTRS_CUSTOMER_DATA_UserEmail>",
        },
    )
    assert cfg == {
        "Ticket": "ab",
        "Df": "-",
        "Cur": "-",
        "Any": "-",
        "Ta": "<OTRS_TA_Marker>",
        "Empty": "-",
    }


@pytest.mark.db
async def test_action_config_is_not_mutated(factory: async_sessionmaker[AsyncSession]) -> None:
    original = {
        "Title": "<OTRS_TICKET_Title>",
        "Nested": {"Title": "<OTRS_TICKET_Title>"},
        "List": ["<OTRS_TICKET_Title>"],
        "Note": 5,
    }
    snapshot = {
        "Title": "<OTRS_TICKET_Title>",
        "Nested": {"Title": "<OTRS_TICKET_Title>"},
        "List": ["<OTRS_TICKET_Title>"],
        "Note": 5,
    }
    result = await _replace(factory, original)
    assert original == snapshot
    assert result["Title"] == "Printer"
    # only top-level strings are replaced (Base.pm iterates ``keys %{ $Param{Config} }``)
    assert result["Nested"] == {"Title": "<OTRS_TICKET_Title>"}
    assert result["List"] == ["<OTRS_TICKET_Title>"]
    assert result["Note"] == 5


@pytest.mark.db
async def test_render_ticketless_uses_submitted_values(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        rendered = await render_ticketless(
            "<OTRS_TICKET_Title> in <OTRS_TICKET_Queue> by <OTRS_CURRENT_UserLogin>"
            " <OTRS_TICKET_Nope>",
            session=session,
            user_id=AGENT,
            sysconfig=_sysconfig(),
            values={"Title": "Neu", "Queue": "Postmaster"},
        )
    assert rendered == "Neu in Postmaster by agent-8875 -"


@pytest.mark.db
async def test_title_set_action_uses_tags_end_to_end(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session, session.begin():
        for name, value in (
            ("ProcessManagementProcessID", "Process-887700"),
            ("ProcessManagementActivityID", "Activity-887720"),
        ):
            await session.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " SELECT id, :o, :v FROM dynamic_field WHERE name = :n"
                ),
                {"o": TICKET_PROC, "v": value, "n": name},
            )
    async with factory() as session, session.begin():
        outcome = await run_transitions(
            session, ticket_id=TICKET_PROC, user_id=AGENT, sysconfig=_sysconfig()
        )
    assert outcome.activity_changed is True
    assert outcome.new_activity_entity_id == "Activity-887721"
    async with factory() as session:
        title = (
            await session.execute(
                text("SELECT title FROM ticket WHERE id = :t"), {"t": TICKET_PROC}
            )
        ).scalar_one()
    assert title == "Order 2026030488754P"


@pytest.mark.db
async def test_dynamic_field_set_splits_a_multiselect_string(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A tag yields "a, b"; DynamicFieldSet stores two values (DynamicFieldSet.pm:132-143)."""
    action = TransitionActionConfig(
        module="Kernel::System::ProcessManagement::TransitionAction::DynamicFieldSet",
        config={"Tags887": "<OTRS_TICKET_Title>, a", "Plain887": "x, y"},
    )
    async with factory() as session, session.begin():
        skipped = await execute_transition_action(
            session,
            action=action,
            ticket_id=TICKET_SET,
            process_entity_id="P",
            activity_entity_id="A",
            transition_entity_id="T",
            user_id=AGENT,
            sysconfig=_sysconfig(),
        )
    assert skipped is None
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT f.name, v.value_text FROM dynamic_field_value v"
                    " JOIN dynamic_field f ON f.id = v.field_id WHERE v.object_id = :t"
                    " ORDER BY f.name, v.id"
                ),
                {"t": TICKET_SET},
            )
        ).all()
    assert [(r[0], r[1]) for r in rows] == [
        ("Plain887", "x, y"),
        ("Tags887", "Set ticket"),
        ("Tags887", "a"),
    ]


@pytest.mark.db
async def test_build_tag_context_carries_articles_and_dash_mode(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        ctx = await build_tag_context(
            session, ticket_id=TICKET_MAIN, user_id=AGENT, sysconfig=_sysconfig()
        )
        text_ = await render_tags(
            "<OTRS_CUSTOMER_SUBJECT>|<OTRS_NOPE>", ctx=ctx, sysconfig=_sysconfig()
        )
    assert ctx.znuny_dash is True
    assert ctx.customer_subject == "Hilfe"
    assert ctx.agent_body == "Wir kuemmern uns"
    assert text_ == "Hilfe|-"


def test_df_display_value_follows_znuny_lookup_and_render() -> None:
    possible = {"PossibleValues": {"eu": "Europa", "zero": "0", "n": ""}}
    # BaseSelect.pm:806-836: key -> label; a key without a truthy label stays the key
    assert df_display_value("Dropdown", possible, "eu") == "Europa"
    assert df_display_value("Dropdown", possible, ["zero"]) == "zero"
    assert df_display_value("Dropdown", possible, "n") == "n"
    assert df_display_value("Dropdown", possible, "unknown") == "unknown"
    assert df_display_value("Dropdown", possible, None) == ""
    # Multiselect.pm:860-903 + 757-804: labels joined with ", ", empty keys dropped
    assert df_display_value("Multiselect", possible, ["eu", "", "x"]) == "Europa, x"
    # Backend.pm:825-842 Checkbox lookup
    assert df_display_value("Checkbox", {}, "1") == "Checked"
    assert df_display_value("Checkbox", {}, "0") == "Unchecked"
    assert df_display_value("Checkbox", {}, "") == ""
    # Date.pm:601-618 keeps the day only; Text and DateTime stay as stored
    assert df_display_value("Date", {}, "2026-02-01 10:30:00") == "2026-02-01"
    assert df_display_value("DateTime", {}, "2026-02-01 10:30:00") == "2026-02-01 10:30:00"
    assert df_display_value("Text", {}, "hello") == "hello"


async def test_dash_mode_is_off_by_default() -> None:
    """E-mail templates and notifications keep rendering unknown tags empty."""
    ctx = PlaceholderContext(ticket={"title": "T"})
    out = await expand_placeholders(
        None, _sysconfig(), "<OTRS_TICKET_Title>|<OTRS_TICKET_Nope>|<OTRS_FOO_BAR>", context=ctx
    )
    assert out == "T||"
    ctx.znuny_dash = True
    out = await expand_placeholders(
        None, _sysconfig(), "<OTRS_TICKET_Title>|<OTRS_TICKET_Nope>|<OTRS_FOO_BAR>", context=ctx
    )
    assert out == "T|-|-"


def test_split_scalar_list_trims_items() -> None:
    assert split_scalar_list(" 1,2 ,3,4 ") == ["1", "2", "3", "4"]
