"""Mode-B CI provider (I3): ticket links, search, get, versions, services, inert outside mode B."""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest
from sqlalchemy import func, select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    SERVICE_ID,
    SERVICE_ID_2,
    TICKET_CLOSED,
    TICKET_HIDDEN,
    TICKET_OPEN,
    USER_A,
    USER_B,
    CmdbWorld,
    _dep,
    _inc,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.provider import TiqoraCiProvider, create_tiqora_provider, tiqora_provider_factory
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.tiqora.models import TiqoraItsmLink
from tiqora.extensions import get_registry
from tiqora.itsm.ci_provider import (
    CatalogValue,
    CiAttribute,
    CiClassInfo,
    CiDetail,
    CiNotFound,
    CiPage,
    CiProvider,
    CiQuery,
    CiRef,
    CiSummary,
    CiTicketLink,
    CiUnavailable,
    CiVersionInfo,
    LinkTypeOption,
    ServiceIncidentState,
)
from tiqora.itsm.providers import CI_PROVIDERS
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

P = TiqoraCiProvider()


def _ref(ci_id: int) -> CiRef:
    return CiRef("tiqora", ci_id)


async def _link_count(s) -> int:  # type: ignore[no-untyped-def]
    return int((await s.execute(select(func.count()).select_from(TiqoraItsmLink))).scalar_one())


def test_dto_fields_match_contract() -> None:
    def names(cls: type) -> list[str]:
        return [f.name for f in dataclasses.fields(cls)]

    assert names(CiRef) == ["source", "id"]
    assert names(CiSummary) == [
        "ref",
        "number",
        "name",
        "class_key",
        "class_name",
        "deployment_state",
        "incident_state",
        "changed_at",
        "edit_url",
    ]
    assert names(CiDetail) == [
        "summary",
        "version",
        "attributes",
        "definition_version",
        "read_only",
    ]
    assert names(CiTicketLink) == ["ticket_id", "ci", "link_type", "direction", "created_at"]
    assert names(CiVersionInfo) == [
        "id",
        "seq",
        "name",
        "deployment_state",
        "incident_state",
        "created_at",
        "created_by",
        "created_by_name",
        "change_note",
    ]
    assert names(CiPage) == ["items", "total"]
    assert names(ServiceIncidentState) == ["service_id", "state", "cis_incident", "cis_warning"]
    assert names(LinkTypeOption) == ["name", "source_label", "target_label", "pointed"]
    assert names(CiClassInfo) == ["key", "name", "icon", "can_read", "can_write"]
    assert names(CatalogValue) == ["key", "name", "functionality"]
    assert names(CiAttribute) == ["key", "label", "kind", "display", "raw", "ref", "children"]


def test_provider_identity_and_registration() -> None:
    assert isinstance(P, CiProvider)
    assert P.source == "tiqora" and P.read_only is False
    factory = get_registry().get(CI_PROVIDERS, "tiqora")
    assert factory is not None and factory.mode == "tiqora"
    assert isinstance(factory.create(SysConfig.__new__(SysConfig)), TiqoraCiProvider)
    assert isinstance(create_tiqora_provider(SysConfig.__new__(SysConfig)), TiqoraCiProvider)
    assert tiqora_provider_factory().mode == "tiqora"


