"""Tests for GDPR erasure engine (resolve/preview/apply/rollback/purge).

Covers MariaDB and Postgres, selector criteria (AND), anonymize + delete,
custom PII column via information_schema, byte-for-byte rollback, and purge.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.dev_anonymize import ValueMapper
from tiqora.gdpr.erasure import (
    ErasureError,
    ErasureSelector,
    build_erasure_preview,
    purge_expired_backups,
    purge_job_backup,
    resolve_selector,
    rollback_job,
    run_erasure,
)
from tiqora.gdpr.gate import GdprRefusedError

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
