"""Visitor identity linking and its effect on chats (band 54420-54459, logins ``wc54``)."""

from __future__ import annotations

import calendar
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.config import Settings
from tiqora.crypto.secret import encrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.webchat.agent import ticket_chat_info
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.identity import (
    IdentityClaims,
    IdentityError,
    sign_identity_token,
)
from tiqora.webchat.models import ChatConfigValues, PrechatField
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import (
    ChatError,
    VisitorContext,
    create_session,
    end_by_visitor,
    submit_offline,
)
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.webchat.visitor_identity import (
    ResolvedIdentity,
    identify_session,
    issue_portal_assertion,
    resolve_identity,
    verify_and_resolve,
    website_secret,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, AGENT = 54420, 54421, 54422
CONFIG, CONFIG_B, CONFIG_NOSECRET = 54425, 54426, 54427
CONFIG_IDS = range(54420, 54460)
KEY, KEY_B, KEY_NOSECRET = "wc_" + "g" * 32, "wc_" + "h" * 32, "wc_" + "i" * 32
ORIGIN = "https://www.example.org"
VISITOR = "v" * 22
SECRET_KEY = "unit-test-secret-key"
WEBSITE_SECRET = "wc54-website-secret"
SETTINGS = Settings(secret_key=SECRET_KEY)
CREATE_EMAIL = "neu@kunde.test"
TAKEN_EMAIL = "taken@kunde.test"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef",
    user_agent="Mozilla/5.0 (Test)",
    page_url="https://www.example.org/kontakt",
    language="de",
)
REQUIRED_EMAIL_FIELDS = [
    PrechatField(key="name", kind="text", label="Name", required=True, maps_to="name"),
    PrechatField(key="email", kind="email", label="E-Mail", required=True, maps_to="email"),
]


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_band(
            conn, config_ids=CONFIG_IDS, queue_ids=[QUEUE], group_ids=[GROUP], user_ids=[AGENT]
        )
        conn.execute(
            text("DELETE FROM customer_user WHERE login LIKE 'wc54-%' OR login IN (:a, :b)"),
            {"a": CREATE_EMAIL, "b": TAKEN_EMAIL},
        )
        conn.execute(text("DELETE FROM customer_user WHERE email = 'dup@example.org'"))
    engine.dispose()


def _seed_customer(
    conn: Any, *, login: str, email: str, customer_id: str, first: str, last: str, valid: int = 1
) -> None:
    conn.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:l, :e, :c, NULL, :f, :s, :v, :t, 1, :t, 1)"
        ),
        {"l": login, "e": email, "c": customer_id, "f": first, "s": last, "v": valid, "t": NOW},
    )


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc54-agent")
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        insert_config(
            conn,
            config_id=CONFIG_B,
            queue_id=QUEUE,
            public_key=KEY_B,
            origins=[ORIGIN],
            values_override={"prechat_fields": REQUIRED_EMAIL_FIELDS},
        )
        insert_config(
            conn, config_id=CONFIG_NOSECRET, queue_id=QUEUE, public_key=KEY_NOSECRET,
            origins=[ORIGIN],
        )  # fmt: skip
        for config_id in (CONFIG, CONFIG_B):
            conn.execute(
                text("UPDATE tiqora_chat_config SET identity_secret = :s WHERE id = :i"),
                {"s": encrypt_secret(SECRET_KEY, WEBSITE_SECRET), "i": config_id},
            )
        _seed_customer(
            conn,
            login="wc54-erika",
            email="erika@example.org",
            customer_id="wc54-co",
            first="Erika",
            last="Muster",
        )
        for login in ("wc54-dup1", "wc54-dup2"):
            _seed_customer(
                conn,
                login=login,
                email="dup@example.org",
                customer_id="wc54-co",
                first="D",
                last="U",
            )
        _seed_customer(
            conn,
            login="wc54-gone",
            email=TAKEN_EMAIL,
            customer_id="wc54-co",
            first="Alt",
            last="Konto",
            valid=2,
        )
        enable_webchat(conn)
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    redis = FakeRedis()
    async with factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(redis, AGENT)
    yield Env(url, factory, redis)
    await aengine.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