async def test_link_and_list_ticket_cis(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        assert await P.link_ticket(
            s, ticket_id=TICKET_OPEN, ref=_ref(ci_id), link_type="RelevantTo", user_id=USER_A
        )
        await s.commit()
        links = await P.ticket_links(s, [TICKET_OPEN, TICKET_CLOSED], user_id=USER_A)
        assert list(links) == [TICKET_OPEN]
        (link,) = links[TICKET_OPEN]
        assert link.ticket_id == TICKET_OPEN and link.link_type == "RelevantTo"
        assert link.ci.ref == _ref(ci_id) and link.ci.name == "cmdb4-a"
        assert link.ci.edit_url is None and isinstance(link.created_at, datetime)
        assert link.direction in {"source", "target", "both"}
        assert await P.ci_ticket_ids(s, _ref(ci_id), user_id=USER_A) == [TICKET_OPEN]
        # B has no ro on the ticket queue: nothing is shown
        assert await P.ticket_links(s, [TICKET_OPEN], user_id=USER_B) == {}
        assert (
            await P.unlink_ticket(
                s, ticket_id=TICKET_OPEN, ref=_ref(ci_id), link_type=None, user_id=USER_A
            )
            == 1
        )
        await s.commit()
        assert await P.ticket_links(s, [TICKET_OPEN], user_id=USER_A) == {}


async def test_link_ticket_is_idempotent_and_never_commits(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci = _ref(await make_ci(s, await make_class(s), "cmdb4-a"))
        kwargs = {"ticket_id": TICKET_OPEN, "ref": ci, "link_type": "RelevantTo", "user_id": USER_A}
        assert await P.link_ticket(s, **kwargs) is True
        assert await P.link_ticket(s, **kwargs) is False
        assert await _link_count(s) == 1
        await s.rollback()  # the provider did not commit
        assert await _link_count(s) == 0


async def test_ticket_links_hide_ci_of_unreadable_class(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        ci = await make_ci(s, cid, "cmdb4-hidden")
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("ci", str(ci)),
            target=LinkEnd("ticket", str(TICKET_OPEN)),
            link_type_key="RelevantTo",
            enforce_permissions=False,
        )
        await s.commit()
        # USER_A may read the ticket but not the class
        assert await P.ticket_links(s, [TICKET_OPEN], user_id=USER_A) == {}
        with pytest.raises(CiNotFound):
            await P.link_ticket(
                s, ticket_id=TICKET_OPEN, ref=_ref(ci), link_type="RelevantTo", user_id=USER_A
            )


async def test_unlink_ignores_link_of_other_ticket(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci = _ref(await make_ci(s, await make_class(s), "cmdb4-a"))
        await P.link_ticket(
            s, ticket_id=TICKET_OPEN, ref=ci, link_type="RelevantTo", user_id=USER_A
        )
        await s.commit()
        removed = await P.unlink_ticket(
            s, ticket_id=TICKET_CLOSED, ref=ci, link_type=None, user_id=USER_A
        )
        assert removed == 0
        assert await P.ci_ticket_ids(s, ci, user_id=USER_A) == [TICKET_OPEN]
        # a ticket without write access on the queue cannot be unlinked either
        with pytest.raises(CiNotFound):
            await P.link_ticket(
                s, ticket_id=TICKET_HIDDEN, ref=ci, link_type="RelevantTo", user_id=USER_A
            )


async def test_search_by_number_name_ip_with_exact_total(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(
            s,
            cid,
            "cmdb4-alpha",
            attributes={"NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.77"}]}}]},
        )
        b = await make_ci(s, cid, "cmdb4-beta")
        c = await make_ci(s, cid, "cmdb4-gamma")
        number = (await P.get(s, _ref(a), user_id=USER_A)).summary.number
        by_number = await P.search(s, CiQuery(text=number), user_id=USER_A)
        assert [i.ref for i in by_number.items] == [_ref(a)]
        by_ip = await P.search(s, CiQuery(text="192.0.2.77"), user_id=USER_A)
        assert [i.ref for i in by_ip.items] == [_ref(a)]
        page = await P.search(s, CiQuery(text="cmdb4-", limit=2), user_id=USER_A)
        assert len(page.items) == 2 and page.total == 3
        rest = await P.search(s, CiQuery(text="cmdb4-", limit=2, offset=2), user_id=USER_A)
        assert {i.ref for i in (*page.items, *rest.items)} == {_ref(a), _ref(b), _ref(c)}
        clamped = await P.search(s, CiQuery(text="cmdb4-", limit=10_000), user_id=USER_A)
        assert clamped.total == 3 and len(clamped.items) == 3
        # unreadable for B
        assert (await P.search(s, CiQuery(text="cmdb4-"), user_id=USER_B)).total == 0


