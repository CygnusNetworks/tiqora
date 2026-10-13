"""Mode A provider (``ZnunyCiProvider``) against the Znuny ITSM databases.

Both dialects x both schema profiles. Everything a test seeds is flushed in a session that is
rolled back at the end (users, groups, CIs, links, tickets, history), so the strict leak check has
nothing to find; the profile packages the provider detects its schema by are committed rows that
``prepare`` removes again. Seed band 940700-940899 (users 94070x, group 940710, services 9407xx,
ticket 940730, CI numbers 9407xxxx).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests._itsm_adapter_helpers import itsm_session, seed_service, seed_user
from tests._itsm_db import ITSM_FIXTURES, ITSM_PARAMS
from tests._itsm_fixtures import CLASS_CATALOG, catalog_id, seed_ci, seed_ci_link
from tests._itsm_mode_helpers import (
    PLAIN_URL_FIXTURES,
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    seed_packages,
)
from tests.itsm_provider_contract import CiProviderContract, SeededCis
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.itsm.ci_provider import (
    CiError,
    CiNotFound,
    CiPermissionDenied,
    CiQuery,
    CiRef,
    CiUnavailable,
)
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.itsm.znuny_adapter.profiles import PROFILES
from tiqora.itsm.znuny_adapter.provider import ZnunyCiProvider
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

pytestmark = pytest.mark.db

FULL = 940701  # reads every class
RO = 940702  # user "itsm94-ro": cannot read the class behind the secret group
NOBODY = 940703  # no group at all
SECRET_GROUP = 940710
TICKET = 940730
SERVICE = 940720
EMPTY_SERVICE = 940721
SYSCONFIG_BASE = 940780
_NOW = datetime(2026, 1, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC


@dataclass
class Env:
    session: AsyncSession
    provider: ZnunyCiProvider
    profile: str
    dialect: str
    engine: AsyncEngine


@pytest.fixture(params=ITSM_PARAMS, ids=[f"{d}-{p}" for d, p in ITSM_PARAMS])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    dialect, profile = request.param
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    async with itsm_session(request, dialect, profile) as (session, _reader, engine):
        yield Env(session, ZnunyCiProvider(SysConfig(session)), profile, dialect, engine)
    clear_detect_cache()


async def _group_id(session: AsyncSession, name: str) -> int:
    return int(
        (
            await session.execute(
                text("SELECT id FROM permission_groups WHERE name = :n"), {"n": name}
            )
        ).scalar_one()
    )


async def _grant(session: AsyncSession, user_id: int, group_id: int, key: str = "ro") -> None:
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
        ),
        {"u": user_id, "g": group_id, "k": key, "t": _NOW},
    )


async def _seed_users(session: AsyncSession) -> int:
    """FULL: ro on itsm-configitem and the secret group; RO: itsm-configitem only; NOBODY: none.

    Returns the id of ``itsm-configitem``.
    """
    for uid, login in ((FULL, "itsm94-full"), (RO, "itsm94-ro"), (NOBODY, "itsm94-nobody")):
        await seed_user(session, uid, login, "Itsm", login)
    stock = await _group_id(session, "itsm-configitem")
    await session.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:i, 'itsm94-secret', 1, :t, 1, :t, 1)"
        ),
        {"i": SECRET_GROUP, "t": _NOW},
    )
    await _grant(session, FULL, stock)
    await _grant(session, FULL, SECRET_GROUP)
    await _grant(session, RO, stock)
    return stock


async def _set_class_group(session: AsyncSession, class_name: str, group_id: int | None) -> None:
    class_id = await catalog_id(session, CLASS_CATALOG, class_name)
    await session.execute(
        text(
            "DELETE FROM general_catalog_preferences"
            " WHERE general_catalog_id = :c AND pref_key = 'Permission'"
        ),
        {"c": class_id},
    )
    if group_id is not None:
        await session.execute(
            text(
                "INSERT INTO general_catalog_preferences (general_catalog_id, pref_key, pref_value)"
                " VALUES (:c, 'Permission', :g)"
            ),
            {"c": class_id, "g": str(group_id)},
        )


async def _grant_ticket_rw(session: AsyncSession, user_id: int, ticket_id: int = TICKET) -> None:
    """``rw`` on the group that owns the seeded ticket's queue (R12)."""
    gid = (
        await session.execute(
            text(
                "SELECT q.group_id FROM queue q JOIN ticket t ON t.queue_id = q.id WHERE t.id = :t"
            ),
            {"t": ticket_id},
        )
    ).scalar_one()
    await _grant(session, user_id, int(gid), "rw")


