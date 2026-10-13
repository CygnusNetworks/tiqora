"""Importer facades on the CMDB: writer -> reader round trip on both dialects.

Uses the CMDB test world (``_cmdb_helpers``): its seeds, its users and groups; the rows created
here are removed by the world's fixtures. No Znuny ITSM tables are needed.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RW,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    CmdbWorld,
    parse_attrs,
)
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.itsm.importer import facades, ports

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

T0 = datetime(2024, 1, 1)


async def _seed_class(w: ports.CmdbWritePort, key: str, group_id: int) -> tuple[int, int, int, int]:
    cid = await w.upsert_class(
        class_key=key,
        name=f"Class {key}",
        group_id=group_id,
        number_prefix="1056",
        user_id=USER_ADMIN,
    )
    did = await w.add_definition(
        class_id=cid,
        attributes=parse_attrs(SERVER_ATTRS),
        comment=None,
        created_by=USER_ADMIN,
        created_at=T0,
    )
    dep = await w.upsert_catalog_item(
        catalog_class=DEPLOYMENT_STATE,
        item_key="production",
        name="Production",
        functionality="productive",
        valid=True,
    )
    inc = await w.upsert_catalog_item(
        catalog_class=INCIDENT_STATE,
        item_key="operational",
        name="Operational",
        functionality="operational",
        valid=True,
    )
    return cid, did, dep, inc


async def test_writer_then_reader_round_trip(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        p = facades.get_ports(s)
        cid, did, dep, inc = await _seed_class(p.writer, "itim56srv", GROUP_RW)
        # skip_if_equal: the same definition again is the same definition id
        again = await p.writer.add_definition(
            class_id=cid,
            attributes=parse_attrs(SERVER_ATTRS),
            comment=None,
            created_by=USER_ADMIN,
            created_at=T0,
        )
        assert again == did
        ci = await p.writer.create_imported_ci(
            class_id=cid,
            number="1056000001",
            name="itim56 srv-db-01.example.test",
            created_by=USER_ADMIN,
            created_at=T0,
            deployment_state_id=dep,
            incident_state_id=inc,
        )
        assert await p.writer.latest_version_id(ci) is None
        attrs = {"Vendor": [{"v": "Example Corp"}]}
        v1 = await p.writer.add_imported_version(
            ci,
            definition_id=did,
            name="itim56 srv-db-01.example.test",
            deployment_state_id=dep,
            incident_state_id=inc,
            attributes=attrs,
            created_by=USER_ADMIN,
            created_at=T0,
        )
        assert v1 is not None
        unchanged = await p.writer.add_imported_version(
            ci,
            definition_id=did,
            name="itim56 srv-db-01.example.test",
            deployment_state_id=dep,
            incident_state_id=inc,
            attributes=attrs,
            created_by=USER_ADMIN,
            created_at=datetime(2024, 1, 2),
        )
        assert unchanged is None
        assert await p.writer.latest_version_id(ci) == v1
        att = await p.writer.add_imported_attachment(
            ci,
            filename="notes.txt",
            content_type="text/plain",
            data=b"example",
            created_at=T0,
            created_by=USER_ADMIN,
        )
        await p.writer.finish_import([ci])
        await s.commit()

        view = await p.reader.get_ci(ci)
        assert view is not None
        assert (view.number, view.class_key, view.version_id) == ("1056000001", "itim56srv", v1)
        assert view.attributes == attrs
        assert (view.deployment_state, view.incident_state) == ("Production", "Operational")
        assert view.definition_version == 1 and view.created_at == T0
        assert view.attachments == [(att, "notes.txt", "text/plain", 7)]
        assert [a["key"] for a in view.schema["attributes"]][0] == SERVER_ATTRS[0]["key"]
        assert (await p.reader.get_ci(ci, version_id=v1)) is not None
        assert await p.reader.get_ci(ci + 1000) is None
        assert await p.reader.ci_id_by_number("1056000001") == ci
        assert await p.reader.ci_id_by_number("nope") is None


async def test_reader_search_class_lookup_and_permissions(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        p = facades.get_ports(s)
        cid, did, dep, inc = await _seed_class(p.writer, "itim56a", GROUP_RW)
        hid, hdid, _, _ = await _seed_class(p.writer, "itim56hid", GROUP_HIDDEN)
        ids = {}
        for number, name, class_id, definition in (
            ("1056000010", "itim56 alpha", cid, did),
            ("1056000011", "itim56 beta", cid, did),
            ("1056000012", "itim56 hidden", hid, hdid),
        ):
            ci = await p.writer.create_imported_ci(
                class_id=class_id,
                number=number,
                name=name,
                created_by=USER_ADMIN,
                created_at=T0,
                deployment_state_id=dep,
                incident_state_id=inc,
            )
            await p.writer.add_imported_version(
                ci,
                definition_id=definition,
                name=name,
                deployment_state_id=dep,
                incident_state_id=inc,
                attributes={"Vendor": [{"v": "Example " + name.split()[1]}]},
                created_by=USER_ADMIN,
                created_at=T0,
            )
            ids[name] = ci
        await p.writer.finish_import(list(ids.values()))
        await s.commit()

        ro = await p.reader.visible_class_ids(USER_A, "ro")
        assert cid in ro and hid not in ro
        assert hid in await p.reader.visible_class_ids(USER_ADMIN, "rw")
        assert (await p.reader.visible_class_ids(USER_A, "rw")) <= ro

        q = ports.CiQuery
        both = {cid, hid}
        found = await p.reader.search_cis(q(name="itim56*"), class_ids=both, limit=10, offset=0)
        assert sorted(found) == sorted(ids.values())
        only_ro = await p.reader.search_cis(q(name="itim56*"), class_ids=ro, limit=10, offset=0)
        assert sorted(only_ro) == sorted([ids["itim56 alpha"], ids["itim56 beta"]])
        by_number = await p.reader.search_cis(
            q(number="1056000011"), class_ids=both, limit=10, offset=0
        )
        assert by_number == [ids["itim56 beta"]]
        paged = await p.reader.search_cis(q(name="itim56*"), class_ids={cid}, limit=1, offset=1)
        assert len(paged) == 1 and paged[0] in (ids["itim56 alpha"], ids["itim56 beta"])
        states = await p.reader.search_cis(
            q(deployment_states=["Production"], incident_states=["Operational"]),
            class_ids={cid},
            limit=10,
            offset=0,
        )
        assert len(states) == 2
        assert (
            await p.reader.search_cis(
                q(deployment_states=["No such state"]), class_ids={cid}, limit=10, offset=0
            )
            == []
        )
        assert await p.reader.search_cis(q(), class_ids=set(), limit=10, offset=0) == []
        assert (
            await p.reader.search_cis(
                q(changed_after=datetime(2999, 1, 1)), class_ids={cid}, limit=10, offset=0
            )
            == []
        )
        by_attr = await p.reader.search_cis(
            q(attributes={"Vendor": "Example beta"}), class_ids={cid}, limit=10, offset=0
        )
        assert by_attr == [ids["itim56 beta"]]

        cls = await p.reader.class_by_key_or_name("itim56a")
        assert cls is not None and (cls.id, cls.group_id, cls.definition_version) == (
            cid,
            GROUP_RW,
            1,
        )
        assert (await p.reader.class_by_key_or_name("CLASS ITIM56A")) == cls  # name, any case
        assert await p.reader.class_by_key_or_name("itim56-none") is None
        item = await p.reader.catalog_item(DEPLOYMENT_STATE, "production")
        assert item is not None and item.functionality == "productive"
        assert await p.reader.catalog_item(DEPLOYMENT_STATE, item.id) == item
        assert await p.reader.catalog_item(DEPLOYMENT_STATE, "no such") is None


async def test_links_history_delete_and_impact(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        p = facades.get_ports(s)
        cid, did, dep, inc = await _seed_class(p.writer, "itim56l", GROUP_RW)
        ci = []
        for n in range(2):
            ci.append(
                await p.writer.create_imported_ci(
                    class_id=cid,
                    number=f"105600002{n}",
                    name=f"itim56 link {n}",
                    created_by=USER_ADMIN,
                    created_at=T0,
                    deployment_state_id=dep,
                    incident_state_id=inc,
                )
            )
        args = {
            "source_kind": "ci",
            "source_key": str(ci[0]),
            "target_kind": "ci",
            "target_key": str(ci[1]),
            "link_type": "DependsOn",
            "created_by": USER_ADMIN,
            "created_at": T0,
        }
        known = {r[0] for r in cmdb_db.sql("SELECT name FROM tiqora_itsm_link_type")}
        added = await p.writer.ensure_link_types(user_id=USER_ADMIN)
        await s.commit()
        now = {r[0] for r in cmdb_db.sql("SELECT name FROM tiqora_itsm_link_type")}
        assert added >= len(now - known)
        for name in now - known:  # this module commits them, so it removes them
            cmdb_db.sql("DELETE FROM tiqora_itsm_link_type WHERE name = :n", n=name)
        assert await p.writer.add_link(**args) is True
        assert await p.writer.add_link(**args) is False  # idempotent
        written = await p.writer.import_history(
            ci[0], [("ConfigItemUpdate", "itim56", USER_ADMIN, T0)]
        )
        assert written == 1
        assert (
            await p.writer.import_history(ci[0], [("ConfigItemUpdate", "itim56", USER_ADMIN, T0)])
            == 0
        )
        await p.writer.finish_import(ci)
        await s.commit()

        view = await p.reader.impact(ci[1], max_depth=2, user_id=USER_ADMIN)
        assert isinstance(view, ports.ImpactView)
        assert (ci[0], "1056000020", "itim56 link 0") in view.cis
        assert view.truncated is False

        await p.writer.delete_ci(ci[0], user_id=USER_ADMIN)
        await s.commit()
        assert await p.reader.get_ci(ci[0]) is None
        assert await p.reader.get_ci(ci[1]) is not None