async def test_search_filters_and_refs(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        other = await make_class(s, key="cmdb4oth")
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b", incident="incident")
        c = await make_ci(s, other, "cmdb4-c", deployment="test_qa")
        assert await _dep(s, "production") != await _dep(s, "test_qa")
        by_class = await P.search(s, CiQuery(class_keys=("cmdb4oth",)), user_id=USER_A)
        assert [i.ref for i in by_class.items] == [_ref(c)]
        by_inc = await P.search(s, CiQuery(incident_state_keys=("incident",)), user_id=USER_A)
        assert [i.ref for i in by_inc.items] == [_ref(b)]
        by_dep = await P.search(s, CiQuery(deployment_state_keys=("test_qa",)), user_id=USER_A)
        assert [i.ref for i in by_dep.items] == [_ref(c)]
        by_refs = await P.search(
            s, CiQuery(refs=(_ref(a), CiRef("znuny", a), _ref(c))), user_id=USER_A
        )
        assert {i.ref for i in by_refs.items} == {_ref(a), _ref(c)} and by_refs.total == 2
        none = await P.search(s, CiQuery(class_keys=("nope",)), user_id=USER_A)
        assert none.items == () and none.total == 0


async def test_get_detail_versions_and_errors(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci = await make_ci(
            s, cid, "cmdb4-a", attributes={"Vendor": [{"v": "ACME"}], "CPU": [{"v": 8}]}
        )
        for n in (1, 2):
            await CiService(s).update(
                system_access(USER_A),
                ci,
                expected_lock_version=None,
                name=f"cmdb4-a{n}",
                change_note=f"rename {n}",
                enforce_permissions=False,
            )
        await s.commit()
        detail = await P.get(s, _ref(ci), user_id=USER_A)
        assert detail.read_only is False and detail.summary.name == "cmdb4-a2"
        assert detail.summary.incident_state is not None
        assert detail.summary.incident_state.functionality == "operational"
        assert detail.version is not None and detail.version.seq == 3
        assert detail.version.change_note == "rename 2"
        by_key = {a.key: a for a in detail.attributes}
        assert by_key["Vendor"].display == "ACME" and by_key["Vendor"].raw == "ACME"
        assert by_key["CPU"].kind == "integer" and by_key["CPU"].raw == "8"

        versions, total = await P.versions(s, _ref(ci), user_id=USER_A, limit=2, offset=0)
        more, total2 = await P.versions(s, _ref(ci), user_id=USER_A, limit=2, offset=2)
        assert total == total2 == 3
        assert [v.seq for v in versions] == [3, 2] and [v.seq for v in more] == [1]
        old = await P.get(s, _ref(ci), user_id=USER_A, version_id=more[0].id)
        assert old.version is not None and old.version.name == "cmdb4-a"

        with pytest.raises(CiNotFound):
            await P.get(s, CiRef("znuny", ci), user_id=USER_A)
        with pytest.raises(CiNotFound):
            await P.get(s, _ref(ci), user_id=USER_B)
        with pytest.raises(CiNotFound):
            await P.get(s, _ref(2_000_000_000), user_id=USER_A)
        with pytest.raises(CiNotFound):
            await P.versions(s, _ref(ci), user_id=USER_B, limit=5, offset=0)
        got = await P.get_many(
            s, [_ref(ci), CiRef("znuny", ci), _ref(2_000_000_000)], user_id=USER_A
        )
        assert set(got) == {_ref(ci)}
        assert await P.get_many(s, [_ref(ci)], user_id=USER_B) == {}


async def test_services_classes_catalogs_and_empty(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        assert await P.is_empty(s) is True
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b", incident="incident")
        for ci in (a, b):
            await LinkService(s).add(
                system_access(USER_A),
                source=LinkEnd("service", str(SERVICE_ID)),
                target=LinkEnd("ci", str(ci)),
                link_type_key="DependsOn",
            )
        await s.commit()
        assert await P.is_empty(s) is False
        cis = await P.service_cis(s, SERVICE_ID, user_id=USER_A)
        assert {c.ref for c in cis} == {_ref(a), _ref(b)}
        assert await P.service_cis(s, SERVICE_ID_2, user_id=USER_A) == []
        assert await P.service_cis(s, SERVICE_ID, user_id=USER_B) == []
        states = await P.service_incident_states(s, [SERVICE_ID, SERVICE_ID_2])
        assert states[SERVICE_ID].state is not None
        assert states[SERVICE_ID].state.functionality == "incident"
        assert states[SERVICE_ID].cis_incident == 1
        assert states[SERVICE_ID_2].state is None
        classes = await P.classes(s, user_id=USER_A)
        assert [(c.key, c.can_read, c.can_write) for c in classes] == [("cmdb4srv", True, True)]
        assert await P.classes(s, user_id=USER_B) == []
        assert "production" in {v.key for v in await P.deployment_states(s)}
        assert {"operational", "incident"} <= {v.functionality for v in await P.incident_states(s)}
        assert await _inc(s, "incident") > 0
        options = await P.ticket_link_types(s)
        assert "RelevantTo" in {o.name for o in options}


async def test_methods_inert_outside_mode_b(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci = _ref(await make_ci(s, await make_class(s), "cmdb4-a"))
    set_mode(cmdb_db, "znuny_readthrough")
    async with cmdb_db.factory() as s:
        assert (await P.search(s, CiQuery(text="cmdb4"), user_id=USER_A)).items == ()
        assert await P.service_incident_states(s, [SERVICE_ID]) == {}
        assert await P.ticket_links(s, [TICKET_OPEN], user_id=USER_A) == {}
        assert await P.get_many(s, [ci], user_id=USER_A) == {}
        assert await P.classes(s, user_id=USER_A) == []
        assert await P.service_cis(s, SERVICE_ID, user_id=USER_A) == []
        assert await P.ticket_link_types(s) == []
        assert await P.is_empty(s) is False  # never gated: the switch logic needs the truth
        with pytest.raises(CiNotFound):
            await P.get(s, ci, user_id=USER_A)
        with pytest.raises(CiUnavailable) as err:
            await P.link_ticket(
                s, ticket_id=TICKET_OPEN, ref=ci, link_type="RelevantTo", user_id=USER_A
            )
        assert err.value.code == "cmdb_not_installed"
        with pytest.raises(CiUnavailable):
            await P.unlink_ticket(s, ticket_id=TICKET_OPEN, ref=ci, link_type=None, user_id=USER_A)
