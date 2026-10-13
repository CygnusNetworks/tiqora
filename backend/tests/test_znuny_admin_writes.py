"""The admin write helpers: same rows and cache signals as the admin POST routes (both dialects)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._itsm_setup_helpers import (
    cleaned_plain_url,
    session_factory,
    signal_floor,
    signals_since,
)
from tiqora.api.v1.admin import dynamic_fields as admin_dynamic_fields
from tiqora.api.v1.admin import states as admin_states
from tiqora.api.v1.admin import types as admin_types
from tiqora.api.v1.admin.schemas import DynamicFieldCreate, StateCreate, TicketTypeCreate
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.dynamic_field_config import (
    config_from_yaml,
    config_to_yaml,
    validate_dynamic_field_config,
)
from tiqora.domain.znuny_admin_writes import (
    create_dynamic_field_row,
    create_ticket_state_row,
    create_ticket_type_row,
)
from tiqora.itsm.seed_definitions import PROFILES, for_profile

pytestmark = pytest.mark.db

DIALECTS = ["mysql", "postgresql"]
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
CONFIG = {
    "DefaultValue": "",
    "Link": "",
    "PossibleNone": 1,
    "PossibleValues": {"a": "A", "b": "B"},
    "TranslatableValues": 1,
}


@pytest.fixture(params=DIALECTS)
def dialect(request) -> str:
    return request.param


async def _row(session, table: str, name: str) -> dict:
    result = await session.execute(text(f"SELECT * FROM {table} WHERE name = :n"), {"n": name})  # noqa: S608
    row = dict(result.mappings().one())
    for key in ("id", "create_time", "change_time"):
        row.pop(key)
    return row


async def test_helpers_equal_admin_routes(request, dialect: str) -> None:
    names = ("W4 route type", "W4 helper type")
    url = cleaned_plain_url(
        request,
        dialect,
        types=names,
        states=("W4 route state", "W4 helper state"),
        fields=("W4RouteField", "W4HelperField"),
    )
    floor = signal_floor(url)
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            await admin_types.create_type(TicketTypeCreate(name=names[0]), ADMIN, session)
            await admin_states.create_state(
                StateCreate(name="W4 route state", type_id=2, comments="c"), ADMIN, session
            )
            await admin_dynamic_fields.create_dynamic_field(
                DynamicFieldCreate(
                    name="W4RouteField",
                    label="L",
                    field_order=900,
                    field_type="Dropdown",
                    config=CONFIG,
                ),
                ADMIN,
                session,
            )
        via_routes = signals_since(url, floor)
        floor = signal_floor(url)
        async with factory() as session:
            await create_ticket_type_row(session, name=names[1], valid_id=1, user_id=ADMIN.id)
            await create_ticket_state_row(
                session, name="W4 helper state", type_id=2, comments="c", valid_id=1,
                user_id=ADMIN.id,
            )  # fmt: skip
            await create_dynamic_field_row(
                session,
                name="W4HelperField",
                label="L",
                field_type="Dropdown",
                object_type="Ticket",
                config=CONFIG,
                field_order=900,
                user_id=ADMIN.id,
            )
            await session.commit()
        via_helpers = signals_since(url, floor)
        assert via_routes == via_helpers == ["Type", "State", "DynamicField", "DynamicFieldValue"]

        async with factory() as session:
            for table, route_name, helper_name in (
                ("ticket_type", names[0], names[1]),
                ("ticket_state", "W4 route state", "W4 helper state"),
                ("dynamic_field", "W4RouteField", "W4HelperField"),
            ):
                a = await _row(session, table, route_name)
                b = await _row(session, table, helper_name)
                assert a.pop("name") == route_name and b.pop("name") == helper_name
                if table == "dynamic_field":
                    a.pop("label"), b.pop("label")
                assert a == b, table
    finally:
        await engine.dispose()


async def test_helpers_flush_but_do_not_commit(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect, types=("W4 rollback type",))
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            row = await create_ticket_type_row(
                session, name="W4 rollback type", valid_id=1, user_id=ADMIN.id
            )
            assert row.id is not None  # flushed
            await session.rollback()
        async with factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM ticket_type WHERE name = 'W4 rollback type'")
                )
            ).scalar_one()
            assert count == 0
    finally:
        await engine.dispose()


async def test_dynamic_field_helper_rejects_invalid_config(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect, fields=("W4Bad",))
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            with pytest.raises(ValueError, match="Unknown config keys"):
                await create_dynamic_field_row(
                    session,
                    name="W4Bad",
                    label="x",
                    field_type="Text",
                    object_type="Ticket",
                    config={"Nonsense": 1},
                    field_order=901,
                    user_id=ADMIN.id,
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("profile", PROFILES)
def test_seed_configs_round_trip_through_the_validator(profile: str) -> None:
    """Every DF config Znuny's packages ship is accepted and survives YAML and back."""
    fields = for_profile(profile).dynamic_fields
    assert len(fields) == 8
    for field in fields:
        validate_dynamic_field_config(field.field_type, field.config)  # no ValueError
        assert config_from_yaml(config_to_yaml(field.config)) == field.config, field.name


def test_validator_accepts_keys_znuny_writes_and_still_rejects_typos() -> None:
    validate_dynamic_field_config("DateTime", {"Link": "", "DateRestriction": "None"})
    validate_dynamic_field_config("Dropdown", {"PossibleValues": {"a": "a"}, "TreeView": 0})
    with pytest.raises(ValueError, match="Unknown config keys"):
        validate_dynamic_field_config("DateTime", {"YearsPerid": 1})
