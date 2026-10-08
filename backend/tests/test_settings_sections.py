"""Settings sections: schema subset validation (pure) and the DB-backed store."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

import pytest
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import create_engine, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.avatars.settings import AVATARS_SECTION
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import (
    KEY_AVATARS_FAVICON_ENABLED,
    KEY_AVATARS_GRAVATAR_ENABLED,
    get_setting,
    get_setting_bool,
    set_setting,
)
from tiqora.extensions import Registry, register_core
from tiqora.settings_sections.section import (
    SECRET_MASK,
    SETTINGS_SECTIONS,
    SettingsSection,
    UnsupportedSettingsSchema,
    validate_section_schema,
)
from tiqora.settings_sections.store import (
    SettingsValidationError,
    encode_setting_value,
    load_section_values,
    read_section_for_admin,
    save_section_values,
)

SECRET_KEY = "test-secret-key-for-settings-sections-32"


class _Demo(BaseModel):
    enabled: bool = False
    limit: int = Field(5, ge=1, le=10)
    mode: Literal["a", "b"] = "a"
    hosts: list[str] = Field(default_factory=list)
    token: SecretStr | None = None


DEMO = SettingsSection(key="test.demo", model=_Demo, title_key="t", i18n_prefix="p")


def test_storage_keys_and_secret_fields() -> None:
    assert DEMO.storage_key("limit") == "test.demo.limit"
    assert DEMO.secret_fields() == frozenset({"token"})


def test_unsupported_field_type_is_rejected() -> None:
    class _Bad(BaseModel):
        nested: dict[str, int] = Field(default_factory=dict)

    with pytest.raises(UnsupportedSettingsSchema, match="nested"):
        validate_section_schema(
            SettingsSection(key="x", model=_Bad, title_key="t", i18n_prefix="p")
        )


def test_field_without_default_is_rejected() -> None:
    class _NoDefault(BaseModel):
        name: str

    with pytest.raises(UnsupportedSettingsSchema, match="name"):
        validate_section_schema(
            SettingsSection(key="x", model=_NoDefault, title_key="t", i18n_prefix="p")
        )


def test_demo_section_fits_the_supported_schema_subset() -> None:
    validate_section_schema(DEMO)


def test_every_core_section_fits_the_supported_schema_subset() -> None:
    reg = Registry()
    register_core(reg)
    sections = reg.items(SETTINGS_SECTIONS)
    assert {s.key for s in sections} >= {"jobs", "avatars"}
    for section in sections:
        validate_section_schema(section)


def test_storage_keys_never_collide() -> None:
    import tiqora.domain.settings_store as legacy

    legacy_values = {v for k, v in vars(legacy).items() if k.startswith("KEY_")}
    reg = Registry()
    register_core(reg)
    seen: dict[str, str] = {}
    for section in reg.items(SETTINGS_SECTIONS):
        for name in section.model.model_fields:
            key = section.storage_key(name)
            assert key not in seen, (key, seen.get(key))
            seen[key] = f"{section.key}.{name}"
            if name not in section.storage_keys:
                assert key not in legacy_values, key


def test_encode_setting_value() -> None:
    assert encode_setting_value(True) == "1"
    assert encode_setting_value(False) == "0"
    assert encode_setting_value(7) == "7"
    assert encode_setting_value(["a", "b"]) == '["a", "b"]'


# --- DB ---------------------------------------------------------------------

pytestmark_db = pytest.mark.db


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


_URLS: list[str] = []
_AVATAR_KEYS = (KEY_AVATARS_GRAVATAR_ENABLED, KEY_AVATARS_FAVICON_ENABLED)


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            delete(TiqoraSettings).where(
                TiqoraSettings.key.like("test.demo.%") | TiqoraSettings.key.in_(_AVATAR_KEYS)
            )
        )
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in set(_URLS):
        _clean(url)
    _URLS.clear()


async def _session(request: pytest.FixtureRequest, url_fixture: str) -> tuple[AsyncSession, object]:
    url: str = request.getfixturevalue(url_fixture)
    _URLS.append(url)
    _clean(url)
    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


DB_PARAMS = pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])


async def _row(session: AsyncSession, key: str) -> str | None:
    session.expire_all()
    return await get_setting(session, key)


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_secret_mask_round_trip_does_not_rotate(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            await save_section_values(s, DEMO, {"token": "s3cret"}, secret_key=SECRET_KEY)
            await s.commit()
            stored = await _row(s, "test.demo.token")
            assert stored is not None and stored.startswith("gAAAA")
            assert "s3cret" not in stored

            values, secrets_set = await read_section_for_admin(s, DEMO)
            assert values["token"] == SECRET_MASK
            assert "token" in secrets_set

            await save_section_values(
                s, DEMO, {"token": SECRET_MASK, "limit": 6}, secret_key=SECRET_KEY
            )
            await s.commit()
            assert await _row(s, "test.demo.token") == stored
            assert await _row(s, "test.demo.limit") == "6"

            await save_section_values(s, DEMO, {"limit": 7}, secret_key=SECRET_KEY)
            await s.commit()
            assert await _row(s, "test.demo.token") == stored

            loaded = await load_section_values(s, DEMO, secret_key=SECRET_KEY)
            assert isinstance(loaded, _Demo)
            assert loaded.token is not None
            assert loaded.token.get_secret_value() == "s3cret"
            assert loaded.limit == 7

            await save_section_values(s, DEMO, {"token": ""}, secret_key=SECRET_KEY)
            await s.commit()
            assert await _row(s, "test.demo.token") is None
            values, secrets_set = await read_section_for_admin(s, DEMO)
            assert secrets_set == []
            assert values["token"] is None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_invalid_value_raises_and_writes_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            with pytest.raises(SettingsValidationError) as exc:
                await save_section_values(
                    s, DEMO, {"limit": 99, "enabled": True}, secret_key=SECRET_KEY
                )
            assert "limit" in exc.value.errors
            await s.commit()
            assert await _row(s, "test.demo.limit") is None
            assert await _row(s, "test.demo.enabled") is None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_unknown_field_is_rejected(url_fixture: str, request: pytest.FixtureRequest) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            with pytest.raises(SettingsValidationError) as exc:
                await save_section_values(s, DEMO, {"bogus": 1}, secret_key=SECRET_KEY)
            assert exc.value.errors == {"bogus": "Unbekanntes Feld"}
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_garbage_row_falls_back_to_default(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            s.add(TiqoraSettings(key="test.demo.limit", value="abc"))
            s.add(TiqoraSettings(key="test.demo.mode", value="b"))
            s.add(TiqoraSettings(key="test.demo.enabled", value="yes"))
            await s.commit()
            loaded = await load_section_values(s, DEMO, secret_key=SECRET_KEY)
            assert isinstance(loaded, _Demo)
            assert loaded.limit == 5
            assert loaded.mode == "b"  # other fields survive one garbage row
            assert loaded.enabled is True
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_save_does_not_commit(url_fixture: str, request: pytest.FixtureRequest) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            await save_section_values(s, DEMO, {"limit": 4}, secret_key=SECRET_KEY)
            assert await _row(s, "test.demo.limit") == "4"
            await s.rollback()
            assert await _row(s, "test.demo.limit") is None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.db
@pytest.mark.asyncio
@DB_PARAMS
async def test_avatars_section_reads_legacy_keys(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    session, engine = await _session(request, url_fixture)
    try:
        async with session as s:
            await set_setting(s, KEY_AVATARS_GRAVATAR_ENABLED, "0")
            loaded = await load_section_values(s, AVATARS_SECTION, secret_key=SECRET_KEY)
            assert loaded.model_dump()["gravatar_enabled"] is False
            assert loaded.model_dump()["favicon_enabled"] is True

            await save_section_values(
                s, AVATARS_SECTION, {"favicon_enabled": False}, secret_key=SECRET_KEY
            )
            await s.commit()
            assert await get_setting_bool(s, KEY_AVATARS_FAVICON_ENABLED, True) is False
    finally:
        await engine.dispose()  # type: ignore[attr-defined]
