"""CMDB importer API (C6): catalog/class/definition upserts, CI header and versions, links,
attachments, history, finish_import (both dialects; rows use names containing ``cmdb4``)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RW,
    SERVER_ATTRS,
    USER_ADMIN,
    CmdbWorld,
    _inc,
    make_ci,
    make_class,
    parse_attrs,
)
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.importer_api import (
    ImportedVersion,
    add_definition,
    add_imported_attachment,
    add_imported_version,
    create_imported_ci,
    delete_ci,
    finish_import,
    import_ci,
    import_history,
    import_link,
    latest_version_id,
    upsert_catalog_item,
    upsert_class,
)
from tiqora.cmdb.links import UnknownLinkType
from tiqora.cmdb.numbering import next_ci_number
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiHistory,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClassDefinition,
    TiqoraItsmLink,
)

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

T0 = datetime(2024, 1, 1)


async def _index_values(s: AsyncSession, ci_id: int, path: str) -> list[str]:
    rows = await s.execute(
        select(TiqoraCmdbCiAttr.value_key)
        .where(TiqoraCmdbCiAttr.ci_id == ci_id, TiqoraCmdbCiAttr.attr_path == path)
        .order_by(TiqoraCmdbCiAttr.position)
    )
    return [str(v) for v in rows.scalars().all()]


async def _imported_class(s: AsyncSession, key: str = "cmdb4imp") -> tuple[int, int, int, int]:
    """(class id, definition id, deployment id, incident id) of a fresh import class."""
    cid = await upsert_class(
        s, class_key=key, name="Import", group_id=GROUP_RW, number_prefix="1022", user_id=USER_ADMIN
    )
    did = await add_definition(
        s,
        class_id=cid,
        attributes=parse_attrs(SERVER_ATTRS),
        comment=None,
        created_by=USER_ADMIN,
        created_at=T0,
    )
    dep = await upsert_catalog_item(
        s,
        catalog_class=DEPLOYMENT_STATE,
        item_key="production",
        name="Production",
        functionality="productive",
        valid=True,
    )
    inc = await upsert_catalog_item(
        s,
        catalog_class=INCIDENT_STATE,
        item_key="operational",
        name="Operational",
        functionality="operational",
        valid=True,
    )
    return cid, did, dep, inc


def _versions(did: int, dep: int, inc: int, n: int = 3) -> list[ImportedVersion]:
    return [
        ImportedVersion(
            did,
            f"cmdb4-old-{i}",
            dep,
            inc,
            {"Vendor": [{"v": f"V{i}"}]},
            USER_ADMIN,
            datetime(2024, 1, 1 + i),
        )
        for i in range(n)
    ]


async def test_import_ci_keeps_number_times_and_all_versions(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        vs = _versions(did, dep, inc)
        ci_id = await import_ci(
            s, class_id=cid, number="1022000123", created_by=USER_ADMIN, created_at=T0, versions=vs
        )
        again = await import_ci(
            s, class_id=cid, number="1022000123", created_by=USER_ADMIN, created_at=T0, versions=vs
        )
        await finish_import(s, [ci_id])
        await s.commit()
        rec = await CiService(s).get(system_access(USER_ADMIN), ci_id)
        assert again == ci_id
        assert rec.version_count == 3 and rec.name == "cmdb4-old-2"
        assert rec.number == "1022000123" and rec.created_at == T0
        assert rec.changed_at == datetime(2024, 1, 3)
        assert rec.attributes["Vendor"] == [{"v": "V2"}]
        assert await next_ci_number(s, cid) == "1022000124"  # counter bumped past the import
        assert await _index_values(s, ci_id, "Vendor") == ["v2"]
        events = (
            (
                await s.execute(
                    select(TiqoraCmdbCiHistory.event)
                    .where(TiqoraCmdbCiHistory.ci_id == ci_id)
                    .order_by(TiqoraCmdbCiHistory.id)
                )
            )
            .scalars()
            .all()
        )
        assert events == ["ConfigItemCreate", "VersionCreate", "VersionCreate", "VersionCreate"]


async def test_import_ci_appends_only_newer_versions(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        vs = _versions(did, dep, inc, 4)
        ci_id = await import_ci(
            s,
            class_id=cid,
            number="1022000200",
            created_by=USER_ADMIN,
            created_at=T0,
            versions=vs[:2],
        )
        await import_ci(
            s, class_id=cid, number="1022000200", created_by=USER_ADMIN, created_at=T0, versions=vs
        )
        await s.commit()
        count = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraCmdbCiVersion)
                .where(TiqoraCmdbCiVersion.ci_id == ci_id)
            )
        ).scalar_one()
        ci = await s.get(TiqoraCmdbCi, ci_id, populate_existing=True)
        assert count == 4
        assert ci is not None and ci.version_count == 4 and ci.name == "cmdb4-old-3"
        assert await _index_values(s, ci_id, "Vendor") == ["v3"]


async def test_import_ci_without_versions_for_new_ci_is_rejected(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, *_ = await _imported_class(s)
        with pytest.raises(ValueError, match="at least one version"):
            await import_ci(
                s,
                class_id=cid,
                number="1022000300",
                created_by=USER_ADMIN,
                created_at=T0,
                versions=[],
            )


async def test_add_imported_version_returns_none_when_unchanged(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        ci_id = await create_imported_ci(
            s,
            class_id=cid,
            number="1022000400",
            name="cmdb4-hdr",
            created_by=USER_ADMIN,
            created_at=T0,
        )
        assert await latest_version_id(s, ci_id) is None
        header = await s.get(TiqoraCmdbCi, ci_id)
        assert header is not None and header.version_count == 0
        assert header.deployment_state_id == dep and header.incident_state_id == inc  # F27 defaults
        v = ImportedVersion(did, "cmdb4-v", dep, inc, {"Vendor": [{"v": "A"}]}, USER_ADMIN, T0)
        first = await add_imported_version(s, ci_id, v)
        assert first is not None and await latest_version_id(s, ci_id) == first
        same = ImportedVersion(
            did, "cmdb4-v", dep, inc, {"Vendor": [{"v": "A"}]}, USER_ADMIN, datetime(2024, 5, 5)
        )
        assert await add_imported_version(s, ci_id, same) is None
        changed = ImportedVersion(
            did, "cmdb4-v", dep, inc, {"Vendor": [{"v": "B"}]}, USER_ADMIN, datetime(2024, 6, 6)
        )
        second = await add_imported_version(s, ci_id, changed)
        assert second is not None and second != first
        await s.commit()
        ci = await s.get(TiqoraCmdbCi, ci_id, populate_existing=True)
        assert ci is not None and ci.version_count == 2 and ci.current_version_id == second
        assert ci.changed_at == datetime(2024, 6, 6)


async def test_create_imported_ci_is_idempotent_and_keeps_creation_data(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        cid, *_ = await _imported_class(s)
        first = await create_imported_ci(
            s,
            class_id=cid,
            number="1022000500",
            name="cmdb4-a",
            created_by=USER_ADMIN,
            created_at=T0,
        )
        second = await create_imported_ci(
            s,
            class_id=cid,
            number="1022000500",
            name="cmdb4-other",
            created_by=USER_ADMIN,
            created_at=datetime(2025, 1, 1),
        )
        ci = await s.get(TiqoraCmdbCi, first)
        assert second == first
        assert ci is not None and ci.name == "cmdb4-a" and ci.created_at == T0
        with pytest.raises(CmdbNotFound):
            await create_imported_ci(
                s, class_id=999999, number="x", name="n", created_by=USER_ADMIN, created_at=T0
            )


async def test_upserts_and_definition_are_idempotent(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        cid2, did2, dep2, inc2 = await _imported_class(s)
        assert (cid2, did2, dep2, inc2) == (cid, did, dep, inc)  # nothing new
        versions = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraCmdbClassDefinition)
                .where(TiqoraCmdbClassDefinition.class_id == cid)
            )
        ).scalar_one()
        assert versions == 1
        # a different definition gets version 2; without skip_if_equal the same one too
        attrs = parse_attrs(SERVER_ATTRS)[:3]
        d2 = await add_definition(
            s, class_id=cid, attributes=attrs, comment="v2", created_by=USER_ADMIN, created_at=T0
        )
        d3 = await add_definition(
            s,
            class_id=cid,
            attributes=attrs,
            comment=None,
            created_by=USER_ADMIN,
            created_at=T0,
            skip_if_equal=False,
        )
        d4 = await add_definition(
            s, class_id=cid, attributes=attrs, comment=None, created_by=USER_ADMIN, created_at=T0
        )
        assert len({did, d2, d3}) == 3 and d4 == d3
        # updating the class and an item changes them in place
        same = await upsert_class(
            s,
            class_key="cmdb4imp",
            name="Renamed",
            group_id=GROUP_RW,
            number_prefix="1023",
            user_id=USER_ADMIN,
            portal_visible=True,
        )
        item = await upsert_catalog_item(
            s,
            catalog_class=DEPLOYMENT_STATE,
            item_key="production",
            name="In Betrieb",
            functionality="productive",
            valid=False,
        )
        assert same == cid and item == dep
        assert (await ClassService(s).get(cid)).name == "Renamed"
        assert (await CatalogService(s).get(dep)).valid is False


async def test_import_link_idempotent_and_orientation(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        kw = {"created_by": USER_ADMIN, "created_at": T0}
        assert await import_link(
            s,
            source_kind="ci",
            source_key="10",
            target_kind="ci",
            target_key="20",
            link_type_key="DependsOn",
            **kw,
        )
        assert not await import_link(
            s,
            source_kind="ci",
            source_key="10",
            target_kind="ci",
            target_key="20",
            link_type_key="DependsOn",
            **kw,
        )
        # directed: the reverse is a different link; undirected: both orders are one link
        assert await import_link(
            s,
            source_kind="ci",
            source_key="20",
            target_kind="ci",
            target_key="10",
            link_type_key="DependsOn",
            **kw,
        )
        assert await import_link(
            s,
            source_kind="ci",
            source_key="20",
            target_kind="ci",
            target_key="10",
            link_type_key="ConnectedTo",
            **kw,
        )
        assert not await import_link(
            s,
            source_kind="ci",
            source_key="10",
            target_kind="ci",
            target_key="20",
            link_type_key="ConnectedTo",
            **kw,
        )
        row = (
            await s.execute(select(TiqoraItsmLink).where(TiqoraItsmLink.link_type == "ConnectedTo"))
        ).scalar_one()
        assert (row.source_key, row.target_key, row.created_at) == ("10", "20", T0)
        with pytest.raises(UnknownLinkType):
            await import_link(
                s,
                source_kind="ci",
                source_key="1",
                target_kind="ci",
                target_key="2",
                link_type_key="cmdb4-nope",
                **kw,
            )
        await s.rollback()


async def test_attachment_import_is_idempotent_and_keeps_time(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-att")
        att = await add_imported_attachment(
            s,
            ci_id,
            filename="../manual.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4 cmdb4",
            created_at=T0,
            created_by=USER_ADMIN,
        )
        again = await add_imported_attachment(
            s,
            ci_id,
            filename="../manual.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4 cmdb4",
            created_at=datetime(2025, 1, 1),
            created_by=USER_ADMIN,
        )
        row = await s.get(TiqoraCmdbAttachment, att)
        assert again == att
        assert row is not None and row.ci_id == ci_id and row.created_at == T0
        assert row.filename == "manual.pdf" and row.size_bytes == len(b"%PDF-1.4 cmdb4")
        with pytest.raises(CmdbNotFound):
            await add_imported_attachment(
                s,
                999999,
                filename="a",
                content_type="text/plain",
                data=b"x",
                created_at=T0,
                created_by=USER_ADMIN,
            )
        await s.rollback()


async def test_import_history_skips_rows_it_has(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-hist")
        rows = [
            ("ValueUpdate", "Vendor: a -> b", USER_ADMIN, T0),
            ("NameUpdate", "x -> y", USER_ADMIN, datetime(2024, 2, 1)),
        ]
        assert await import_history(s, ci_id, rows) == 2
        assert await import_history(s, ci_id, [*rows, ("ValueUpdate", "z", USER_ADMIN, T0)]) == 1
        assert await import_history(s, ci_id, []) == 0
        stored = (
            (
                await s.execute(
                    select(TiqoraCmdbCiHistory.detail).where(
                        TiqoraCmdbCiHistory.ci_id == ci_id,
                        TiqoraCmdbCiHistory.event.in_(["ValueUpdate", "NameUpdate"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(stored) == ["Vendor: a -> b", "x -> y", "z"]
        await s.rollback()


async def test_finish_import_bumps_counter_and_recomputes_incident_state(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        bad = await _inc(s, "incident")
        pair = []
        for number, state in (("1022000900", bad), ("1022000700", inc)):
            pair.append(
                await import_ci(
                    s,
                    class_id=cid,
                    number=number,
                    created_by=USER_ADMIN,
                    created_at=T0,
                    versions=[
                        ImportedVersion(did, f"cmdb4-{number}", dep, state, {}, USER_ADMIN, T0)
                    ],
                )
            )
        # DependsOn propagates a warning from the source to the target: 700 depends on 900
        assert await import_link(
            s,
            source_kind="ci",
            source_key=str(pair[1]),
            target_kind="ci",
            target_key=str(pair[0]),
            link_type_key="DependsOn",
            created_by=USER_ADMIN,
            created_at=T0,
        )
        result = await finish_import(s, pair)
        await s.commit()
        assert result.deferred is False
        assert await next_ci_number(s, cid) == "1022000901"  # the highest number wins
        warn = await _inc(s, "warning")
        one = await s.get(TiqoraCmdbCi, pair[1], populate_existing=True)
        assert one is not None and one.cur_incident_state_id == warn
        assert (await finish_import(s, [])).updated == 0


async def test_delete_ci_removes_everything_of_the_ci(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid, did, dep, inc = await _imported_class(s)
        ci_id = await import_ci(
            s,
            class_id=cid,
            number="1022000800",
            created_by=USER_ADMIN,
            created_at=T0,
            versions=_versions(did, dep, inc, 2),
        )
        await import_link(
            s,
            source_kind="ci",
            source_key=str(ci_id),
            target_kind="service",
            target_key="740400",
            link_type_key="DependsOn",
            created_by=USER_ADMIN,
            created_at=T0,
        )
        await delete_ci(s, ci_id, user_id=USER_ADMIN)
        await s.commit()
        for model, column in (
            (TiqoraCmdbCi, TiqoraCmdbCi.id),
            (TiqoraCmdbCiVersion, TiqoraCmdbCiVersion.ci_id),
            (TiqoraCmdbCiAttr, TiqoraCmdbCiAttr.ci_id),
            (TiqoraCmdbCiHistory, TiqoraCmdbCiHistory.ci_id),
        ):
            n = (
                await s.execute(select(func.count()).select_from(model).where(column == ci_id))
            ).scalar_one()
            assert n == 0, model.__tablename__
        links = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraItsmLink)
                .where(TiqoraItsmLink.source_key == str(ci_id), TiqoraItsmLink.source_kind == "ci")
            )
        ).scalar_one()
        assert links == 0
