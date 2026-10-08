"""Identity provider store: encrypted secrets, validation, env read-only (wave 1, Task 3).

Seed band 47000-47099 (role/group ids). Provider slugs use the ``idptest-`` prefix.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.identity.config_models import (
    JitDefaults,
    OidcProviderConfig,
    RoleRule,
    SamlProviderConfig,
)
from tiqora.identity.store import (
    ProviderChanges,
    ProviderReadOnly,
    ProviderValidationError,
    _insert_env_row,
    create_provider,
    delete_provider,
    get_provider,
    get_provider_by_slug,
    list_enabled,
    list_providers,
    update_provider,
)
from tiqora.settings_sections.section import SECRET_MASK

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 8, 12, 0, 0)
KEY = "unit-test-secret-key-for-identity-store"
OTHER_KEY = "a-completely-different-secret-key-0000"
ROLE_ID = 47001
GROUP_ID = 47002
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'idptest-%')",
            {},
        ),
        (
            "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'idptest-%')",
            {},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'idptest-%'", {}),
        ("DELETE FROM roles WHERE id = :id", {"id": ROLE_ID}),
        ("DELETE FROM permission_groups WHERE id = :id", {"id": GROUP_ID}),
    )


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in _cleanup_statements():
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


def _seed(url: str) -> None:
    _clean(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        for table, rid, name in (
            ("roles", ROLE_ID, "idptest-role"),
            ("permission_groups", GROUP_ID, "idptest-group"),
        ):
            conn.execute(
                text(
                    f"INSERT INTO {table} (id, name, valid_id, create_time, create_by,"  # noqa: S608
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": rid, "n": name, "t": NOW},
            )
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _seed(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _clean(url)


async def _raw(session: AsyncSession, slug: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT config, secrets_enc, source FROM tiqora_identity_provider"
                    " WHERE slug = :s"
                ),
                {"s": slug},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _create(session: AsyncSession, preset: str, slug: str, **kw: Any) -> Any:
    rec = await create_provider(
        session,
        secret_key=KEY,
        actor_id=1,
        preset=preset,
        name="Test",
        slug=slug,
        audience=kw.get("audience", "agent"),
    )
    await session.commit()
    return rec


@pytest.mark.asyncio
async def test_create_saml_stores_private_key_only_encrypted(session: AsyncSession) -> None:
    rec = await _create(session, "generic_saml", "idptest-saml")
    assert rec.enabled is False and rec.sort == 100 and rec.source == "db"
    assert rec.secrets_set == frozenset({"sp_private_key"})
    raw = await _raw(session, "idptest-saml")
    cfg = json.loads(raw["config"])
    assert "sp_private_key" not in cfg
    assert "BEGIN CERTIFICATE" in cfg["sp_certificate"]
    secrets = json.loads(decrypt_secret(KEY, raw["secrets_enc"]) or "{}")
    assert "BEGIN PRIVATE KEY" in secrets["sp_private_key"]
    again = await get_provider_by_slug(session, "idptest-saml", secret_key=KEY)
    assert again is not None
    assert isinstance(again.config, SamlProviderConfig)
    assert again.config.sp_private_key is not None
    assert "BEGIN PRIVATE KEY" in again.config.sp_private_key.get_secret_value()
    assert (await get_provider(session, rec.id, secret_key=KEY)) == again


@pytest.mark.asyncio
async def test_create_oidc_has_no_secrets_enc(session: AsyncSession) -> None:
    rec = await _create(session, "generic_oidc", "idptest-oidc")
    assert rec.kind == "oidc" and rec.secrets_set == frozenset()
    assert (await _raw(session, "idptest-oidc"))["secrets_enc"] is None
    assert rec.preset == "generic_oidc" and rec.button_icon == "key"


@pytest.mark.asyncio
async def test_update_secret_mask_leaves_secrets_enc_identical(session: AsyncSession) -> None:
    rec = await _create(session, "generic_oidc", "idptest-mask")
    await update_provider(
        session,
        rec.id,
        ProviderChanges(config={"client_secret": "s3cret"}),
        secret_key=KEY,
        actor_id=1,
    )
    await session.commit()
    before = (await _raw(session, "idptest-mask"))["secrets_enc"]
    assert before is not None
    out = await update_provider(
        session,
        rec.id,
        ProviderChanges(config={"client_secret": SECRET_MASK, "scopes": "openid"}),
        secret_key=KEY,
        actor_id=1,
    )
    await session.commit()
    assert (await _raw(session, "idptest-mask"))["secrets_enc"] == before
    assert isinstance(out.config, OidcProviderConfig) and out.config.scopes == "openid"
    assert out.config.client_secret is not None
    assert out.config.client_secret.get_secret_value() == "s3cret"
    cleared = await update_provider(
        session, rec.id, ProviderChanges(config={"client_secret": ""}), secret_key=KEY, actor_id=1
    )
    await session.commit()
    assert cleared.secrets_set == frozenset()
    assert (await _raw(session, "idptest-mask"))["secrets_enc"] is None


@pytest.mark.asyncio
async def test_update_rejects_unknown_role_and_group(session: AsyncSession) -> None:
    rec = await _create(session, "generic_oidc", "idptest-rules")
    with pytest.raises(ProviderValidationError) as exc:
        await update_provider(
            session,
            rec.id,
            ProviderChanges(
                role_rules=[RoleRule(attribute="groups", value="x", role_ids=[ROLE_ID, 47099])]
            ),
            secret_key=KEY,
            actor_id=1,
        )
    assert exc.value.errors == {"role_rules": "Unbekannte Rolle: 47099"}
    with pytest.raises(ProviderValidationError) as exc2:
        await update_provider(
            session,
            rec.id,
            ProviderChanges(jit_defaults=JitDefaults(initial_role_ids=[47098])),
            secret_key=KEY,
            actor_id=1,
        )
    assert "jit_defaults" in exc2.value.errors
    with pytest.raises(ProviderValidationError) as exc3:
        await update_provider(
            session,
            rec.id,
            ProviderChanges(
                role_rules=[
                    RoleRule(
                        attribute="groups",
                        value="x",
                        groups=[{"group_id": 47097}],  # type: ignore[list-item]
                    )
                ]
            ),
            secret_key=KEY,
            actor_id=1,
        )
    assert exc3.value.errors == {"role_rules": "Unbekannte Gruppe: 47097"}
    ok = await update_provider(
        session,
        rec.id,
        ProviderChanges(
            role_rules=[
                RoleRule(
                    attribute="groups",
                    value="x",
                    role_ids=[ROLE_ID],
                    groups=[{"group_id": GROUP_ID}],  # type: ignore[list-item]
                )
            ]
        ),
        secret_key=KEY,
        actor_id=1,
    )
    assert ok.role_rules[0].role_ids == [ROLE_ID]


@pytest.mark.asyncio
async def test_slug_validation(session: AsyncSession) -> None:
    for bad in ("env-oidc", "env-oidc\n", "login\n", "Bad Slug", "x", "login"):
        with pytest.raises(ProviderValidationError) as exc:
            await create_provider(
                session,
                secret_key=KEY,
                actor_id=1,
                preset="generic_oidc",
                name="T",
                slug=bad,
                audience="agent",
            )
        assert set(exc.value.errors) == {"slug"}
    await _create(session, "generic_oidc", "idptest-dup")
    with pytest.raises(ProviderValidationError) as exc:
        await create_provider(
            session,
            secret_key=KEY,
            actor_id=1,
            preset="generic_oidc",
            name="T",
            slug="idptest-dup",
            audience="agent",
        )
    assert exc.value.errors == {"slug": "Dieser Kurzname ist bereits vergeben."}
    other = await _create(session, "generic_oidc", "idptest-other")
    with pytest.raises(ProviderValidationError):
        await update_provider(
            session, other.id, ProviderChanges(slug="idptest-dup"), secret_key=KEY, actor_id=1
        )
    renamed = await update_provider(
        session, other.id, ProviderChanges(slug="idptest-other"), secret_key=KEY, actor_id=1
    )
    assert renamed.slug == "idptest-other"
    with pytest.raises(ProviderValidationError) as exc4:
        await create_provider(
            session,
            secret_key=KEY,
            actor_id=1,
            preset="nope",
            name="T",
            slug="idptest-np",
            audience="agent",
        )
    assert "preset" in exc4.value.errors


@pytest.mark.asyncio
async def test_env_rows_are_read_only(session: AsyncSession) -> None:
    rec = await _insert_env_row(
        session,
        secret_key=KEY,
        slug="idptest-env",
        kind="oidc",
        name="Env",
        audience="agent",
        config=OidcProviderConfig(issuer="https://idp.example.org"),
    )
    await session.commit()
    assert rec.read_only and rec.source == "env"
    with pytest.raises(ProviderReadOnly):
        await update_provider(
            session, rec.id, ProviderChanges(name="x"), secret_key=KEY, actor_id=1
        )
    with pytest.raises(ProviderReadOnly):
        await delete_provider(session, rec.id)
    assert (await get_provider(session, rec.id, secret_key=KEY)) is not None


@pytest.mark.asyncio
async def test_undecryptable_secret_is_treated_as_unset(session: AsyncSession) -> None:
    rec = await _create(session, "generic_oidc", "idptest-badkey")
    await session.execute(
        text("UPDATE tiqora_identity_provider SET secrets_enc = :e WHERE id = :i"),
        {"e": encrypt_secret(OTHER_KEY, json.dumps({"client_secret": "x"})), "i": rec.id},
    )
    await session.commit()
    got = await get_provider(session, rec.id, secret_key=KEY)
    assert got is not None and got.secrets_set == frozenset()
    assert isinstance(got.config, OidcProviderConfig) and got.config.client_secret is None


@pytest.mark.asyncio
async def test_list_enabled_and_delete(session: AsyncSession) -> None:
    a = await _create(session, "generic_oidc", "idptest-la", audience="agent")
    b = await _create(session, "generic_saml", "idptest-lb", audience="both")
    c = await _create(session, "generic_oidc", "idptest-lc", audience="customer")
    for r in (a, b):
        await update_provider(
            session, r.id, ProviderChanges(enabled=True), secret_key=KEY, actor_id=1
        )
    await session.commit()
    slugs = lambda recs: [r.slug for r in recs if r.slug.startswith("idptest-")]  # noqa: E731
    assert slugs(await list_enabled(session, secret_key=KEY)) == ["idptest-la", "idptest-lb"]
    assert slugs(await list_enabled(session, secret_key=KEY, user_kind="customer")) == [
        "idptest-lb"
    ]
    assert slugs(await list_enabled(session, secret_key=KEY, kind="saml")) == ["idptest-lb"]
    assert "idptest-lc" in slugs(await list_providers(session, secret_key=KEY))
    await delete_provider(session, c.id)
    await session.commit()
    assert await get_provider(session, c.id, secret_key=KEY) is None


@pytest.mark.asyncio
async def test_failed_update_does_not_persist_slug(session: AsyncSession) -> None:
    rec = await _create(session, "generic_oidc", "idptest-atomic")
    with pytest.raises(ProviderValidationError):
        await update_provider(
            session,
            rec.id,
            ProviderChanges(
                slug="idptest-atomic2",
                role_rules=[RoleRule(attribute="groups", value="x", role_ids=[47099])],
            ),
            secret_key=KEY,
            actor_id=1,
        )
    await session.commit()
    got = await get_provider(session, rec.id, secret_key=KEY)
    assert got is not None and got.slug == "idptest-atomic"


def test_ids_bounded_to_int32() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RoleRule(attribute="a", value="b", role_ids=[2**31])
    with pytest.raises(ValidationError):
        RoleRule(attribute="a", value="b", groups=[{"group_id": 0}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        JitDefaults(initial_role_ids=[2**40])
