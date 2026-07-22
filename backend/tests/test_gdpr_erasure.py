"""Tests for GDPR erasure engine (resolve/preview/apply/rollback/purge).

Covers MariaDB and Postgres, selector criteria (AND), anonymize + delete,
custom PII column via information_schema, byte-for-byte rollback, and purge.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import gdpr as admin_gdpr
from tiqora.api.v1.admin.schemas import (
    ErasureSelectorIn,
    GdprCustomerRecordPreviewRequest,
    GdprSelectorCountRequest,
)
from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.dev_anonymize import ValueMapper
from tiqora.gdpr.erasure import (
    ErasureError,
    ErasureNotFoundError,
    ErasureSelector,
    build_customer_record_preview,
    build_erasure_preview,
    purge_expired_backups,
    purge_job_backup,
    resolve_selector,
    rollback_job,
    run_erasure,
)
from tiqora.gdpr.gate import GdprRefusedError


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


NOW = datetime(2026, 1, 1, 12, 0, 0)


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _is_mysql(url: str) -> bool:
    return "mysql" in url


async def _seed_tiqora_tables(session: AsyncSession, *, mysql: bool) -> None:
    bool_t = "TINYINT(1)" if mysql else "BOOLEAN"
    auto = "BIGINT AUTO_INCREMENT PRIMARY KEY" if mysql else "BIGSERIAL PRIMARY KEY"
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_settings ("
                f"{'`key`' if mysql else 'key'} VARCHAR(200) PRIMARY KEY, value TEXT)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_gdpr_audit ("
                f"id {auto}, action VARCHAR(50) NOT NULL,"
                " target VARCHAR(255) NOT NULL, actor VARCHAR(200) NOT NULL,"
                f" counts TEXT NOT NULL, force_parallel {bool_t} NOT NULL DEFAULT FALSE,"
                " created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation ("
                f"id {auto}, ticket_id BIGINT NULL,"
                " cache_type VARCHAR(100) NULL,"
                " created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_gdpr_job ("
                f"id {auto}, mode VARCHAR(20) NOT NULL, selector TEXT NOT NULL,"
                " resolved_logins TEXT NOT NULL, status VARCHAR(20) NOT NULL,"
                " counts TEXT NOT NULL DEFAULT '{}', seed INT NULL,"
                " actor VARCHAR(200) NOT NULL,"
                f" force_parallel {bool_t} NOT NULL DEFAULT FALSE,"
                " created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " rolled_back_at TIMESTAMP NULL,"
                " backup_expires_at TIMESTAMP NOT NULL)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_gdpr_backup ("
                f"id {auto}, job_id BIGINT NOT NULL, table_name VARCHAR(100) NOT NULL,"
                " row_pk TEXT NOT NULL, original_row TEXT NOT NULL,"
                " created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    await session.commit()

    # Best-effort: add site-specific PII column for introspection coverage.
    with contextlib.suppress(Exception):
        if mysql:
            await session.execute(
                text("ALTER TABLE customer_user ADD COLUMN wpnum VARCHAR(50) NULL")
            )
        else:
            await session.execute(
                text("ALTER TABLE customer_user ADD COLUMN IF NOT EXISTS wpnum VARCHAR(50)")
            )
        await session.commit()

    # Clean prior rows for idempotent re-runs.
    for tbl in (
        "tiqora_gdpr_backup",
        "tiqora_gdpr_job",
        "tiqora_gdpr_audit",
        "tiqora_cache_invalidation",
    ):
        with contextlib.suppress(Exception):
            await session.execute(text(f"DELETE FROM {tbl}"))
    await session.commit()


async def _insert_customer(
    session: AsyncSession,
    *,
    login: str,
    customer_id: str,
    email: str | None = None,
    change_time: datetime = NOW,
    valid_id: int = 1,
    wpnum: str | None = None,
) -> int:
    async with session.begin():
        cu = CustomerUser(
            login=login,
            email=email or login,
            customer_id=customer_id,
            first_name="Target",
            last_name="Person",
            phone="+49 30 1234567",
            valid_id=valid_id,
            create_time=NOW,
            create_by=1,
            change_time=change_time,
            change_by=1,
        )
        session.add(cu)
        await session.flush()
        cid = int(cu.id)
    if wpnum is not None:
        with contextlib.suppress(Exception):
            async with session.begin():
                await session.execute(
                    text("UPDATE customer_user SET wpnum = :w WHERE id = :id"),
                    {"w": wpnum, "id": cid},
                )
    return cid


async def _insert_company(session: AsyncSession, *, customer_id: str, name: str) -> None:
    async with session.begin():
        session.add(
            CustomerCompany(
                customer_id=customer_id,
                name=name,
                street="Main 1",
                zip="10115",
                city="Berlin",
                country="DE",
                valid_id=1,
                create_time=NOW,
                create_by=1,
                change_time=NOW,
                change_by=1,
            )
        )


async def _insert_ticket(
    session: AsyncSession,
    *,
    customer_user_id: str,
    customer_id: str = "ERASE-CO",
    tn: str = "2026010112000001",
    state_id: int = 4,  # open-ish depending on seed; 4 often "open"
) -> int:
    t = Ticket(
        tn=tn,
        title="Erase me",
        queue_id=1,
        ticket_lock_id=1,
        user_id=1,
        responsible_user_id=1,
        ticket_priority_id=3,
        ticket_state_id=state_id,
        customer_id=customer_id,
        customer_user_id=customer_user_id,
        timeout=0,
        until_time=0,
        escalation_time=0,
        escalation_update_time=0,
        escalation_response_time=0,
        escalation_solution_time=0,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )
    async with session.begin():
        session.add(t)
        await session.flush()
        return int(t.id)


async def _insert_article_bundle(
    session: AsyncSession,
    *,
    ticket_id: int,
    from_addr: str,
    body: str,
) -> int:
    async with session.begin():
        await session.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:ticket_id, 2, 1, 1, :ct, 1, :ct, 1)"
            ),
            {"ticket_id": ticket_id, "ct": NOW},
        )
        article_id = int(
            (
                await session.execute(
                    text("SELECT MAX(id) FROM article WHERE ticket_id = :tid"),
                    {"tid": ticket_id},
                )
            ).scalar_one()
        )
        await session.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_from, a_to, a_cc, a_bcc,"
                " a_reply_to, a_subject, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :a_from, 'agent@tiqora.test', :a_cc, NULL,"
                " NULL, :subj, :a_body, :mid, 0, :ct, 1, :ct, 1)"
            ),
            {
                "aid": article_id,
                "a_from": from_addr,
                "a_cc": from_addr,
                "subj": f"Hello {from_addr}",
                "a_body": body,
                "mid": f"<msg-{article_id}@example.com>",
                "ct": NOW,
            },
        )
        # PG fixture maps LONGBLOB→TEXT for body; MySQL keeps LONGBLOB.
        # Bind as str always — works for both (MySQL coerces).
        await session.execute(
            text(
                "INSERT INTO article_data_mime_plain (article_id, body,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :body, :ct, 1, :ct, 1)"
            ),
            {"aid": article_id, "body": body, "ct": NOW},
        )
        # Attachment content: use empty/null-safe approach — store as text via
        # convert_from is dialect-specific; insert empty content + filename only
        # on PG when bind fails. Prefer ORM-less cast via decode of hex.
        await session.execute(
            text(
                "INSERT INTO article_data_mime_attachment (article_id, filename,"
                " content_size, content_type, content,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :fn, :cs, 'text/plain', NULL, :ct, 1, :ct, 1)"
            ),
            {
                "aid": article_id,
                "fn": f"secret-{from_addr}.txt",
                "cs": str(len(body)),
                "ct": NOW,
            },
        )
        await session.execute(
            text(
                "INSERT INTO article_search_index (ticket_id, article_id,"
                " article_key, article_value)"
                " VALUES (:tid, :aid, 'From', :val)"
            ),
            {"tid": ticket_id, "aid": article_id, "val": f"{from_addr} {body}"},
        )
    return article_id


async def _factory(url: str) -> tuple:
    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ReDoS guards on admin-supplied selector regexes (L-1)
# ---------------------------------------------------------------------------


def test_compile_selector_regex_rejects_overlong_pattern() -> None:
    from tiqora.gdpr.erasure import _MAX_SELECTOR_REGEX_LEN, _compile_selector_regex

    overlong = "a" * (_MAX_SELECTOR_REGEX_LEN + 1)
    with pytest.raises(ErasureError, match="maximum length"):
        _compile_selector_regex(overlong, field="login_regex")


def test_compile_selector_regex_rejects_catastrophic_pattern() -> None:
    from tiqora.gdpr.erasure import _compile_selector_regex

    with pytest.raises(ErasureError, match="ReDoS|nested|quantifier"):
        _compile_selector_regex(r"(a+)+$", field="login_regex")

    with pytest.raises(ErasureError, match="ReDoS|nested|quantifier"):
        _compile_selector_regex(r"(a*)*b", field="customer_id_regex")


def test_compile_selector_regex_allows_normal_patterns() -> None:
    from tiqora.gdpr.erasure import _compile_selector_regex

    cre = _compile_selector_regex(r"^user-\d+$", field="login_regex")
    assert cre.search("user-42")
    assert cre.search("nope") is None


# resolve_selector
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_selector_criteria(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    prefix = "rs_mysql" if mysql else "rs_pg"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        id_a = await _insert_customer(
            session, login=f"{prefix}.alice@ex.com", customer_id=f"{prefix}-A", wpnum="W1"
        )
        id_b = await _insert_customer(
            session,
            login=f"{prefix}.bob@ex.com",
            customer_id=f"{prefix}-B",
            change_time=datetime(2025, 6, 1),
            valid_id=1,
        )
        id_c = await _insert_customer(
            session,
            login=f"{prefix}.carol@ex.com",
            customer_id=f"{prefix}-A",
            valid_id=2,
        )
        await _insert_ticket(
            session,
            customer_user_id=f"{prefix}.alice@ex.com",
            customer_id=f"{prefix}-A",
            tn=f"2026{prefix}0001",
        )

    async with factory() as session:
        # logins
        ids = await resolve_selector(session, ErasureSelector(logins=[f"{prefix}.alice@ex.com"]))
        assert ids == [id_a]

        # customer_ids
        ids = await resolve_selector(session, ErasureSelector(customer_ids=[f"{prefix}-B"]))
        assert ids == [id_b]

        # login_regex
        ids = await resolve_selector(session, ErasureSelector(login_regex=rf"^{prefix}\.b"))
        assert id_b in ids
        assert id_a not in ids

        # customer_id_regex
        ids = await resolve_selector(session, ErasureSelector(customer_id_regex=rf"{prefix}-A$"))
        assert set(ids) >= {id_a, id_c}

        # change_time window
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A", f"{prefix}-B"],
                changed_before=datetime(2025, 12, 1),
            ),
        )
        assert ids == [id_b]

        # valid_id
        ids = await resolve_selector(
            session, ErasureSelector(customer_ids=[f"{prefix}-A"], valid_id=2)
        )
        assert ids == [id_c]

        # no_tickets
        ids = await resolve_selector(
            session,
            ErasureSelector(customer_ids=[f"{prefix}-B"], activity="no_tickets"),
        )
        assert ids == [id_b]
        ids = await resolve_selector(
            session,
            ErasureSelector(logins=[f"{prefix}.alice@ex.com"], activity="no_tickets"),
        )
        assert ids == []

        # combined AND
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A"],
                valid_id=1,
                login_regex=r"alice",
            ),
        )
        assert ids == [id_a]

        # empty selector → empty
        assert await resolve_selector(session, ErasureSelector()) == []

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_selector_email_regex_and_negation(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """email_regex filter, plus negation (``*_negate``) for login/customer_id/email."""
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    prefix = "neg_mysql" if mysql else "neg_pg"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        id_a = await _insert_customer(
            session,
            login=f"{prefix}.alice",
            customer_id=f"{prefix}-A",
            email=f"alice@{prefix}-edu.example",
        )
        id_b = await _insert_customer(
            session,
            login=f"{prefix}.bob",
            customer_id=f"{prefix}-B",
            email=f"bob@{prefix}-corp.example",
        )
        id_c = await _insert_customer(
            session,
            login=f"{prefix}.carol",
            customer_id=f"{prefix}-A",
            email=f"carol@{prefix}-corp.example",
        )

    async with factory() as session:
        # email_regex: positive match
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A", f"{prefix}-B"],
                email_regex=rf"@{prefix}-edu\.",
            ),
        )
        assert ids == [id_a]

        # email_regex: negated — everyone whose email does NOT match
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A", f"{prefix}-B"],
                email_regex=rf"@{prefix}-edu\.",
                email_regex_negate=True,
            ),
        )
        assert set(ids) == {id_b, id_c}
        assert id_a not in ids

        # login_regex negated
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A", f"{prefix}-B"],
                login_regex=rf"^{prefix}\.alice$",
                login_regex_negate=True,
            ),
        )
        assert set(ids) == {id_b, id_c}

        # customer_id_regex negated
        ids = await resolve_selector(
            session,
            ErasureSelector(
                logins=[f"{prefix}.alice", f"{prefix}.bob", f"{prefix}.carol"],
                customer_id_regex=rf"^{prefix}-A$",
                customer_id_regex_negate=True,
            ),
        )
        assert ids == [id_b]

        # combined: negated login_regex AND positive email_regex (AND semantics)
        ids = await resolve_selector(
            session,
            ErasureSelector(
                customer_ids=[f"{prefix}-A", f"{prefix}-B"],
                login_regex=rf"^{prefix}\.alice$",
                login_regex_negate=True,
                email_regex=rf"@{prefix}-corp\.",
            ),
        )
        assert set(ids) == {id_b, id_c}

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_selector_python_regex_semantics(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Python re semantics: \\d, (?i), invalid pattern, tz-aware time window.

    Native SQL REGEXP/~ does not understand these patterns; resolve_selector
    must apply re.search on in-memory candidates so admin preview is non-empty.
    """
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    prefix = "rx_mysql" if mysql else "rx_pg"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        id_legacy = await _insert_customer(
            session,
            login=f"{prefix}-legacy-0007",
            customer_id=f"{prefix}-LEGACY-42",
            change_time=datetime(2025, 8, 15, 10, 0, 0),
        )
        id_legacy_upper = await _insert_customer(
            session,
            login=f"{prefix}-LEGACY-42",
            customer_id=f"{prefix}-OTHER",
            change_time=datetime(2025, 9, 1, 10, 0, 0),
        )
        id_alt = await _insert_customer(
            session,
            login=f"{prefix}-alt_kunde",
            customer_id=f"{prefix}-ALT",
            change_time=datetime(2025, 7, 1, 10, 0, 0),
        )
        id_neu = await _insert_customer(
            session,
            login=f"{prefix}-neu.kunde",
            customer_id=f"{prefix}-NEU",
            change_time=datetime(2024, 1, 1, 10, 0, 0),
        )

        # login_regex with \\d — failed under native SQL REGEXP before the fix
        ids = await resolve_selector(
            session,
            ErasureSelector(login_regex=rf"^{prefix}-legacy-\d+$"),
        )
        assert ids == [id_legacy]
        assert id_legacy_upper not in ids
        assert id_neu not in ids

        # customer_id_regex anchored prefix
        ids = await resolve_selector(
            session,
            ErasureSelector(customer_id_regex=rf"^{prefix}-LEGACY-"),
        )
        assert ids == [id_legacy]

        # inline case-insensitive flag (?i)
        ids = await resolve_selector(
            session,
            ErasureSelector(login_regex=rf"(?i)^{prefix}-ALT_"),
        )
        assert ids == [id_alt]

        # invalid pattern → ErasureError (4xx path), not a 500
        with pytest.raises(ErasureError, match="invalid login_regex"):
            await resolve_selector(session, ErasureSelector(login_regex="[unclosed"))

        with pytest.raises(ErasureError, match="invalid customer_id_regex"):
            await resolve_selector(session, ErasureSelector(customer_id_regex="[unclosed"))

        # tz-aware changed_after combined with regex — no crash, no spurious empty
        ids = await resolve_selector(
            session,
            ErasureSelector(
                login_regex=rf"^{prefix}-legacy-\d+$",
                changed_after=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
            ),
        )
        assert ids == [id_legacy]

        # window after the row's change_time → empty (still no tz crash)
        ids = await resolve_selector(
            session,
            ErasureSelector(
                login_regex=rf"^{prefix}-legacy-\d+$",
                changed_after=datetime(2025, 12, 1, tzinfo=UTC),
            ),
        )
        assert ids == []

    await engine.dispose()