async def _seed_ticket(session: AsyncSession, ticket_id: int = TICKET) -> None:
    await session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
            " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by) VALUES (:id, :tn, 'itsm94 ticket', 1, 1, 1, 1, 1,"
            " 3, 1, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
        ),
        {"id": ticket_id, "tn": f"2026010194{ticket_id}", "t": _NOW},
    )


async def seed_world(env: Env) -> SeededCis:
    """Three CIs (a: 3 versions, operational; b: incident; hidden: secret class) and a service."""
    session = env.session
    await _seed_users(session)
    await _set_class_group(session, "Hardware", SECRET_GROUP)
    versions = [
        {"name": "itsm94-kit-a-v1", "inci_state": "Incident"},
        {"name": "itsm94-kit-a-v2", "inci_state": "Warning"},
        {"name": "itsm94-kit-a", "inci_state": "Operational"},
    ]
    a = await seed_ci(
        session, env.profile, number="94070001", class_name="Computer", name="x", versions=versions
    )
    b = await seed_ci(
        session,
        env.profile,
        number="94070002",
        class_name="Computer",
        name="itsm94-kit-b",
        inci_state="Incident",
    )
    hidden = await seed_ci(
        session,
        env.profile,
        number="94070003",
        class_name="Hardware",
        name="itsm94-kit-hidden",
    )
    await seed_service(session, SERVICE, "itsm94-kit-service")
    await seed_service(session, EMPTY_SERVICE, "itsm94-kit-empty")
    for ci in (a, b):
        await seed_ci_link(
            session,
            source_key=SERVICE,
            target_key=ci,
            link_type="DependsOn",
            source_object="Service",
            target_object="ITSMConfigItem",
        )
    await _seed_ticket(session)
    await _grant_ticket_rw(session, FULL)
    return SeededCis(
        user_id=FULL,
        restricted_user_id=RO,
        a=CiRef("znuny", a),
        b=CiRef("znuny", b),
        hidden=CiRef("znuny", hidden),
        common_text="itsm94-kit",
        a_version_count=3,
        ticket_id=TICKET,
        service_id=SERVICE,
        empty_service_id=EMPTY_SERVICE,
    )


class TestZnunyProviderContract(CiProviderContract):
    """The conformance kit against Mode A, on all four database combinations."""

    @pytest.fixture
    def provider(self, env: Env) -> ZnunyCiProvider:
        return env.provider

    @pytest.fixture
    def session(self, env: Env) -> AsyncSession:
        return env.session

    @pytest.fixture
    async def seed(self, env: Env) -> SeededCis:
        return await seed_world(env)


# --- permission ------------------------------------------------------------------------------


async def test_permission_needs_itsm_configitem_group(env: Env) -> None:
    """Stock classes point at ``itsm-configitem``: without ro on it nothing is visible."""
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    assert await p.classes(s, user_id=NOBODY) == []
    page = await p.search(s, CiQuery(text=seeded.common_text), user_id=NOBODY)
    assert (page.items, page.total) == ((), 0)
    with pytest.raises(CiNotFound):
        await p.get(s, seeded.a, user_id=NOBODY)
    assert await p.get_many(s, [seeded.a, seeded.b], user_id=NOBODY) == {}
    # rw counts as ro (Znuny's PermissionUserGet)
    stock = await _group_id(s, "itsm-configitem")
    await _grant(s, NOBODY, stock, "rw")
    assert {c.name for c in await p.classes(s, user_id=NOBODY)} >= {"Computer"}
    assert (await p.get(s, seeded.a, user_id=NOBODY)).summary.ref == seeded.a


