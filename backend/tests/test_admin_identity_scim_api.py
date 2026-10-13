"""Admin API for SCIM provisioning of an identity provider (both dialects).

ASGI + DB. Seed band 63390-63394 (agents), provider slugs use the ``sc63adm`` prefix.
63390 admin, 63391 plain agent, 63392 active SCIM agent, 63393 deactivated SCIM agent.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import NOW, FakeRedis, seed_agent
from tests._scim_helpers import scim_cleanup_statements, seed_scim_provider
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraScimGroup,
    TiqoraScimGroupMember,
    TiqoraScimPendingSync,
    TiqoraScimUser,
)
from tiqora.domain.auth import SessionStore
from tiqora.identity.scim.audit import record_event_in
from tiqora.identity.store import ProviderRecord

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-scim-helpers"  # = tests._scim_helpers.KEY
BAND = (63390, 63394)
ADMIN, PLAIN, AGENT_ON, AGENT_OFF = 63390, 63391, 63392, 63393
SLUG = "sc63adm"
BASE = "/api/v1/admin/identity-providers"
_URLS: set[str] = set()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in (
                *scim_cleanup_statements(slug_prefix=SLUG, login_like="sc63adm.%", user_ids=BAND),
                (
                    "DELETE FROM tiqora_identity_link WHERE provider_id IN"
                    " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
                    {},
                ),
                ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'", {}),
                (
                    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
                    {"lo": BAND[0], "hi": BAND[1]},
                ),
            ):
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Env:
    def __init__(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        provider: ProviderRecord,
        tokens: dict[int, str],
    ) -> None:
        self.client = client
        self.session = session
        self.provider = provider
        self.tokens = tokens
        self.url = f"{BASE}/{provider.id}/scim"

    def login_as(self, user_id: int | None) -> None:
        self.client.cookies.delete("tiqora_session")
        if user_id is not None:
            self.client.cookies.set("tiqora_session", self.tokens[user_id])

    async def token(self, name: str = "Entra", **kw: Any) -> httpx.Response:
        body = {"name": name, "user_kind": "agent", "expires_in_days": 30, **kw}
        return await self.client.post(f"{self.url}/tokens", json=body)

    async def seed_scim_user(
        self, scim_id: str, user_id: int, user_name: str, *, deleted: bool = False
    ) -> None:
        self.session.add(
            TiqoraScimUser(
                provider_id=self.provider.id,
                user_kind="agent",
                scim_id=scim_id,
                user_id=user_id,
                user_name=user_name,
                user_name_fold=user_name.casefold(),
                user_name_fold_hash=_sha(user_name.casefold()),
                external_id=f"ext-{user_id}",
                external_id_hash=_sha(f"ext-{user_id}"),
                deleted=deleted,
                version=1,
                created=NOW,
                changed=NOW,
            )
        )
        await self.session.commit()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        environment="test",
        secret_key=KEY,
        public_base_url="https://helpdesk.example.org",
        oidc_enabled=True,
        oidc_issuer="https://idp.example.org/realms/x",
        oidc_client_id="tiqora",
        oidc_client_secret="env-s3cr3t",
        oidc_claim="preferred_username",
        oidc_redirect_uri="https://helpdesk.example.org/api/v1/auth/oidc/callback",
    )
    app = create_app(settings)
    redis = FakeRedis()
    app.state.redis = redis

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with (
        maker() as session,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://helpdesk.example.org",
            headers={"Origin": "https://helpdesk.example.org"},
        ) as client,
    ):
        for uid, login, valid in (
            (ADMIN, "sc63adm.admin", 1),
            (PLAIN, "sc63adm.plain", 1),
            (AGENT_ON, "sc63adm.on", 1),
            (AGENT_OFF, "sc63adm.off", 2),
        ):
            await seed_agent(session, uid, login, valid_id=valid)
        await session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) SELECT :u, id, 'rw', :t, 1, :t, 1"
                " FROM permission_groups WHERE name = 'admin'"
            ),
            {"u": ADMIN, "t": NOW},
        )
        await session.commit()
        provider = await seed_scim_provider(session, audience="both", slug=f"{SLUG}-main")
        store = SessionStore(redis, settings)  # type: ignore[arg-type]
        tokens = {
            uid: await store.create(uid, login)
            for uid, login in ((ADMIN, "sc63adm.admin"), (PLAIN, "sc63adm.plain"))
        }
        e = Env(client, session, provider, tokens)
        e.login_as(ADMIN)
        yield e
    await engine.dispose()
    _clean(url)


# --------------------------------------------------------------------------- access


async def test_non_admin_forbidden(env: Env) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", env.url, None),
        ("POST", f"{env.url}/tokens", {"name": "x", "user_kind": "agent"}),
        ("DELETE", f"{env.url}/tokens/1", None),
        ("GET", f"{env.url}/accounts", None),
        ("GET", f"{env.url}/groups", None),
        ("GET", f"{env.url}/events", None),
    ]
    for user, expected in ((PLAIN, 403), (None, 401)):
        env.login_as(user)
        for method, path, body in calls:
            resp = await env.client.request(method, path, json=body)
            assert resp.status_code == expected, (method, path, resp.status_code, resp.text)
    env.login_as(ADMIN)
    assert (await env.client.get(env.url)).json()["tokens"] == []  # nothing was created


async def test_unknown_provider_is_404(env: Env) -> None:
    assert (await env.client.get(f"{BASE}/999999/scim")).status_code == 404
    assert (await env.client.get(f"{BASE}/999999/scim/accounts")).status_code == 404


# --------------------------------------------------------------------------- status / tokens


async def test_status_for_env_provider_unavailable(env: Env) -> None:
    listing = await env.client.get(BASE)
    assert listing.status_code == 200, listing.text
    env_row = next(p for p in listing.json() if p["source"] == "env")
    resp = await env.client.get(f"{BASE}/{env_row['id']}/scim")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"]
    assert body["tokens"] == []
    assert body["users"] == 0
    created = await env.client.post(
        f"{BASE}/{env_row['id']}/scim/tokens",
        json={"name": "x", "user_kind": "agent", "expires_in_days": 30},
    )
    assert created.status_code == 422
    assert "provider" in created.json()["detail"]["errors"]


async def test_status_of_enabled_provider(env: Env) -> None:
    body = (await env.client.get(env.url)).json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["enabled"] is True
    assert body["adopt_existing"] is False
    assert body["base_url"] == "https://helpdesk.example.org/scim/v2"
    assert (body["users"], body["active_users"], body["groups"], body["pending_sync"]) == (
        0,
        0,
        0,
        0,
    )
    assert body["last_activity_at"] is None


async def test_create_token_shows_secret_once(env: Env) -> None:
    resp = await env.token()
    assert resp.status_code == 201, resp.text
    created = resp.json()
    raw = created["token"]
    assert raw.startswith("tqscim_")
    assert created["record"]["token_hint"] == raw[-4:]
    assert created["record"]["created_by_name"] == "Idp Test"
    assert created["record"]["active"] is True
    status_body = (await env.client.get(env.url)).json()
    assert len(status_body["tokens"]) == 1
    assert status_body["tokens"][0]["token_hint"] == raw[-4:]
    assert raw not in (await env.client.get(env.url)).text
    assert "token" not in status_body["tokens"][0]
    events = (await env.client.get(f"{env.url}/events")).json()
    assert [(e["operation"], e["method"], e["resource"]) for e in events] == [
        ("token_create", "ADMIN", "Token")
    ]


async def test_create_token_validation_errors(env: Env) -> None:
    bad = await env.token(name="  ")
    assert bad.status_code == 422
    assert "name" in bad.json()["detail"]["errors"]
    too_long = await env.token(expires_in_days=100000)
    assert too_long.status_code == 422
    assert "expires_in_days" in too_long.json()["detail"]["errors"]
    for i in range(5):
        assert (await env.token(name=f"t{i}")).status_code == 201
    sixth = await env.token(name="t6")
    assert sixth.status_code == 422
    assert "tokens" in sixth.json()["detail"]["errors"]


async def test_revoke_token(env: Env) -> None:
    created = (await env.token()).json()
    token_id = created["record"]["id"]
    assert (await env.client.delete(f"{env.url}/tokens/{token_id}")).status_code == 204
    assert (await env.client.delete(f"{env.url}/tokens/{token_id}")).status_code == 204
    tokens = (await env.client.get(env.url)).json()["tokens"]
    assert tokens[0]["active"] is False
    assert tokens[0]["revoked_at"] is not None
    events = (await env.client.get(f"{env.url}/events")).json()
    assert [e["operation"] for e in events] == ["token_revoke", "token_create"]  # idempotent
    assert (await env.client.delete(f"{env.url}/tokens/999999")).status_code == 404


# --------------------------------------------------------------------------- lists


async def test_accounts_groups_events_lists(env: Env) -> None:
    await env.seed_scim_user("11111111-1111-4111-8111-111111111111", AGENT_ON, "Alice@example.org")
    await env.seed_scim_user("22222222-2222-4222-8222-222222222222", AGENT_OFF, "bob@example.org")
    await env.seed_scim_user(
        "33333333-3333-4333-8333-333333333333", 63399, "gone@example.org", deleted=True
    )
    ids = list(
        (
            await env.session.scalars(
                select(TiqoraScimUser.id)
                .where(TiqoraScimUser.provider_id == env.provider.id)
                .order_by(TiqoraScimUser.id)
            )
        ).all()
    )
    group = TiqoraScimGroup(
        provider_id=env.provider.id,
        scim_id="44444444-4444-4444-8444-444444444444",
        display_name="Support",
        display_name_fold="support",
        display_name_fold_hash=_sha("support"),
        external_id=None,
        external_id_hash=None,
        version=1,
        created=NOW,
        changed=NOW,
    )
    env.session.add(group)
    await env.session.flush()
    for member_id in ids[:2]:
        env.session.add(TiqoraScimGroupMember(group_id=group.id, member_id=member_id))
    env.session.add(
        TiqoraScimPendingSync(
            provider_id=env.provider.id,
            user_id=AGENT_ON,
            queued_at=NOW,
            generation=1,
        )
    )
    await record_event_in(
        env.session,
        provider_id=env.provider.id,
        token_id=None,
        method="POST",
        resource="Users",
        operation="create",
        status=201,
        scim_id="11111111-1111-4111-8111-111111111111",
        user_kind="agent",
        user_id=AGENT_ON,
    )
    await record_event_in(
        env.session,
        provider_id=env.provider.id,
        token_id=None,
        method="PATCH",
        resource="Users",
        operation="rejected",
        status=400,
        user_kind="agent",
        user_id=AGENT_OFF,
        detail="invalidValue",
    )
    await env.session.commit()

    default = (await env.client.get(f"{env.url}/accounts")).json()
    assert default["total"] == 2  # the deleted mapping is hidden
    by_login = {i["login"]: i for i in default["items"]}
    assert by_login["sc63adm.on"]["active"] is True
    assert by_login["sc63adm.on"]["user_name"] == "Alice@example.org"
    assert by_login["sc63adm.on"]["external_id"] == f"ext-{AGENT_ON}"
    assert by_login["sc63adm.off"]["active"] is False
    with_deleted = (
        await env.client.get(f"{env.url}/accounts", params={"include_deleted": True})
    ).json()
    assert with_deleted["total"] == 3
    gone = next(i for i in with_deleted["items"] if i["deleted"])
    assert gone["login"] is None  # no such account any more
    assert gone["active"] is False

    # q: stored userName (case-insensitive, exact) or a part of the local login
    by_name = (
        await env.client.get(f"{env.url}/accounts", params={"q": "ALICE@example.org"})
    ).json()
    assert [i["login"] for i in by_name["items"]] == ["sc63adm.on"]
    by_part = (await env.client.get(f"{env.url}/accounts", params={"q": "63adm.of"})).json()
    assert [i["login"] for i in by_part["items"]] == ["sc63adm.off"]
    # final review (T18 M1): a part of the login matches case-insensitively on both dialects
    upper = (await env.client.get(f"{env.url}/accounts", params={"q": "63ADM.OF"})).json()
    assert [i["login"] for i in upper["items"]] == ["sc63adm.off"]
    assert (await env.client.get(f"{env.url}/accounts", params={"q": "nobody"})).json()[
        "total"
    ] == 0
    paged = (await env.client.get(f"{env.url}/accounts", params={"limit": 1, "offset": 1})).json()
    assert paged["total"] == 2
    assert len(paged["items"]) == 1
    assert (await env.client.get(f"{env.url}/accounts", params={"limit": 201})).status_code == 422

    groups = (await env.client.get(f"{env.url}/groups")).json()
    assert groups["total"] == 1
    assert groups["items"][0]["display_name"] == "Support"
    assert groups["items"][0]["member_count"] == 2

    events = (await env.client.get(f"{env.url}/events")).json()
    assert [e["operation"] for e in events] == ["rejected", "create"]  # newest first
    assert events[1]["login"] == "sc63adm.on"
    assert events[0]["login"] == "sc63adm.off"
    failures = (await env.client.get(f"{env.url}/events", params={"failures_only": True})).json()
    assert [e["status"] for e in failures] == [400]
    older = (
        await env.client.get(f"{env.url}/events", params={"before_id": events[0]["id"]})
    ).json()
    assert [e["operation"] for e in older] == ["create"]

    status_body = (await env.client.get(env.url)).json()
    assert status_body["users"] == 2
    assert status_body["active_users"] == 1
    assert status_body["groups"] == 1
    assert status_body["pending_sync"] == 1
    assert status_body["last_activity_at"] is not None
