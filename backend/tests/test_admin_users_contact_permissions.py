"""DB tests for the admin user CRUD additions: email/mobile persistence via
``user_preferences``, auto-generated-password welcome mail, effective
group/queue permissions, and admin-editable agent language — following the
direct-router-call pattern used by ``test_admin_states.py``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import UserCreate, UserUpdate
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.schemas import UserLanguageUpdate
from tiqora.domain.welcome_mail import WelcomeMailError

pytestmark = pytest.mark.db

NOW = datetime(2024, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


# Logins created by `admin_users.create_user` across this module — deleted in
# `_cleanup_after_test` below so the module passes the strict DB-leak check
# (see conftest.py `_restore_db_between_modules`) on its own, not just via the
# module-scoped restore.
_TEST_LOGINS = [
    "contact.test",
    "autopw.test",
    "failmail.test",
    "update.contact",
    "lang.test",
]


def _delete_group_role_seed(conn: Any) -> None:
    conn.execute(text("DELETE FROM queue WHERE id IN (210, 211)"))
    conn.execute(text("DELETE FROM group_user WHERE user_id = 200 OR group_id IN (20, 21)"))
    conn.execute(text("DELETE FROM group_role WHERE role_id IN (60, 61) OR group_id IN (20, 21)"))
    conn.execute(text("DELETE FROM role_user WHERE user_id = 200 OR role_id IN (60, 61)"))
    conn.execute(text("DELETE FROM roles WHERE id IN (60, 61)"))
    conn.execute(text("DELETE FROM permission_groups WHERE id IN (20, 21)"))
    conn.execute(text("DELETE FROM users WHERE id = 200"))


@pytest.fixture(autouse=True)
def _cleanup_after_test(mariadb_znuny_url: str) -> Any:
    """Delete everything this module's tests commit, so the module-scoped
    restore in conftest.py finds nothing left over (``TIQORA_STRICT_DB_LEAKS``
    fails a module that relies on the restore to clean up after it)."""
    yield
    engine = create_engine(mariadb_znuny_url)
    try:
        with engine.begin() as conn:
            # `tiqora_*` tables are created (not restored) by conftest.py's
            # snapshot/restore, so any row added by this module is a leak
            # unless removed here — every admin-mutating call queues one.
            conn.execute(text("DELETE FROM tiqora_cache_invalidation"))
            rows = conn.execute(
                text("SELECT id FROM users WHERE login IN :logins").bindparams(
                    bindparam("logins", expanding=True)
                ),
                {"logins": _TEST_LOGINS},
            ).all()
            ids = [row[0] for row in rows]
            if ids:
                conn.execute(
                    text("DELETE FROM user_preferences WHERE user_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
                conn.execute(
                    text("DELETE FROM users WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
            _delete_group_role_seed(conn)
    finally:
        engine.dispose()


def _seed_group_role(sync_url: str) -> dict[str, Any]:
    """Direct + role-derived group permissions on a fresh agent, for the
    effective-permissions endpoint. Mirrors ``test_permissions.py``'s seed."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _delete_group_role_seed(conn)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (200, 'agent.effective', 'x', 'Ef', 'Fective', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (20, 'group-effective', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (200, 20, 'ro', :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO roles (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (60, 'effective-role', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO role_user (user_id, role_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (200, 60, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_role (role_id, group_id, permission_key, permission_value,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (60, 20, 'rw', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (210, 'effective-queue', 20, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )

        # Invalid counterparts: an invalid group held directly, an invalid role
        # granting on the *valid* group, and a valid queue inside the invalid
        # group. None of these grant anything, but the admin view lists them.
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (21, 'group-effective-dead', 2, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (200, 21, 'rw', :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO roles (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (61, 'effective-role-dead', 2, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO role_user (user_id, role_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (200, 61, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_role (role_id, group_id, permission_key, permission_value,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (61, 20, 'note', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (211, 'effective-queue-dead-group', 21, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
    engine.dispose()
    return {
        "user_id": 200,
        "group_id": 20,
        "role_id": 60,
        "queue_id": 210,
        "dead_group_id": 21,
        "dead_role_id": 61,
        "dead_queue_id": 211,
    }


async def test_create_user_persists_email_and_mobile(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_users.create_user(
                UserCreate(
                    login="contact.test",
                    password="s3cret-pw",
                    first_name="Con",
                    last_name="Tact",
                    email="contact@example.test",
                    mobile="+49 151 000000",
                ),
                _root_user(),
                session,
            )
            assert created.email == "contact@example.test"
            assert created.mobile == "+49 151 000000"

            fetched = await admin_users.get_user(created.id, _root_user(), session)
            assert fetched.email == "contact@example.test"
            assert fetched.mobile == "+49 151 000000"

            page = await admin_users.list_users(
                _root_user(),
                session,
                ListParams(page=1, page_size=50, valid="valid"),
            )
            listed = next(u for u in page.items if u.id == created.id)
            assert listed.email == "contact@example.test"
    finally:
        await engine.dispose()


async def test_create_user_without_password_requires_email() -> None:
    with pytest.raises(ValidationError):
        UserCreate(login="nopw", first_name="No", last_name="Pw")


async def test_create_user_auto_generates_and_emails_password(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sent: dict[str, Any] = {}

    async def fake_send(session: Any, *, to_addr: str, subject: str, body: str) -> None:
        sent["to_addr"] = to_addr
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(admin_users, "send_transactional_email", fake_send)

    try:
        async with factory() as session:
            created = await admin_users.create_user(
                UserCreate(
                    login="autopw.test",
                    first_name="Auto",
                    last_name="Pw",
                    email="autopw@example.test",
                ),
                _root_user(),
                session,
            )
            assert created.email == "autopw@example.test"
            assert sent["to_addr"] == "autopw@example.test"
            assert created.login in sent["body"]
    finally:
        await engine.dispose()


async def test_create_user_welcome_mail_failure_returns_502(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def failing_send(session: Any, *, to_addr: str, subject: str, body: str) -> None:
        raise WelcomeMailError("smtp disabled")

    monkeypatch.setattr(admin_users, "send_transactional_email", failing_send)

    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_users.create_user(
                    UserCreate(
                        login="failmail.test",
                        first_name="Fail",
                        last_name="Mail",
                        email="failmail@example.test",
                    ),
                    _root_user(),
                    session,
                )
            assert exc_info.value.status_code == 502

            # The user row was still created — only the mail failed.
            result = await session.execute(
                text("SELECT id FROM users WHERE login = 'failmail.test'")
            )
            assert result.scalar_one_or_none() is not None
    finally:
        await engine.dispose()


async def test_update_user_email_overwrite_and_clear(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_users.create_user(
                UserCreate(
                    login="update.contact",
                    password="s3cret-pw",
                    first_name="Up",
                    last_name="Date",
                    email="old@example.test",
                ),
                _root_user(),
                session,
            )

            updated = await admin_users.update_user(
                created.id, UserUpdate(email="new@example.test"), _root_user(), session
            )
            assert updated.email == "new@example.test"

            cleared = await admin_users.update_user(
                created.id, UserUpdate(email=""), _root_user(), session
            )
            assert cleared.email is None
    finally:
        await engine.dispose()


async def test_effective_permissions_reports_direct_and_role_sources(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    ids = _seed_group_role(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await admin_users.get_effective_permissions(
                ids["user_id"], _root_user(), session
            )
            assert any(r.id == ids["role_id"] for r in result.roles)

            group = next(g for g in result.groups if g.group_id == ids["group_id"])
            assert group.valid_id == 1
            # "note" comes from the invalid role and is configured but not in
            # force; it is still listed, flagged via its source's valid_id.
            assert set(group.keys) == {"ro", "rw", "note"}
            vias = {s.via for s in group.sources}
            assert "direct" in vias
            assert any(v.startswith("Rolle:") for v in vias)

            queue = next(q for q in result.queues if q.queue_id == ids["queue_id"])
            assert queue.group_id == ids["group_id"]
            assert queue.valid_id == 1
            assert queue.group_valid_id == 1
            assert set(queue.keys) == {"ro", "rw", "note"}
    finally:
        await engine.dispose()


async def test_effective_permissions_lists_invalid_resources_flagged(
    mariadb_znuny_url: str,
) -> None:
    """Invalid groups/roles/queues are returned rather than dropped, so the
    admin UI can offer a valid/invalid/all filter over them."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    ids = _seed_group_role(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await admin_users.get_effective_permissions(
                ids["user_id"], _root_user(), session
            )

            dead_role = next(r for r in result.roles if r.id == ids["dead_role_id"])
            assert dead_role.valid_id == 2

            dead_group = next(g for g in result.groups if g.group_id == ids["dead_group_id"])
            assert dead_group.valid_id == 2

            # The valid group carries one source from the invalid role.
            group = next(g for g in result.groups if g.group_id == ids["group_id"])
            note_sources = [s for s in group.sources if s.key == "note"]
            assert note_sources and all(s.valid_id == 2 for s in note_sources)
            assert any(s.valid_id == 1 for s in group.sources)

            # A valid queue inside an invalid group: the queue is fine, the
            # permission is not.
            dead_queue = next(q for q in result.queues if q.queue_id == ids["dead_queue_id"])
            assert dead_queue.valid_id == 1
            assert dead_queue.group_valid_id == 2
    finally:
        await engine.dispose()


async def test_language_get_set_roundtrip(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_users.create_user(
                UserCreate(
                    login="lang.test",
                    password="s3cret-pw",
                    first_name="Lang",
                    last_name="Test",
                ),
                _root_user(),
                session,
            )

            initial = await admin_users.get_user_language(created.id, _root_user(), session)
            assert initial.language is None

            updated = await admin_users.set_user_language(
                created.id, UserLanguageUpdate(language="pt-br"), _root_user(), session
            )
            assert updated.language == "pt_BR"

            fetched = await admin_users.get_user_language(created.id, _root_user(), session)
            assert fetched.language == "pt_BR"

            with pytest.raises(HTTPException) as exc_info:
                await admin_users.set_user_language(
                    created.id, UserLanguageUpdate(language="not-a-lang"), _root_user(), session
                )
            assert exc_info.value.status_code == 422
    finally:
        await engine.dispose()
