"""Unit tests for Znuny-compatible Ticket ACL matching and filtering.

Pure tests (no DB) cover ``compare_match_with_data``, property matching, and
Possible / PossibleAdd / PossibleNot application — the core of
``domain/ticket_acl.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.deployed import clear_revision_cache, record_revision
from tiqora.deployed.acl_dump import build_acl_dump
from tiqora.domain.ticket_acl import (
    AclRule,
    acl_properties_match,
    apply_possible_filters,
    apply_ticket_acl,
    compare_match_with_data,
    load_valid_acls,
    match_property_items,
    parse_acl_yaml,
    properties_block_matches,
)

# ---------------------------------------------------------------------------
# compare_match_with_data
# ---------------------------------------------------------------------------


def test_plain_equality() -> None:
    assert compare_match_with_data("open", "open")["match"] is True
    assert compare_match_with_data("open", "closed")["match"] is False


def test_not_prefix() -> None:
    assert compare_match_with_data("[Not]closed", "open")["match"] is True
    assert compare_match_with_data("[Not]closed", "closed")["match"] is False


def test_regexp_case_sensitive() -> None:
    assert compare_match_with_data("[RegExp]^open", "open")["match"] is True
    assert compare_match_with_data("[RegExp]^Open", "open")["match"] is False


def test_regexp_case_insensitive() -> None:
    assert compare_match_with_data("[regexp]^open", "OPEN")["match"] is True


def test_not_regexp() -> None:
    assert compare_match_with_data("[NotRegExp]closed", "open")["match"] is True
    assert compare_match_with_data("[NotRegExp]closed", "closed successful")["match"] is False


def test_slash_regex_convenience() -> None:
    assert compare_match_with_data("/^Raw/", "Raw")["match"] is True
    assert compare_match_with_data("/^raw/i", "Raw")["match"] is True
    assert compare_match_with_data("/^Raw/", "Junk")["match"] is False


def test_array_not_semantics_via_match_property_items() -> None:
    # [Not]Admin matches when Admin is NOT in the role list.
    assert match_property_items(["[Not]Admin"], ["users", "agents"]) is True
    assert match_property_items(["[Not]Admin"], ["users", "Admin"]) is False
    # Positive array membership.
    assert match_property_items(["Admin"], ["users", "Admin"]) is True
    assert match_property_items(["Admin"], ["users"]) is False


# ---------------------------------------------------------------------------
# Properties matching
# ---------------------------------------------------------------------------


def test_empty_properties_force_match() -> None:
    matched, tried = acl_properties_match({}, {}, {})
    assert matched is True
    assert tried is True


def test_properties_and_properties_database_both_required() -> None:
    config = {
        "Properties": {"Frontend": {"Action": ["AgentTicketPhone"]}},
        "PropertiesDatabase": {"Ticket": {"Queue": ["Support"]}},
    }
    checks = {"Frontend": {"Action": "AgentTicketPhone"}, "Ticket": {"Queue": "Raw"}}
    checks_db = {"Ticket": {"Queue": "Support"}}
    matched, tried = acl_properties_match(config, checks, checks_db)
    assert matched is True
    assert tried is True

    checks_db_bad = {"Ticket": {"Queue": "Raw"}}
    matched2, _ = acl_properties_match(config, checks, checks_db_bad)
    assert matched2 is False


def test_missing_properties_inherits_database() -> None:
    config = {"PropertiesDatabase": {"Ticket": {"State": ["open"]}}}
    checks: dict = {}
    checks_db = {"Ticket": {"State": "open"}}
    matched, tried = acl_properties_match(config, checks, checks_db)
    assert matched is True
    assert tried is True


def test_properties_block_requires_all_fields() -> None:
    block = {
        "Ticket": {
            "Queue": ["Support"],
            "State": ["open"],
        }
    }
    checks = {"Ticket": {"Queue": "Support", "State": "closed"}}
    match, tried = properties_block_matches(block, checks)
    assert tried is True
    assert match is False


def test_user_group_rw_array_match() -> None:
    block = {"User": {"Group_rw": ["admin", "users"]}}
    checks = {"User": {"Group_rw": ["stats", "users"]}}
    match, _ = properties_block_matches(block, checks)
    assert match is True


# ---------------------------------------------------------------------------
# Possible / PossibleAdd / PossibleNot
# ---------------------------------------------------------------------------


def test_possible_replaces_whitelist() -> None:
    data = {1: "1 very low", 2: "2 low", 3: "3 normal", 4: "4 high"}
    current = dict(data)
    change = {"Possible": {"Ticket": {"Priority": ["3 normal", "4 high"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="Priority"
    )
    assert used is True
    assert new == {3: "3 normal", 4: "4 high"}


def test_possible_not_subtracts() -> None:
    data = {1: "open", 2: "closed successful", 3: "closed unsuccessful"}
    current = dict(data)
    change = {"PossibleNot": {"Ticket": {"State": ["[RegExp]^closed"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is True
    assert new == {1: "open"}


def test_possible_add_unions() -> None:
    data = {1: "open", 2: "closed successful", 3: "merged"}
    # Start from a restricted set, then PossibleAdd brings "merged" back.
    current = {1: "open"}
    change = {"PossibleAdd": {"Ticket": {"State": ["merged"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is True
    assert new == {1: "open", 3: "merged"}


def test_possible_then_possible_not_stacked() -> None:
    data = {1: "a", 2: "b", 3: "c", 4: "d"}
    current = dict(data)
    change = {
        "Possible": {"Ticket": {"Queue": ["a", "b", "c"]}},
        "PossibleNot": {"Ticket": {"Queue": ["b"]}},
    }
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="Queue"
    )
    assert used is True
    assert new == {1: "a", 3: "c"}


def test_action_return_type_uses_array_under_action() -> None:
    data = {1: "AgentTicketClose", 2: "AgentTicketNote", 3: "AgentTicketMove"}
    current = dict(data)
    change = {"Possible": {"Action": ["AgentTicketNote", "AgentTicketMove"]}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Action", return_sub_type="-"
    )
    assert used is True
    assert new == {2: "AgentTicketNote", 3: "AgentTicketMove"}


def test_no_matching_section_returns_unused() -> None:
    data = {1: "open"}
    change = {"Possible": {"Ticket": {"Priority": ["3 normal"]}}}
    new, used = apply_possible_filters(
        data, data, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is False
    assert new == data


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def test_parse_acl_yaml_roundtrip() -> None:
    raw = """