async def test_permission_class_group(env: Env) -> None:
    """The group is the class's ``Permission`` preference, not a fixed one."""
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    full = {c.name for c in await p.classes(s, user_id=FULL)}
    restricted = {c.name for c in await p.classes(s, user_id=RO)}
    assert "Hardware" in full
    assert "Hardware" not in restricted and "Computer" in restricted
    assert (await p.get(s, seeded.hidden, user_id=FULL)).summary.class_name == "Hardware"
    with pytest.raises(CiNotFound):
        await p.get(s, seeded.hidden, user_id=RO)
    # a class without the preference has no group at all: nobody reads it (stock Znuny)
    await _set_class_group(s, "Network", None)
    net = await seed_ci(
        s, env.profile, number="94070010", class_name="Network", name="itsm94-net-1"
    )
    assert "Network" not in {c.name for c in await p.classes(s, user_id=FULL)}
    with pytest.raises(CiNotFound):
        await p.get(s, CiRef("znuny", net), user_id=FULL)
    # the unreadable class also disappears from the link read path
    link_type = "RelevantTo"
    with pytest.raises(CiNotFound):
        await p.link_ticket(
            s, ticket_id=TICKET, ref=CiRef("znuny", net), link_type=link_type, user_id=FULL
        )


# --- links -------------------------------------------------------------------------------------


async def _links(session: AsyncSession) -> list[tuple[str, str, str, str, str, str]]:
    rows = await session.execute(
        text(
            "SELECT so.name, r.source_key, tobj.name, r.target_key, lt.name, ls.name"
            " FROM link_relation r JOIN link_object so ON so.id = r.source_object_id"
            " JOIN link_object tobj ON tobj.id = r.target_object_id"
            " JOIN link_type lt ON lt.id = r.type_id JOIN link_state ls ON ls.id = r.state_id"
            " WHERE (so.name = 'Ticket' AND r.source_key = :t)"
            " OR (tobj.name = 'Ticket' AND r.target_key = :t)"
        ),
        {"t": str(TICKET)},
    )
    return [tuple(str(c) for c in r) for r in rows.all()]  # type: ignore[misc]


async def _ticket_history(session: AsyncSession) -> list[tuple[str, str]]:
    rows = await session.execute(
        text(
            "SELECT ht.name, h.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
            " WHERE h.ticket_id = :t AND ht.name IN ('CILinkAdd', 'CILinkDelete') ORDER BY h.id"
        ),
        {"t": TICKET},
    )
    return [(str(r[0]), str(r[1])) for r in rows.all()]


async def test_link_ticket_needs_rw_on_the_ticket(env: Env) -> None:
    """R12: ro on the CI is not enough, and a missing ticket never reaches a write."""
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    # RO reads the CI class (itsm-configitem) but holds no group on the ticket's queue
    with pytest.raises(CiPermissionDenied):
        await p.link_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=RO)
    # ro (not rw) on the ticket's queue group is still refused
    gid = (await s.execute(text("SELECT group_id FROM queue WHERE id = 1"))).scalar_one()
    await _grant(s, RO, int(gid), "ro")
    with pytest.raises(CiPermissionDenied):
        await p.link_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=RO)
    assert await _links(s) == [] and await _ticket_history(s) == []
    # a nonexistent ticket is CiNotFound and writes nothing
    with pytest.raises(CiNotFound):
        await p.link_ticket(
            s, ticket_id=TICKET + 1000, ref=seeded.a, link_type="RelevantTo", user_id=FULL
        )
    with pytest.raises(CiNotFound):
        await p.unlink_ticket(
            s, ticket_id=TICKET + 1000, ref=seeded.a, link_type=None, user_id=FULL
        )
    # an existing link survives an unlink attempt without rw, history untouched
    assert await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=FULL
    )
    before = (await _links(s), await _ticket_history(s))
    with pytest.raises(CiPermissionDenied):
        await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type=None, user_id=RO)
    assert (await _links(s), await _ticket_history(s)) == before
    # with rw it works
    await _grant(s, RO, int(gid), "rw")
    assert await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type=None, user_id=RO) == 1