# -- helpers -------------------------------------------------------------------------------


async def _config(env: Env, config_id: int = CONFIG) -> tuple[TiqoraChatConfig, ChatConfigValues]:
    async with env.factory() as db:
        cfg = await load_config(db, config_id)
        assert cfg is not None
        return cfg, config_values(cfg)


def _claims(**overrides: Any) -> IdentityClaims:
    data: dict[str, Any] = {
        "sub": None,
        "email": None,
        "name": None,
        "source": "website",
        "visitor_id": None,
    }
    data.update(overrides)
    return IdentityClaims(**data)


async def _resolve(env: Env, claims: IdentityClaims, **value_overrides: Any) -> ResolvedIdentity:
    _cfg, values = await _config(env)
    values = values.model_copy(update=value_overrides)
    async with env.factory() as db:
        resolved = await resolve_identity(db, claims=claims, values=values)
        await db.commit()
        return resolved


async def _erika(env: Env, source: str = "website") -> ResolvedIdentity:
    return await _resolve(env, _claims(sub="wc54-erika", source=source))


async def _start(
    env: Env,
    config_id: int = CONFIG,
    identity: ResolvedIdentity | None = None,
    prechat: dict[str, str | bool] | None = None,
) -> TiqoraChatSession:
    cfg, values = await _config(env, config_id)
    payload = ChatSessionCreateIn(
        key=cfg.public_key,
        origin=ORIGIN,
        visitor_id=VISITOR,
        prechat=prechat if prechat is not None else {},
        message="Hallo",
        client_id="c-1",
    )
    async with env.factory() as db:
        chat, _token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=values,
            payload=payload,
            ctx=CTX,
            now=NOW,
            identity=identity,
        )
        await db.commit()
        return chat


async def _identify(env: Env, chat: TiqoraChatSession, identity: ResolvedIdentity) -> bool:
    async with env.factory() as db:
        try:
            changed = await identify_session(
                db, SysConfig(db), chat=chat, identity=identity, now=NOW
            )
        except ChatError:
            await db.rollback()
            raise
        await db.commit()
        return changed


async def _rows(env: Env, sql: str, **params: Any) -> list[Any]:
    async with env.factory() as db:
        return list((await db.execute(text(sql), params)).all())


async def _ticket(env: Env, ticket_id: int) -> tuple[str | None, str | None]:
    rows = await _rows(
        env, "SELECT customer_id, customer_user_id FROM ticket WHERE id = :t", t=ticket_id
    )
    return rows[0][0], rows[0][1]


async def _customer_updates(env: Env, ticket_id: int) -> int:
    rows = await _rows(
        env,
        "SELECT COUNT(*) FROM ticket_history h"
        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
        " WHERE h.ticket_id = :t AND ht.name = 'CustomerUpdate'",
        t=ticket_id,
    )
    return int(rows[0][0])