# ---------------------------------------------------------------------------
# anonymize + rollback
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_anonymize_scrubs_and_rollback_restores(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    login = f"erase.anon.{'my' if mysql else 'pg'}@example.com"
    company = f"ERASE-ANON-{'MY' if mysql else 'PG'}"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        await _insert_company(session, customer_id=company, name="Acme Erase")
        cuid = await _insert_customer(session, login=login, customer_id=company, wpnum="WP-999")
        tid = await _insert_ticket(
            session, customer_user_id=login, customer_id=company, tn=f"2026ANON{cuid}"
        )
        await _insert_article_bundle(
            session,
            ticket_id=tid,
            from_addr=login,
            body=f"Private body for {login}",
        )

    # Snapshot original row for byte-for-byte check.
    async with factory() as session:
        orig_cu = dict(
            (
                await session.execute(
                    text("SELECT * FROM customer_user WHERE id = :id"), {"id": cuid}
                )
            )
            .mappings()
            .one()
        )
        orig_mime = dict(
            (
                await session.execute(
                    text(
                        "SELECT a_from, a_to, a_cc, a_body, a_subject FROM article_data_mime"
                        " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                    ),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        orig_plain = (
            await session.execute(
                text(
                    "SELECT body FROM article_data_mime_plain"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        orig_att = dict(
            (
                await session.execute(
                    text(
                        "SELECT filename, content FROM article_data_mime_attachment"
                        " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                    ),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        orig_search = (
            await session.execute(
                text(
                    "SELECT article_value FROM article_search_index"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        orig_ticket = dict(
            (
                await session.execute(
                    text("SELECT customer_user_id, customer_id FROM ticket WHERE id = :t"),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        orig_company_name = (
            await session.execute(
                text("SELECT name FROM customer_company WHERE customer_id = :c"),
                {"c": company},
            )
        ).scalar_one()

        preview = await build_erasure_preview(
            session, ErasureSelector(logins=[login]), mode="anonymize"
        )
        assert preview.counts["customer_user"] == 1
        assert preview.counts["tickets"] == 1
        assert preview.counts["article_data_mime"] >= 1

    # Gate refuses without force_parallel
    with pytest.raises(GdprRefusedError):
        await run_erasure(
            factory,
            Settings(),
            customer_user_ids=[cuid],
            mode="anonymize",
            force_parallel=False,
            actor="test",
        )

    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="anonymize",
        seed=42,
        force_parallel=True,
        actor="test",
        selector=ErasureSelector(logins=[login]),
    )
    assert result.job_id > 0
    assert result.counts["customer_user"] == 1
    assert result.counts["article_data_mime"] >= 1

    mapper = ValueMapper(seed=42)
    expected_login = mapper.map_value(login, "login")

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT login, first_name, email, valid_id, phone FROM customer_user"
                    " WHERE id = :id"
                ),
                {"id": cuid},
            )
        ).one()
        assert row.login == expected_login
        assert row.first_name != "Target"
        assert row.email != login
        assert int(row.valid_id) == 2
        assert row.phone != "+49 30 1234567"

        # Custom column wpnum scrubbed when present.
        with contextlib.suppress(Exception):
            wp = (
                await session.execute(
                    text("SELECT wpnum FROM customer_user WHERE id = :id"), {"id": cuid}
                )
            ).scalar_one()
            if orig_cu.get("wpnum"):
                assert wp != orig_cu["wpnum"]

        mime = (
            await session.execute(
                text(
                    "SELECT a_from, a_body FROM article_data_mime"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).one()
        assert login not in (mime.a_from or "")
        assert f"Private body for {login}" not in (mime.a_body or "")

        plain = (
            await session.execute(
                text(
                    "SELECT body FROM article_data_mime_plain"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        plain_text = plain.decode("utf-8") if isinstance(plain, (bytes, memoryview)) else str(plain)
        assert login not in plain_text

        att = (
            await session.execute(
                text(
                    "SELECT filename, content FROM article_data_mime_attachment"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).one()
        assert login not in (att.filename or "")
        # content may stay NULL (seed) or be zeroed by scrub.
        if att.content is not None:
            if isinstance(att.content, (bytes, memoryview)):
                assert bytes(att.content) == b""
            else:
                assert att.content == ""

        search_val = (
            await session.execute(
                text(
                    "SELECT article_value FROM article_search_index"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        assert login not in (search_val or "")

        trow = (
            await session.execute(
                text("SELECT customer_user_id, customer_id FROM ticket WHERE id = :t"),
                {"t": tid},
            )
        ).one()
        assert trow.customer_user_id == expected_login

        inv = (
            await session.execute(text("SELECT COUNT(*) FROM tiqora_cache_invalidation"))
        ).scalar_one()
        assert int(inv) >= 1

        audit = (
            await session.execute(
                text(
                    "SELECT action, force_parallel FROM tiqora_gdpr_audit"
                    " WHERE action = 'erasure_anonymize'"
                )
            )
        ).first()
        assert audit is not None
        assert bool(audit[1]) is True

        # Preview counts should match apply (customer_user / tickets / mime).
        assert preview.counts["customer_user"] == result.counts["customer_user"]
        assert preview.counts["tickets"] == result.counts["tickets"]

    # Rollback restores originals.
    rb = await rollback_job(factory, Settings(), result.job_id, force_parallel=True, actor="test")
    assert rb["restored_rows"] > 0

    async with factory() as session:
        restored = dict(
            (
                await session.execute(
                    text("SELECT * FROM customer_user WHERE id = :id"), {"id": cuid}
                )
            )
            .mappings()
            .one()
        )
        assert restored["login"] == orig_cu["login"]
        assert restored["email"] == orig_cu["email"]
        assert restored["first_name"] == orig_cu["first_name"]
        assert int(restored["valid_id"]) == int(orig_cu["valid_id"])
        if "wpnum" in orig_cu and orig_cu["wpnum"] is not None:
            assert restored.get("wpnum") == orig_cu["wpnum"]

        mime2 = dict(
            (
                await session.execute(
                    text(
                        "SELECT a_from, a_to, a_cc, a_body, a_subject FROM article_data_mime"
                        " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                    ),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        assert mime2["a_from"] == orig_mime["a_from"]
        assert mime2["a_body"] == orig_mime["a_body"]

        plain2 = (
            await session.execute(
                text(
                    "SELECT body FROM article_data_mime_plain"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        if isinstance(orig_plain, (bytes, memoryview)) or isinstance(plain2, (bytes, memoryview)):
            assert bytes(plain2) == bytes(orig_plain)
        else:
            assert plain2 == orig_plain

        att2 = dict(
            (
                await session.execute(
                    text(
                        "SELECT filename, content FROM article_data_mime_attachment"
                        " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                    ),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        assert att2["filename"] == orig_att["filename"]

        search2 = (
            await session.execute(
                text(
                    "SELECT article_value FROM article_search_index"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        assert search2 == orig_search

        t2 = dict(
            (
                await session.execute(
                    text("SELECT customer_user_id, customer_id FROM ticket WHERE id = :t"),
                    {"t": tid},
                )
            )
            .mappings()
            .one()
        )
        assert t2["customer_user_id"] == orig_ticket["customer_user_id"]
        assert t2["customer_id"] == orig_ticket["customer_id"]

        cname = (
            await session.execute(
                text("SELECT name FROM customer_company WHERE customer_id = :c"),
                {"c": company},
            )
        ).scalar_one()
        assert cname == orig_company_name

        job_status = (
            await session.execute(
                text("SELECT status FROM tiqora_gdpr_job WHERE id = :id"),
                {"id": result.job_id},
            )
        ).scalar_one()
        assert job_status == "rolled_back"

    # Second rollback refused.
    with pytest.raises(ErasureError):
        await rollback_job(factory, Settings(), result.job_id, force_parallel=True, actor="test")

    await engine.dispose()


# ---------------------------------------------------------------------------
# delete + rollback
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_removes_master_tokenizes_ticket_and_rollback(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    login = f"erase.del.{'my' if mysql else 'pg'}@example.com"
    company = f"ERASE-DEL-{'MY' if mysql else 'PG'}"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        await _insert_company(session, customer_id=company, name="Delete Co")
        cuid = await _insert_customer(session, login=login, customer_id=company)
        tid = await _insert_ticket(
            session, customer_user_id=login, customer_id=company, tn=f"2026DEL{cuid}"
        )
        await _insert_article_bundle(session, ticket_id=tid, from_addr=login, body=f"body {login}")
        # Related master rows
        await session.execute(
            text(
                "INSERT INTO customer_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:u, 'UserLanguage', 'de')"
            ),
            {"u": login},
        )
        await session.commit()

    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="delete",
        seed=7,
        force_parallel=True,
        actor="test",
    )

    async with factory() as session:
        gone = (
            await session.execute(
                text("SELECT COUNT(*) FROM customer_user WHERE id = :id"), {"id": cuid}
            )
        ).scalar_one()
        assert int(gone) == 0
        prefs = (
            await session.execute(
                text("SELECT COUNT(*) FROM customer_preferences WHERE user_id = :u"),
                {"u": login},
            )
        ).scalar_one()
        assert int(prefs) == 0
        # Tickets remain, refs tokenized.
        trow = (
            await session.execute(
                text("SELECT customer_user_id, customer_id FROM ticket WHERE id = :t"),
                {"t": tid},
            )
        ).one()
        assert trow.customer_user_id != login
        assert trow.customer_user_id is not None
        # Company deleted (no other users).
        co = (
            await session.execute(
                text("SELECT COUNT(*) FROM customer_company WHERE customer_id = :c"),
                {"c": company},
            )
        ).scalar_one()
        assert int(co) == 0
        # Articles still present (never hard-deleted).
        arts = (
            await session.execute(
                text("SELECT COUNT(*) FROM article WHERE ticket_id = :t"), {"t": tid}
            )
        ).scalar_one()
        assert int(arts) >= 1
        backups = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_gdpr_backup WHERE job_id = :j"),
                {"j": result.job_id},
            )
        ).scalar_one()
        assert int(backups) >= 1

    await rollback_job(factory, Settings(), result.job_id, force_parallel=True, actor="test")

    async with factory() as session:
        restored = (
            await session.execute(
                text("SELECT login, email, first_name FROM customer_user WHERE id = :id"),
                {"id": cuid},
            )
        ).one()
        assert restored.login == login
        assert restored.email == login
        assert restored.first_name == "Target"
        prefs = (
            await session.execute(
                text("SELECT preferences_value FROM customer_preferences WHERE user_id = :u"),
                {"u": login},
            )
        ).scalar_one()
        assert prefs == "de"
        co = (
            await session.execute(
                text("SELECT name FROM customer_company WHERE customer_id = :c"),
                {"c": company},
            )
        ).scalar_one()
        assert co == "Delete Co"
        trow = (
            await session.execute(
                text("SELECT customer_user_id FROM ticket WHERE id = :t"), {"t": tid}
            )
        ).scalar_one()
        assert trow == login

    await engine.dispose()


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
async def test_purge_expires_and_blocks_rollback(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    login = "erase.purge@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        cuid = await _insert_customer(session, login=login, customer_id="ERASE-PURGE")

    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="anonymize",
        seed=1,
        force_parallel=True,
        actor="test",
    )

    # Force expiry into the past.
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE tiqora_gdpr_job SET backup_expires_at = :exp WHERE id = :id"),
            {"exp": datetime(2020, 1, 1), "id": result.job_id},
        )

    purged = await purge_expired_backups(factory, now=datetime(2026, 7, 1))
    assert purged["purged_jobs"] >= 1
    assert purged["deleted_backups"] >= 1

    async with factory() as session:
        status = (
            await session.execute(
                text("SELECT status FROM tiqora_gdpr_job WHERE id = :id"),
                {"id": result.job_id},
            )
        ).scalar_one()
        assert status == "purged"
        left = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_gdpr_backup WHERE job_id = :j"),
                {"j": result.job_id},
            )
        ).scalar_one()
        assert int(left) == 0

    with pytest.raises(ErasureError):
        await rollback_job(factory, Settings(), result.job_id, force_parallel=True, actor="test")

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_manual_purge_job_backup(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    login = "erase.manualpurge@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        cuid = await _insert_customer(session, login=login, customer_id="ERASE-MP")

    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="anonymize",
        seed=2,
        force_parallel=True,
        actor="test",
    )
    out = await purge_job_backup(factory, result.job_id, actor="test")
    assert out["deleted_backups"] >= 1
    async with factory() as session:
        status = (
            await session.execute(
                text("SELECT status FROM tiqora_gdpr_job WHERE id = :id"),
                {"id": result.job_id},
            )
        ).scalar_one()
        assert status == "purged"
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_inactive_since_activity(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    login = "erase.inactive@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        cuid = await _insert_customer(session, login=login, customer_id="ERASE-INACT")
        # Ticket with old change_time — already inactive after 2025-01-01.
        t = Ticket(
            tn="2026010199990001",
            title="old",
            queue_id=1,
            ticket_lock_id=1,
            user_id=1,
            responsible_user_id=1,
            ticket_priority_id=3,
            ticket_state_id=2,
            customer_id="ERASE-INACT",
            customer_user_id=login,
            timeout=0,
            until_time=0,
            escalation_time=0,
            escalation_update_time=0,
            escalation_response_time=0,
            escalation_solution_time=0,
            create_time=datetime(2024, 1, 1),
            create_by=1,
            change_time=datetime(2024, 6, 1),
            change_by=1,
        )
        async with session.begin():
            session.add(t)

    async with factory() as session:
        ids = await resolve_selector(
            session,
            ErasureSelector(logins=[login], activity="inactive_since:2025-01-01"),
        )
        assert ids == [cuid]
        ids = await resolve_selector(
            session,
            ErasureSelector(logins=[login], activity="inactive_since:2024-01-01"),
        )
        assert ids == []

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_extra_pii_setting_and_introspection(mariadb_znuny_url: str) -> None:
    from tiqora.gdpr.erasure import resolve_customer_user_pii_columns

    engine, factory = await _factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        await session.execute(
            text(
                "INSERT INTO tiqora_settings (`key`, value) VALUES"
                " ('gdpr.customer_extra_pii_columns', :v)"
                " ON DUPLICATE KEY UPDATE value = VALUES(value)"
            ),
            {"v": json.dumps(["wpnum"])},
        )
        await session.commit()
        cols = await resolve_customer_user_pii_columns(session)
        assert "email" in cols
        assert "login" in cols
        # wpnum present after ALTER in seed
        assert "wpnum" in cols
    await engine.dispose()


async def _count(session: AsyncSession, sql: str, params: dict) -> int:
    return int((await session.execute(text(sql), params)).scalar_one())


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_tickets_hard_delete_and_rollback_restores(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """delete_tickets hard-deletes the ticket + all FK children (FK-safe) and
    rollback_job re-inserts everything."""
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    login = f"erase.del.{'my' if mysql else 'pg'}@example.com"
    company = f"ERASE-DEL-{'MY' if mysql else 'PG'}"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        # Idempotent on the shared DB: FK-safe cleanup of any leftover from a
        # prior run (rollback restores rows, so they persist otherwise).
        tick = "(SELECT id FROM ticket WHERE customer_user_id = :l)"
        art = f"(SELECT id FROM article WHERE ticket_id IN {tick})"
        async with session.begin():
            for stmt in (
                f"DELETE FROM article_search_index WHERE ticket_id IN {tick}",
                f"DELETE FROM article_data_mime_attachment WHERE article_id IN {art}",
                f"DELETE FROM article_data_mime_plain WHERE article_id IN {art}",
                f"DELETE FROM article_data_mime WHERE article_id IN {art}",
                f"DELETE FROM article WHERE ticket_id IN {tick}",
                "DELETE FROM ticket WHERE customer_user_id = :l",
                "DELETE FROM customer_user WHERE login = :l",
                "DELETE FROM customer_company WHERE customer_id = :c",
            ):
                await session.execute(text(stmt), {"l": login, "c": company})
        await _insert_company(session, customer_id=company, name="Acme Delete")
        cuid = await _insert_customer(session, login=login, customer_id=company, wpnum="WP-1")
        tid = await _insert_ticket(
            session, customer_user_id=login, customer_id=company, tn=f"2026DEL{cuid}"
        )
        aid = await _insert_article_bundle(
            session, ticket_id=tid, from_addr=login, body=f"Body for {login}"
        )

    child_counts_sql = {
        "ticket": ("SELECT COUNT(*) FROM ticket WHERE id = :t", {"t": tid}),
        "article": ("SELECT COUNT(*) FROM article WHERE ticket_id = :t", {"t": tid}),
        "article_data_mime": (
            "SELECT COUNT(*) FROM article_data_mime WHERE article_id = :a",
            {"a": aid},
        ),
        "article_data_mime_plain": (
            "SELECT COUNT(*) FROM article_data_mime_plain WHERE article_id = :a",
            {"a": aid},
        ),
        "article_data_mime_attachment": (
            "SELECT COUNT(*) FROM article_data_mime_attachment WHERE article_id = :a",
            {"a": aid},
        ),
        "article_search_index": (
            "SELECT COUNT(*) FROM article_search_index WHERE ticket_id = :t",
            {"t": tid},
        ),
    }

    # Baseline: every child row is present.
    async with factory() as session:
        for name, (sql, params) in child_counts_sql.items():
            assert await _count(session, sql, params) >= 1, f"{name} should exist pre-delete"
        orig_title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
        ).scalar_one()

    # Hard delete.
    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="delete",
        force_parallel=True,
        actor="test",
        selector=ErasureSelector(logins=[login]),
        delete_tickets=True,
    )
    assert result.job_id > 0

    # Everything gone (ticket + children + customer master).
    async with factory() as session:
        for name, (sql, params) in child_counts_sql.items():
            assert await _count(session, sql, params) == 0, f"{name} should be deleted"
        assert (
            await _count(session, "SELECT COUNT(*) FROM customer_user WHERE id = :id", {"id": cuid})
            == 0
        )

    # Rollback restores all rows.
    rb = await rollback_job(factory, Settings(), result.job_id, force_parallel=True, actor="test")
    assert rb["restored_rows"] >= 7  # ticket + 5 child tables + customer_user (+more)

    async with factory() as session:
        for name, (sql, params) in child_counts_sql.items():
            assert await _count(session, sql, params) >= 1, f"{name} should be restored"
        restored_title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
        ).scalar_one()
        assert restored_title == orig_title
        restored_cu = (
            await session.execute(
                text("SELECT customer_user_id FROM ticket WHERE id = :t"), {"t": tid}
            )
        ).scalar_one()
        assert restored_cu == login
        assert (
            await _count(session, "SELECT COUNT(*) FROM customer_user WHERE id = :id", {"id": cuid})
            == 1
        )

    await engine.dispose()


# ---------------------------------------------------------------------------
# build_customer_record_preview / admin record-preview + selector-count API
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_build_customer_record_preview_anonymize(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    login = f"rp.anon.{'my' if mysql else 'pg'}@example.com"
    company = f"RP-ANON-{'MY' if mysql else 'PG'}"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        await _insert_company(session, customer_id=company, name="Acme Preview")
        cuid = await _insert_customer(session, login=login, customer_id=company)
        tid = await _insert_ticket(
            session, customer_user_id=login, customer_id=company, tn=f"2026RPAN{cuid}"
        )
        await _insert_article_bundle(
            session,
            ticket_id=tid,
            from_addr=login,
            body=f"Private body for {login}",
        )

    async with factory() as session:
        # Full snapshot of every row this preview touches, taken *before* the
        # call, to prove the preview does not write anything.
        before_cu = dict(
            (
                await session.execute(
                    text("SELECT * FROM customer_user WHERE id = :id"), {"id": cuid}
                )
            )
            .mappings()
            .one()
        )
        before_ticket_title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
        ).scalar_one()
        before_mime_from = (
            await session.execute(
                text(
                    "SELECT a_from FROM article_data_mime"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()

        preview = await build_customer_record_preview(session, login, "anonymize", seed=42)

    assert preview.login == login
    assert preview.mode == "anonymize"
    assert preview.delete_summary == []

    by_field = {f.field: f for f in preview.fields}
    mapper = ValueMapper(seed=42)
    expected_login = mapper.map_value(login, "login")

    assert by_field["customer_user.login"].before == login
    assert by_field["customer_user.login"].after == expected_login
    assert by_field["customer_user.login"].changed is True

    valid_field = by_field["customer_user.valid_id"]
    assert valid_field.before == 1
    assert valid_field.after == 2
    assert valid_field.changed is True

    title_field = by_field["ticket.title"]
    assert title_field.before == "Erase me"
    assert title_field.changed is True
    assert title_field.occurrences == 1

    from_field = by_field["article_data_mime.a_from"]
    assert from_field.before == login
    assert login not in (from_field.after or "")
    assert from_field.occurrences == 1

    body_field = by_field["article_data_mime_plain.body"]
    assert f"Private body for {login}" in (body_field.before or "")
    assert login not in (body_field.after or "")
    assert body_field.occurrences == 1

    # Read-only: nothing in the DB actually changed.
    async with factory() as session:
        after_cu = dict(
            (
                await session.execute(
                    text("SELECT * FROM customer_user WHERE id = :id"), {"id": cuid}
                )
            )
            .mappings()
            .one()
        )
        after_ticket_title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
        ).scalar_one()
        after_mime_from = (
            await session.execute(
                text(
                    "SELECT a_from FROM article_data_mime"
                    " WHERE article_id IN (SELECT id FROM article WHERE ticket_id = :t)"
                ),
                {"t": tid},
            )
        ).scalar_one()
        assert after_cu == before_cu
        assert after_ticket_title == before_ticket_title
        assert after_mime_from == before_mime_from

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_build_customer_record_preview_delete_mode(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = await _factory(url)
    mysql = _is_mysql(url)
    login = f"rp.del.{'my' if mysql else 'pg'}@example.com"
    company = f"RP-DEL-{'MY' if mysql else 'PG'}"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=mysql)
        await _insert_company(session, customer_id=company, name="Acme Record Preview Delete")
        await _insert_customer(session, login=login, customer_id=company)
        tid = await _insert_ticket(session, customer_user_id=login, customer_id=company)
        await _insert_article_bundle(
            session, ticket_id=tid, from_addr=login, body="body to be deleted"
        )

    async with factory() as session:
        preview = await build_customer_record_preview(session, login, "delete")

    assert preview.login == login
    assert preview.mode == "delete"
    assert preview.fields == []
    by_table = {row.table: row.count for row in preview.delete_summary}
    assert by_table["customer_user"] == 1
    assert by_table["tickets"] == 1
    assert by_table["articles"] == 1

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_build_customer_record_preview_not_found(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)

    async with factory() as session:
        with pytest.raises(ErasureNotFoundError):
            await build_customer_record_preview(session, "does.not.exist@example.com")

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_admin_record_preview_endpoint_404(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)

    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await admin_gdpr.customer_record_preview(
                GdprCustomerRecordPreviewRequest(login="ghost@example.com"),
                _root_user(),
                session,
            )
        assert exc_info.value.status_code == 404

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_admin_record_preview_endpoint_anonymize(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    login = "rp.endpoint@example.com"
    company = "RP-ENDPOINT"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        await _insert_company(session, customer_id=company, name="Acme Endpoint")
        await _insert_customer(session, login=login, customer_id=company)

    async with factory() as session:
        out = await admin_gdpr.customer_record_preview(
            GdprCustomerRecordPreviewRequest(login=login, mode="anonymize", seed=7),
            _root_user(),
            session,
        )
        assert out.login == login
        assert out.mode == "anonymize"
        assert any(f.field == "customer_user.login" and f.changed for f in out.fields)

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_admin_selector_count_endpoint(mariadb_znuny_url: str) -> None:
    engine, factory = await _factory(mariadb_znuny_url)
    company = "SEL-COUNT"

    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=True)
        await _insert_customer(session, login="sc.a@example.com", customer_id=company)
        await _insert_customer(session, login="sc.b@example.com", customer_id=company)
        await _insert_customer(session, login="sc.c@example.com", customer_id="SEL-COUNT-OTHER")

    async with factory() as session:
        out = await admin_gdpr.selector_count(
            GdprSelectorCountRequest(selector=ErasureSelectorIn(customer_ids=[company])),
            _root_user(),
            session,
        )
        assert out.count == 2

        out_none = await admin_gdpr.selector_count(
            GdprSelectorCountRequest(selector=ErasureSelectorIn(customer_ids=["no-such-company"])),
            _root_user(),
            session,
        )
        assert out_none.count == 0

    await engine.dispose()
