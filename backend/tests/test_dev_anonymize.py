"""Tests for ``tiqora dev anonymize`` (backend/src/tiqora/domain/dev_anonymize.py).

The required unit-test coverage is ``ValueMapper`` consistency (pure,
DB-free): same (seed, kind, original) always maps to the same replacement;
different originals map to different replacements. A small DB-marked test
additionally exercises the end-to-end table scrub.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.dev_anonymize import AnonymizeError, ValueMapper, anonymize_database

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


# ---------------------------------------------------------------------------
# ValueMapper: pure, no DB
# ---------------------------------------------------------------------------


def test_map_value_same_seed_and_original_is_consistent() -> None:
    mapper1 = ValueMapper(seed=42)
    mapper2 = ValueMapper(seed=42)
    assert mapper1.map_value("alice@example.com", "email") == mapper2.map_value(
        "alice@example.com", "email"
    )
    assert mapper1.map_value("Alice", "first_name") == mapper2.map_value("Alice", "first_name")


def test_map_value_is_cached_within_a_single_mapper_instance() -> None:
    mapper = ValueMapper(seed=1)
    first = mapper.map_value("bob@example.com", "email")
    second = mapper.map_value("bob@example.com", "email")
    assert first == second


def test_map_value_different_originals_map_differently() -> None:
    mapper = ValueMapper(seed=1)
    a = mapper.map_value("alice@example.com", "email")
    b = mapper.map_value("bob@example.com", "email")
    assert a != b


def test_map_value_different_seeds_diverge() -> None:
    mapper_a = ValueMapper(seed=1)
    mapper_b = ValueMapper(seed=2)
    assert mapper_a.map_value("alice@example.com", "email") != mapper_b.map_value(
        "alice@example.com", "email"
    )


def test_map_value_none_and_empty_passthrough() -> None:
    mapper = ValueMapper(seed=1)
    assert mapper.map_value(None, "email") is None
    assert mapper.map_value("", "email") == ""


def test_anonymize_address_field_maps_embedded_emails_consistently() -> None:
    mapper = ValueMapper(seed=9)
    replaced_from = mapper.anonymize_address_field('"Alice Example" <alice@example.com>')
    replaced_to = mapper.map_value("alice@example.com", "email")
    assert replaced_to is not None
    assert replaced_to in (replaced_from or "")
    assert "alice@example.com" not in (replaced_from or "")


def test_anonymize_body_preserves_line_count() -> None:
    mapper = ValueMapper(seed=3)
    original = "First line here.\nSecond line, a bit longer than the first one.\n\nFourth line."
    scrubbed = mapper.anonymize_body(original)
    assert scrubbed is not None
    assert scrubbed.count("\n") == original.count("\n")
    assert scrubbed != original


def test_require_faker_missing_raises_anonymize_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocking_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "faker":
            raise ImportError("no module named faker")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    mapper = ValueMapper(seed=1)
    with pytest.raises(AnonymizeError, match="faker"):
        mapper.map_value("someone@example.com", "email")


# ---------------------------------------------------------------------------
# End-to-end DB scrub (small, cheap)
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_database_scrubs_customer_user(mariadb_znuny_url: str) -> None:
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session, session.begin():
        session.add(
            CustomerUser(
                login="anon.target@example.com",
                email="anon.target@example.com",
                customer_id="ANON1",
                first_name="Target",
                last_name="Person",
                valid_id=1,
                create_time=NOW,
                create_by=1,
                change_time=NOW,
                change_by=1,
            )
        )
    result = await anonymize_database(factory, seed=5, batch_size=100)
    assert result.customer_users >= 1

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT first_name, last_name, email, login FROM customer_user"
                    " WHERE customer_id = 'ANON1'"
                )
            )
        ).first()
        assert row is not None
        first_name, last_name, email, login = row
        assert first_name != "Target"
        assert last_name != "Person"
        assert email != "anon.target@example.com"
        assert login != "anon.target@example.com"

    await engine.dispose()