async def test_unlink_temporary_link_writes_no_history(env: Env) -> None:
    """Survey q5: Znuny writes no history for temporary links."""
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    await seed_ci_link(
        s,
        source_key=TICKET,
        target_key=seeded.a.id,
        link_type="RelevantTo",
        source_object="Ticket",
        target_object="ITSMConfigItem",
        state="Temporary",
    )
    assert await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type=None, user_id=FULL)
    assert await _ticket_history(s) == []


async def test_link_ticket_writes_link_relation_like_znuny(env: Env) -> None:
    """Object ``ITSMConfigItem``, state Valid, ticket as source, and the ticket history entry.

    Direction decision (behaviour survey, question 6): Znuny's dialog stores the *selected* CI as
    source for ``<Type>::Source``; the provider contract has no direction, so a new link has the
    ticket as source (I4) and both orientations are read back.
    """
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    ci = seeded.a.id
    assert await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=FULL
    )
    assert await _links(s) == [
        ("Ticket", str(TICKET), "ITSMConfigItem", str(ci), "RelevantTo", "Valid")
    ]
    assert await _ticket_history(s) == [("CILinkAdd", f"%%{ci}%%RelevantTo")]
    # idempotent: no second row, no second history entry
    assert not await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=FULL
    )
    assert len(await _links(s)) == 1 and len(await _ticket_history(s)) == 1
    # a type Znuny does not allow between a ticket and a CI is refused, not stored
    with pytest.raises(CiError):
        await p.link_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type="Normal", user_id=FULL)
    assert len(await _links(s)) == 1
    # a pointed link: ticket side "source"; Znuny-made (CI as source): ticket side "target"
    assert await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.a, link_type="DependsOn", user_id=FULL
    )
    await seed_ci_link(
        s,
        source_key=seeded.b.id,
        target_key=TICKET,
        link_type="AlternativeTo",
        source_object="ITSMConfigItem",
        target_object="Ticket",
    )
    await seed_ci_link(
        s,
        source_key=seeded.hidden.id,
        target_key=TICKET,
        link_type="DependsOn",
        source_object="ITSMConfigItem",
        target_object="Ticket",
    )
    found = (await p.ticket_links(s, [TICKET], user_id=RO))[TICKET]
    assert {(lk.ci.ref.id, lk.link_type, lk.direction) for lk in found} == {
        (ci, "RelevantTo", "both"),
        (ci, "DependsOn", "source"),
        (seeded.b.id, "AlternativeTo", "both"),
    }  # the hidden CI's link is omitted for the restricted user
    full = (await p.ticket_links(s, [TICKET], user_id=FULL))[TICKET]
    assert (seeded.hidden.id, "DependsOn", "target") in {
        (lk.ci.ref.id, lk.link_type, lk.direction) for lk in full
    }
    # a pointed link Znuny stored the other way round counts as existing
    assert not await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.hidden, link_type="DependsOn", user_id=FULL
    )
    assert await p.ci_ticket_ids(s, seeded.hidden, user_id=FULL) == [TICKET]
    # removal: both orientations, every type, history per removed link
    assert (
        await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type=None, user_id=FULL) == 2
    )
    assert (
        await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.b, link_type=None, user_id=FULL) == 1
    )
    deleted = [h for h in await _ticket_history(s) if h[0] == "CILinkDelete"]
    assert sorted(deleted) == sorted(
        [
            ("CILinkDelete", f"%%{ci}%%DependsOn"),
            ("CILinkDelete", f"%%{ci}%%RelevantTo"),
            ("CILinkDelete", f"%%{seeded.b.id}%%AlternativeTo"),
        ]
    )