# -- linking -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_sub_links_existing_customer(env: Env) -> None:
    r = await _resolve(env, _claims(sub="WC54-Erika"))
    assert (r.customer_user_login, r.customer_id, r.email, r.known_customer) == (
        "wc54-erika",
        "wc54-co",
        "erika@example.org",
        True,
    )
    assert r.name == "Erika Muster" and r.source == "website"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_email_links_unique_customer_only(env: Env) -> None:
    unique = await _resolve(env, _claims(email="erika@example.org", name="Erika M."))
    assert (unique.customer_user_login, unique.known_customer) == ("wc54-erika", True)
    assert unique.name == "Erika M."

    ambiguous = await _resolve(env, _claims(email="dup@example.org"))
    assert ambiguous.known_customer is False
    assert (ambiguous.customer_user_login, ambiguous.customer_id) == (
        "dup@example.org",
        "dup@example.org",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_unknown_email_without_create_switch(env: Env) -> None:
    r = await _resolve(env, _claims(email=CREATE_EMAIL, name="Max Beispiel"))
    assert (r.customer_user_login, r.customer_id, r.email, r.known_customer) == (
        CREATE_EMAIL,
        CREATE_EMAIL,
        CREATE_EMAIL,
        False,
    )
    assert await _rows(env, "SELECT id FROM customer_user WHERE login = :l", l=CREATE_EMAIL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_unknown_email_creates_customer_when_allowed(env: Env) -> None:
    r = await _resolve(
        env,
        _claims(email=CREATE_EMAIL, name="Max Beispiel"),
        identity_create_customers=True,
    )
    assert r.known_customer is True and r.customer_user_login == CREATE_EMAIL
    rows = await _rows(
        env,
        "SELECT login, email, customer_id, first_name, last_name, valid_id FROM customer_user"
        " WHERE login = :l",
        l=CREATE_EMAIL,
    )
    assert [tuple(x) for x in rows] == [
        (CREATE_EMAIL, CREATE_EMAIL, CREATE_EMAIL, "Max", "Beispiel", 1)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_create_switch_falls_back_when_email_in_use(env: Env) -> None:
    # The address belongs to an invalid account: Znuny refuses a second one, the chat
    # continues as an unknown customer instead of failing.
    r = await _resolve(
        env, _claims(email=TAKEN_EMAIL, name="Alt Konto"), identity_create_customers=True
    )
    assert r.known_customer is False
    assert (r.customer_user_login, r.customer_id, r.email) == (TAKEN_EMAIL,) * 3
    assert await _rows(env, "SELECT id FROM customer_user WHERE login = :l", l=TAKEN_EMAIL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_unknown_sub_without_email_free_text(env: Env) -> None:
    r = await _resolve(env, _claims(sub="nobody-wc54"), identity_create_customers=True)
    assert (r.customer_user_login, r.customer_id, r.email, r.known_customer) == (
        "nobody-wc54",
        "nobody-wc54",
        None,
        False,
    )
    assert await _rows(env, "SELECT id FROM customer_user WHERE login = 'nobody-wc54'") == []


# -- create_session -----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_create_session_with_identity_sets_verified_customer(env: Env) -> None:
    erika = await _erika(env)
    chat = await _start(
        env, identity=erika, prechat={"name": "Falsch", "email": "falsch@example.org"}
    )
    assert await _ticket(env, int(chat.ticket_id)) == ("wc54-co", "wc54-erika")
    assert chat.visitor_email == "erika@example.org" and chat.visitor_name == "Erika Muster"
    assert chat.identity_source == "website" and chat.customer_user_login == "wc54-erika"
    a_from = (
        await _rows(env, "SELECT a_from FROM article_data_mime WHERE article_id IN"
                    " (SELECT id FROM article WHERE ticket_id = :t)", t=int(chat.ticket_id))
    )[0][0]  # fmt: skip
    assert "erika@example.org" in a_from and "falsch@" not in a_from
    async with env.factory() as db:
        info = await ticket_chat_info(db, env.redis, ticket_id=int(chat.ticket_id), user_id=AGENT)
    assert info.visitor_email_verified is True and info.identity_source == "website"
    assert info.visitor_email == "erika@example.org"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_anonymous_chat_is_not_verified(env: Env) -> None:
    chat = await _start(env, prechat={"email": "anon@example.org"})
    assert chat.identity_source is None
    async with env.factory() as db:
        info = await ticket_chat_info(db, env.redis, ticket_id=int(chat.ticket_id), user_id=AGENT)
    assert info.visitor_email_verified is False and info.identity_source is None


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_required_prechat_email_satisfied_by_identity(env: Env) -> None:
    from tiqora.webchat.sessions import ChatError as SessionChatError

    with pytest.raises(SessionChatError) as excinfo:
        await _start(env, CONFIG_B)
    assert excinfo.value.code == "invalid_prechat"
    assert excinfo.value.errors == {"name": "required", "email": "required"}

    erika = await _erika(env)
    # Mapped answers are ignored even when they are invalid.
    chat = await _start(env, CONFIG_B, identity=erika, prechat={"email": "kein-mail"})
    assert chat.visitor_email == "erika@example.org" and chat.identity_source == "website"
    assert await _ticket(env, int(chat.ticket_id)) == ("wc54-co", "wc54-erika")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_offline_form_ignores_identity_path(env: Env) -> None:
    # submit_offline is unchanged: it keeps the typed address and never records an identity.
    cfg, values = await _config(env)
    from tiqora.webchat.schemas import ChatOfflineIn

    async with env.factory() as db:
        await submit_offline(
            db,
            env.factory,
            SysConfig(db),
            config=cfg,
            values=values.model_copy(update={"offline_mode": "form"}),
            payload=ChatOfflineIn(
                key=KEY, origin=ORIGIN, visitor_id=VISITOR, email="off@example.org", message="Hi"
            ),
            ctx=CTX,
            now=NOW,
        )
        await db.commit()
    rows = await _rows(
        env,
        "SELECT visitor_email, identity_source FROM tiqora_chat_session WHERE config_id = :c",
        c=CONFIG,
    )
    assert [tuple(r) for r in rows] == [("off@example.org", None)]


# -- identify_session ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_anonymous_session_sets_customer(env: Env) -> None:
    chat = await _start(env, prechat={"email": "anon@example.org"})
    ticket_id = int(chat.ticket_id)
    assert await _ticket(env, ticket_id) == ("anon@example.org", "anon@example.org")
    before = await _customer_updates(env, ticket_id)

    assert await _identify(env, chat, await _erika(env, "portal")) is True

    assert await _ticket(env, ticket_id) == ("wc54-co", "wc54-erika")
    assert await _customer_updates(env, ticket_id) == before + 1
    async with env.factory() as db:
        row = await get_chat_session(db, chat.id)
    assert row is not None
    assert (row.identity_source, row.customer_user_login) == ("portal", "wc54-erika")
    assert (row.visitor_email, row.visitor_name) == ("erika@example.org", "Erika Muster")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_without_email_drops_typed_address(env: Env) -> None:
    chat = await _start(env, prechat={"email": "anon@example.org"})
    sub_only = await _resolve(env, _claims(sub="wc54-unknown-sub"))
    assert sub_only.email is None
    assert await _identify(env, chat, sub_only) is True
    async with env.factory() as db:
        row = await get_chat_session(db, chat.id)
        info = await ticket_chat_info(db, env.redis, ticket_id=int(chat.ticket_id), user_id=AGENT)
    assert row is not None
    assert row.identity_source == "website" and row.visitor_email is None
    assert info.visitor_email_verified is False


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_keeps_customer_an_agent_changed(env: Env) -> None:
    chat = await _start(env, prechat={"email": "anon@example.org"})
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET customer_user_id = 'agent-set' WHERE id = :t"),
            {"t": int(chat.ticket_id)},
        )
        await db.commit()
    assert await _identify(env, chat, await _erika(env)) is True
    assert (await _ticket(env, int(chat.ticket_id)))[1] == "agent-set"
    async with env.factory() as db:
        row = await get_chat_session(db, chat.id)
    assert row is not None and row.identity_source == "website"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_same_identity_is_noop(env: Env) -> None:
    erika = await _erika(env)
    chat = await _start(env, identity=erika)
    ticket_id = int(chat.ticket_id)
    updates = await _customer_updates(env, ticket_id)
    assert await _identify(env, chat, erika) is False
    assert await _customer_updates(env, ticket_id) == updates
    assert await _ticket(env, ticket_id) == ("wc54-co", "wc54-erika")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_conflict_on_other_verified_identity(env: Env) -> None:
    chat = await _start(env, identity=await _erika(env))
    other = await _resolve(env, _claims(email="other@example.org", name="Otto"))
    with pytest.raises(ChatError) as excinfo:
        await _identify(env, chat, other)
    assert (excinfo.value.code, excinfo.value.http_status) == ("identity_conflict", 409)
    async with env.factory() as db:
        row = await get_chat_session(db, chat.id)
    assert row is not None
    assert (row.customer_user_login, row.visitor_email) == ("wc54-erika", "erika@example.org")
    assert await _ticket(env, int(chat.ticket_id)) == ("wc54-co", "wc54-erika")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_identify_ended_session_refused(env: Env) -> None:
    chat = await _start(env, prechat={"email": "anon@example.org"})
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW + timedelta(seconds=5))
        await db.commit()
    with pytest.raises(ChatError) as excinfo:
        await _identify(env, chat, await _erika(env))
    assert (excinfo.value.code, excinfo.value.http_status) == ("session_closed", 409)
    assert await _ticket(env, int(chat.ticket_id)) == ("anon@example.org", "anon@example.org")
    async with env.factory() as db:
        row = await get_chat_session(db, chat.id)
    assert row is not None and row.identity_source is None


# -- tokens end to end --------------------------------------------------------------------


def _erika_customer() -> AuthenticatedCustomer:
    return AuthenticatedCustomer(
        id=1,
        login="wc54-erika",
        email="erika@example.org",
        customer_id="wc54-co",
        first_name="Erika",
        last_name="Muster",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_portal_assertion_bound_to_visitor_and_chat(env: Env) -> None:
    cfg, values = await _config(env)
    cfg_b, _values_b = await _config(env, CONFIG_B)
    token = issue_portal_assertion(
        _erika_customer(), public_key=cfg.public_key, visitor_id=VISITOR, settings=SETTINGS, now=NOW
    )

    async def verify(config: TiqoraChatConfig, visitor_id: str) -> ResolvedIdentity:
        async with env.factory() as db:
            return await verify_and_resolve(
                db,
                SETTINGS,
                config=config,
                values=values,
                token=token,
                visitor_id=visitor_id,
                now=NOW,
            )

    with pytest.raises(IdentityError) as other_visitor:
        await verify(cfg, "w" * 22)
    assert other_visitor.value.code == "visitor"
    with pytest.raises(IdentityError) as other_chat:
        await verify(cfg_b, VISITOR)
    assert other_chat.value.code == "audience"

    resolved = await verify(cfg, VISITOR)
    assert resolved.source == "portal" and resolved.known_customer is True
    assert (resolved.customer_user_login, resolved.customer_id) == ("wc54-erika", "wc54-co")

    expired = NOW + timedelta(seconds=700)
    async with env.factory() as db:
        with pytest.raises(IdentityError) as late:
            await verify_and_resolve(
                db,
                SETTINGS,
                config=cfg,
                values=values,
                token=token,
                visitor_id=VISITOR,
                now=expired,
            )
    assert late.value.code == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_website_token_verified_with_stored_secret(env: Env) -> None:
    cfg, values = await _config(env)
    assert website_secret(cfg, SETTINGS) == WEBSITE_SECRET
    assert website_secret(cfg, Settings(secret_key="another-secret-key")) is None
    now = calendar.timegm(NOW.utctimetuple())
    token = sign_identity_token(
        {"sub": "wc54-erika", "aud": cfg.public_key, "exp": now + 600},
        WEBSITE_SECRET.encode(),
    )
    async with env.factory() as db:
        resolved = await verify_and_resolve(
            db,
            SETTINGS,
            config=cfg,
            values=values,
            token=token,
            visitor_id=VISITOR,
            now=NOW,
        )
    assert resolved.source == "website" and resolved.customer_user_login == "wc54-erika"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_website_secret_missing_rejects_website_tokens(env: Env) -> None:
    cfg, values = await _config(env, CONFIG_NOSECRET)
    assert website_secret(cfg, SETTINGS) is None
    now = calendar.timegm(NOW.utctimetuple())
    token = sign_identity_token({"sub": "wc54-erika", "exp": now + 600}, WEBSITE_SECRET.encode())
    async with env.factory() as db:
        with pytest.raises(IdentityError) as excinfo:
            await verify_and_resolve(
                db,
                SETTINGS,
                config=cfg,
                values=values,
                token=token,
                visitor_id=VISITOR,
                now=NOW,
            )
    assert excinfo.value.code == "signature"