Properties:
  Frontend:
    Action:
      - AgentTicketPhone
Possible:
  Ticket:
    Priority:
      - 3 normal
"""
    # config_match only has Properties; Possible would live in config_change.
    parsed = parse_acl_yaml(raw)
    assert "Properties" in parsed
    assert parsed["Properties"]["Frontend"]["Action"] == ["AgentTicketPhone"]


def test_parse_acl_yaml_empty_and_invalid() -> None:
    assert parse_acl_yaml(None) == {}
    assert parse_acl_yaml("") == {}
    assert parse_acl_yaml("not: [valid: yaml: {{{") == {}
    assert parse_acl_yaml("- just a list") == {}


# ---------------------------------------------------------------------------
# Evaluation reads the newest ACL revision (DB; seed ids 881750-881759, rulings F8)
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 3, 4, 5, 6, 7)
_SEEDED_ACL_URLS: list[str] = []
_STATES = {1: "new", 2: "closed successful", 4: "open"}


def _acl_cleanup(conn: Any) -> None:
    conn.execute(text("DELETE FROM tiqora_acl_revision"))
    conn.execute(text("DELETE FROM acl WHERE id BETWEEN 881750 AND 881759"))


def _insert_acl(conn: Any, n: int, name: str, state: str, stop: int = 0) -> None:
    conn.execute(
        text(
            "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
            " config_match, config_change, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :name, NULL, '', 1, :stop, :match, :change, :t, 1, :t, 1)"
        ),
        {
            "id": 881750 + n,
            "name": name,
            "stop": stop,
            "match": "{}\n",
            "change": f"PossibleNot:\n  Ticket:\n    State: [{state}]\n",
            "t": _T0,
        },
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_acl_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED_ACL_URLS):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _acl_cleanup(conn)
        engine.dispose()
    _SEEDED_ACL_URLS.clear()
    clear_revision_cache()


@pytest.fixture
async def acl_session(mariadb_znuny_url: str) -> AsyncIterator[AsyncSession]:
    _SEEDED_ACL_URLS.append(mariadb_znuny_url)
    clear_revision_cache()
    sync_engine = create_engine(mariadb_znuny_url)
    with sync_engine.begin() as conn:
        _acl_cleanup(conn)
        _insert_acl(conn, 1, "a-acl", "open")
    sync_engine.dispose()
    engine = create_async_engine(mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _filtered_states(session: AsyncSession) -> dict[Any, str]:
    return await apply_ticket_acl(
        session,
        user_id=2,
        data=dict(_STATES),
        return_type="Ticket",
        return_sub_type="State",
        checks={},
    )


@pytest.mark.db
async def test_acl_from_revision_ignores_undeployed_acl(
    acl_session: AsyncSession, mariadb_znuny_url: str
) -> None:
    async with acl_session.begin():
        await record_revision(
            acl_session,
            "acl",
            dump=await build_acl_dump(acl_session),
            source="initial",
            deployed_by=None,
        )
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        _insert_acl(conn, 2, "b-acl", "new")  # valid, but never deployed
    engine.dispose()

    rules = await load_valid_acls(acl_session)
    assert [r.name for r in rules] == ["a-acl"]
    assert await _filtered_states(acl_session) == {1: "new", 2: "closed successful"}


@pytest.mark.db
async def test_acl_without_revision_uses_live_rows(
    acl_session: AsyncSession, mariadb_znuny_url: str
) -> None:
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        _insert_acl(conn, 2, "b-acl", "new", stop=1)
    engine.dispose()

    rules = await load_valid_acls(acl_session)
    assert rules == [
        AclRule(
            name="a-acl",
            stop_after_match=False,
            config_match={},
            config_change={"PossibleNot": {"Ticket": {"State": ["open"]}}},
        ),
        AclRule(
            name="b-acl",
            stop_after_match=True,
            config_match={},
            config_change={"PossibleNot": {"Ticket": {"State": ["new"]}}},
        ),
    ]
    assert await _filtered_states(acl_session) == {2: "closed successful"}


@pytest.mark.db
async def test_revision_rule_carries_dump_sections_and_stop_flag(
    acl_session: AsyncSession, mariadb_znuny_url: str
) -> None:
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE acl SET stop_after_match = 1 WHERE id = 881751"))
    engine.dispose()
    async with acl_session.begin():
        await record_revision(
            acl_session,
            "acl",
            dump=await build_acl_dump(acl_session),
            source="initial",
            deployed_by=None,
        )

    (rule,) = await load_valid_acls(acl_session)
    assert rule.name == "a-acl"
    assert rule.stop_after_match is True
    assert rule.config_match == {"Properties": {}, "PropertiesDatabase": {}}
    assert rule.config_change == {
        "Possible": {},
        "PossibleAdd": {},
        "PossibleNot": {"Ticket": {"State": ["open"]}},
    }


def test_possible_owner_and_responsible_subtypes_filter_login_maps() -> None:
    # Znuny hands _GetOwners/_GetResponsibles to the ACL as {UserID => UserLogin}.
    agents = {2: "agent-a", 3: "agent-b", 4: "agent-c"}
    change = {
        "Possible": {"Ticket": {"Owner": ["agent-a", "agent-b"]}},
        "PossibleNot": {"Ticket": {"Responsible": ["agent-c"]}},
    }
    owners, owner_used = apply_possible_filters(
        agents, dict(agents), change, return_type="Ticket", return_sub_type="Owner"
    )
    responsibles, resp_used = apply_possible_filters(
        agents, dict(agents), change, return_type="Ticket", return_sub_type="Responsible"
    )
    assert owner_used and resp_used
    assert owners == {2: "agent-a", 3: "agent-b"}
    assert responsibles == {2: "agent-a", 3: "agent-b"}


@pytest.mark.db
async def test_owner_options_use_login_acl_and_full_name_labels(
    acl_session: AsyncSession, mariadb_znuny_url: str
) -> None:
    from tiqora.domain.auth import AuthenticatedUser
    from tiqora.domain.field_options import collect_ticket_field_options

    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE acl SET config_change = :c WHERE id = 881751"),
            {"c": "PossibleNot:\n  Ticket:\n    Owner: [root@localhost]\n"},
        )
    engine.dispose()
    user = AuthenticatedUser(
        id=2, login="someone", first_name="Some", last_name="One", auth_method="session"
    )
    got = await collect_ticket_field_options(
        acl_session, user, fields={"owner", "responsible"}, action="AgentTicketBulk"
    )
    assert "1" not in got.owner  # root@localhost removed by PossibleNot Owner
    assert "1" in got.responsible  # the ACL names Owner only
    assert got.responsible["1"].strip() != "root@localhost"