async def test_ticket_link_types_follow_the_offer_table(env: Env) -> None:
    await seed_world(env)
    options = await env.provider.ticket_link_types(env.session)
    assert {o.name for o in options} == {"AlternativeTo", "DependsOn", "RelevantTo"}
    depends = next(o for o in options if o.name == "DependsOn")
    assert depends.pointed and depends.source_label and depends.target_label
    # a type the admin hid in the override table is neither offered nor accepted
    await env.session.execute(
        insert(TiqoraItsmLinkType).values(
            name="DependsOn",
            origin="znuny",
            pointed=True,
            propagation="both",
            offer_ticket_ticket=False,
            offer_ticket_ci=False,
            offer_ci_ci=False,
            offer_ci_service=False,
            position=5,
            valid=True,
            create_time=_NOW,
            create_by=1,
            change_time=_NOW,
            change_by=1,
        )
    )
    assert "DependsOn" not in {o.name for o in await env.provider.ticket_link_types(env.session)}
    ref = await seed_world_refs(env)
    with pytest.raises(CiError):
        await env.provider.link_ticket(
            env.session, ticket_id=TICKET, ref=ref, link_type="DependsOn", user_id=FULL
        )


async def seed_world_refs(env: Env) -> CiRef:
    row = (
        await env.session.execute(
            text("SELECT id FROM configitem WHERE configitem_number = '94070001'")
        )
    ).scalar_one()
    return CiRef("znuny", int(row))


# --- read-only guarantee -------------------------------------------------------------------------


async def _checksums(session: AsyncSession, tables: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for table in tables:  # constants of the profile, never input
        rows = (await session.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
        blob = "\n".join(sorted(repr(tuple(r)) for r in rows))
        out[table] = hashlib.sha256(blob.encode()).hexdigest()
    return out


async def test_mode_a_writes_no_itsm_table(env: Env) -> None:
    seeded = await seed_world(env)
    p, s = env.provider, env.session
    tables = PROFILES[env.profile].readonly_tables
    assert {
        "configitem",
        "general_catalog",
        "xml_storage",
        "cip_allocate",
        "service",
        "sla",
    } <= set(tables)
    before = await _checksums(s, tables)

    await p.classes(s, user_id=FULL)
    await p.search(s, CiQuery(text="itsm94-kit"), user_id=FULL)
    await p.get(s, seeded.a, user_id=FULL)
    await p.versions(s, seeded.a, user_id=FULL, limit=10, offset=0)
    await p.get_many(s, [seeded.a, seeded.b], user_id=FULL)
    await p.service_incident_states(s, [SERVICE, EMPTY_SERVICE])
    await p.service_cis(s, SERVICE, user_id=FULL)
    assert await p.link_ticket(
        s, ticket_id=TICKET, ref=seeded.a, link_type="RelevantTo", user_id=FULL
    )
    await p.ticket_links(s, [TICKET], user_id=FULL)
    assert (
        await p.unlink_ticket(s, ticket_id=TICKET, ref=seeded.a, link_type=None, user_id=FULL) == 1
    )

    assert await _checksums(s, tables) == before


# --- service incident state ----------------------------------------------------------------------


def _expected_service_states(profile: str) -> dict[tuple[str, str], str]:
    doc = json.loads((ITSM_FIXTURES / "behaviour" / f"{profile}.json").read_text())
    scenarios = doc["questions"]["2"]["expected"]["scenarios"]
    return {(e["scenario"], e["path"]): e["value"] for e in scenarios}


async def test_service_incident_state_vectors(env: Env) -> None:
    """The service-level answers of behaviour question 2, rebuilt from the peer's scenarios.

    The peer's CI states come from Znuny's own recalculation (``CurInciStateRecalc``), which turns
    a neighbour of an incident CI to *Warning* in ``configitem.cur_inci_state_id``. Mode A reads
    that stored state, so the scenarios seed the neighbours as the peer left them (Warning);
    the transitive propagation itself is Znuny's work and not asserted here.
    """
    s, p, profile = env.session, env.provider, env.profile
    counter = iter(range(1, 100))

    async def service(name: str, sid: int) -> int:
        await seed_service(s, sid, name)
        return sid

    async def ci(inci: str, depl: str = "Production") -> int:
        n = next(counter)
        return await seed_ci(
            s,
            profile,
            number=f"9407{n:04d}",
            class_name="Computer",
            name=f"itsm94-sc{n}.example.org",
            depl_state=depl,
            inci_state=inci,
        )

    async def link(svc: int, ci_id: int, kind: str = "DependsOn") -> None:
        await seed_ci_link(
            s,
            source_key=svc,
            target_key=ci_id,
            link_type=kind,
            source_object="Service",
            target_object="ITSMConfigItem",
        )

    async def state(sid: int) -> str:
        value = (await p.service_incident_states(s, [sid]))[sid].state
        assert value is not None
        assert value.functionality is not None
        return value.functionality

    got: dict[tuple[str, str], str] = {}
    base = 940740
    s1, a = await service("itsm94-s1", base + 1), await ci("Incident")
    await link(s1, a)
    got[("s1_incident_ci", "service.type")] = await state(s1)

    s2, w = await service("itsm94-s2", base + 2), await ci("Warning")
    await link(s2, w)
    got[("s2_warning_via_dependency", "service.type")] = await state(s2)

    s2b, wb = await service("itsm94-s2b", base + 3), await ci("Warning")
    await link(s2b, wb)  # the incident CI depends on the neighbour: Both counts either way
    got[("s2b_warning_opposite_direction", "service.type")] = await state(s2b)

    s3 = await service("itsm94-s3", base + 4)
    await link(s3, await ci("Incident", depl="Planned"))
    got[("s3_non_productive_ignored", "service.type")] = await state(s3)

    s4, w4, x4 = await service("itsm94-s4", base + 5), await ci("Warning"), await ci("Incident")
    await link(s4, w4)
    got[("s4_incident_beats_warning", "service_only_warning_ci.type")] = await state(s4)
    await link(s4, x4)
    got[("s4_incident_beats_warning", "service_with_incident_ci.type")] = await state(s4)

    s5 = await service("itsm94-s5", base + 6)
    await link(s5, await ci("Incident"), "RelevantTo")
    got[("s5_relevant_to_not_counted", "service.type")] = await state(s5)

    parent = await service("itsm94-par", base + 7)
    child = await service("itsm94-par::child", base + 8)
    await link(child, await ci("Incident"))
    got[("s6_child_service_makes_parent_warning", "parent.type")] = await state(parent)
    got[("s6_child_service_makes_parent_warning", "child.type")] = await state(child)

    # s7: the dependency is removed, the neighbour and the service recover
    await s.execute(
        text("UPDATE configitem SET cur_inci_state_id = :o WHERE id = :i"),
        {"o": await catalog_id(s, "ITSM::Core::IncidentState", "Operational"), "i": w},
    )
    got[("s7_unlink_recovers", "service.type")] = await state(s2)

    # s8: a new operational version of the incident CI recovers service s1
    await s.execute(
        text("UPDATE configitem SET cur_inci_state_id = :o WHERE id = :i"),
        {"o": await catalog_id(s, "ITSM::Core::IncidentState", "Operational"), "i": a},
    )
    got[("s8_new_operational_version_recovers", "service.type")] = await state(s1)

    expected = _expected_service_states(profile)
    service_level = {k: v for k, v in expected.items() if k[1].endswith(".type")}
    assert len(service_level) == len(got)  # every service answer of the vector is covered
    assert got == service_level


async def test_service_state_counts_and_none(env: Env) -> None:
    seeded = await seed_world(env)
    states = await env.provider.service_incident_states(
        env.session, [seeded.service_id, seeded.empty_service_id, 2_000_000_000]
    )
    assert states[seeded.service_id].cis_incident == 1
    assert states[seeded.service_id].cis_warning == 0
    assert states[seeded.empty_service_id].state is None
    assert states[2_000_000_000].state is None
    # linked, nothing counted -> operational (not None)
    cis = await env.provider.service_cis(env.session, seeded.service_id, user_id=FULL, limit=1)
    assert len(cis) == 1


# --- deep links ----------------------------------------------------------------------------------


async def _set_sysconfig(session: AsyncSession, values: dict[str, Any]) -> None:
    for i, (name, value) in enumerate(values.items()):
        await session.execute(text("DELETE FROM sysconfig_default WHERE name = :n"), {"n": name})
        enc: bytes | str = yaml_encode_effective(value)
        if session.get_bind().dialect.name == "postgresql":
            enc = enc.decode("utf-8")  # type: ignore[union-attr]
        await session.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
                " is_readonly, is_required, is_valid, has_configlevel, user_modification_possible,"
                " user_modification_active, xml_content_raw, xml_content_parsed, xml_filename,"
                " effective_value, is_dirty, exclusive_lock_guid, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 'x', 'x', 0, 0, 0, 1, 0, 0, 0, 'x',"
                " 'x', 'Framework.xml', :e, 0, '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": SYSCONFIG_BASE + i, "n": name, "e": enc},
        )


async def test_edit_url_from_sysconfig_and_override(env: Env) -> None:
    seeded = await seed_world(env)
    s = env.session
    ref = seeded.a

    async def edit_url(provider: ZnunyCiProvider) -> str | None:
        return (await provider.get(s, ref, user_id=FULL)).summary.edit_url

    # FQDN still at Znuny's placeholder: no link is better than a wrong one
    assert await edit_url(ZnunyCiProvider(SysConfig(s))) is None

    await _set_sysconfig(
        s, {"FQDN": "znuny.example.org", "HttpType": "https", "ScriptAlias": "znuny/"}
    )
    expected = f"https://znuny.example.org/znuny/index.pl?Action=AgentITSMConfigItemEdit;ConfigItemID={ref.id}"
    assert await edit_url(ZnunyCiProvider(SysConfig(s))) == expected
    found = await ZnunyCiProvider(SysConfig(s)).search(s, CiQuery(refs=(ref,)), user_id=FULL)
    assert found.items[0].edit_url == expected  # search items carry the link, too

    # the ITSM setting overrides SysConfig (stored in tiqora_settings, read by the default loader)
    await s.execute(
        insert(TiqoraSettings).values(
            key="itsm.znuny_agent_url", value="https://helpdesk.example.org/otrs"
        )
    )
    override = (
        "https://helpdesk.example.org/otrs/index.pl"
        f"?Action=AgentITSMConfigItemEdit;ConfigItemID={ref.id}"
    )
    assert await edit_url(ZnunyCiProvider(SysConfig(s))) == override

    # an injected loader works the same (unit seam), trailing slash tolerated
    async def loader(_session: AsyncSession) -> ItsmSettings:
        return ItsmSettings(znuny_agent_url="https://znuny.example.org/znuny/")

    injected = ZnunyCiProvider(SysConfig(s), settings_loader=loader)
    assert await edit_url(injected) == expected


# --- availability ------------------------------------------------------------


async def test_is_empty(env: Env) -> None:
    assert await env.provider.is_empty(env.session) is True
    await seed_ci(
        env.session, env.profile, number="94070900", class_name="Computer", name="itsm94-e"
    )
    assert await env.provider.is_empty(env.session) is False


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_unsupported_version_raises_ci_unavailable(dialect, profile, request) -> None:
    url = itsm_url(request, dialect, profile)
    packages = profile_packages(profile)
    packages["ITSMConfigurationManagement"] = "6.4.1"  # a line the survey does not cover
    seed_packages(url, packages)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            provider = ZnunyCiProvider(SysConfig(session))
            calls = (
                provider.is_empty(session),
                provider.classes(session, user_id=FULL),
                provider.search(session, CiQuery(), user_id=FULL),
                provider.get(session, CiRef("znuny", 1), user_id=FULL),
                provider.service_incident_states(session, [1]),
            )
            for call in calls:
                with pytest.raises(CiUnavailable) as caught:
                    await call
                assert caught.value.code == "unsupported_znuny_itsm_version"
    finally:
        await engine.dispose()
        clear_detect_cache()


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
async def test_missing_itsm_tables_raise_ci_unavailable(url_fixture, request) -> None:
    url = plain_url(request, url_fixture)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            with pytest.raises(CiUnavailable) as caught:
                await ZnunyCiProvider(SysConfig(session)).is_empty(session)
            assert caught.value.code == "znuny_itsm_missing"
    finally:
        await engine.dispose()
        clear_detect_cache()


def test_factory_is_registered_for_mode_a() -> None:
    provider = get_ci_provider("znuny_readthrough", SysConfig())
    assert isinstance(provider, ZnunyCiProvider)
    assert provider.source == "znuny" and provider.read_only is True
    assert get_ci_provider("off", SysConfig()) is None
