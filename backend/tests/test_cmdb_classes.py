"""Catalog and class services: definitions, versions, templates (both dialects)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RW,
    SERVER_ATTRS,
    USER_ADMIN,
    CmdbWorld,
    _dep,
    _inc,
    make_ci,
    make_class,
    parse_attrs,
    run_jobs_until_idle,
)
from tiqora.cmdb.catalog import CatalogItemExists, CatalogMisconfigured, CatalogService
from tiqora.cmdb.classes import (
    CMDB_REINDEX_KIND,
    ClassConflict,
    ClassData,
    ClassKeyInvalid,
    ClassKeyTaken,
    ClassService,
    DefinitionInvalid,
    TemplateNotFound,
)
from tiqora.cmdb.definition import AttributeDef
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.reindex import CMDB_REINDEX_JOB
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi, TiqoraJob
from tiqora.extensions import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _registry() -> Registry:
    """The real process registry: the reindex kind must be registered there (F10)."""
    return get_registry()


async def test_definition_versions_and_current(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        svc = ClassService(s)
        cls = await svc.create(
            ClassData(class_key="cmdb4srv", name="Server", group_id=GROUP_RW), user_id=USER_ADMIN
        )
        assert cls.number_prefix == "CMDB4SRV-"
        d1 = await svc.add_definition(
            cls.id, parse_attrs(SERVER_ATTRS), comment=None, user_id=USER_ADMIN
        )
        d2 = await svc.add_definition(
            cls.id, parse_attrs(SERVER_ATTRS)[:3], comment="kleiner", user_id=USER_ADMIN
        )
        await s.commit()
        assert (d1.version, d2.version) == (1, 2)
        assert (await svc.current_definition(cls.id)).id == d2.id
        assert [d.version for d in await svc.definitions(cls.id)] == [1, 2]
        assert len((await svc.definition(d1.id)).attributes) == len(SERVER_ATTRS)
        assert (await svc.current_definition(cls.id)).comment == "kleiner"


async def test_non_current_definition_keeps_current_pointer(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4old")
        svc = ClassService(s)
        cur = (await svc.current_definition(cid)).id
        old = await svc.add_definition(
            cid, parse_attrs(SERVER_ATTRS)[:1], comment=None, user_id=USER_ADMIN, make_current=False
        )
        assert old.version == 2
        assert (await svc.current_definition(cid)).id == cur


async def test_invalid_definition_rejected(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cls = await ClassService(s).create(
            ClassData(class_key="cmdb4bad", name="X", group_id=GROUP_RW), user_id=USER_ADMIN
        )
        with pytest.raises(DefinitionInvalid) as e:
            await ClassService(s).add_definition(
                cls.id,
                [AttributeDef(key="T", name="T", type="catalog")],
                comment=None,
                user_id=USER_ADMIN,
            )
        assert e.value.issues[0].code == "catalog_class_missing"
        assert await ClassService(s).definitions(cls.id) == []


async def test_class_key_rules(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        svc = ClassService(s)
        for bad in ("A", "1abc", "has-dash", "x" * 51, "UPPER"):
            with pytest.raises(ClassKeyInvalid):
                await svc.create(
                    ClassData(class_key=bad, name="X", group_id=GROUP_RW), user_id=USER_ADMIN
                )
        data = ClassData(class_key="cmdb4dup", name="X", group_id=GROUP_RW)
        await svc.create(data, user_id=USER_ADMIN)
        with pytest.raises(ClassKeyTaken):
            await svc.create(data, user_id=USER_ADMIN)
        with pytest.raises(CmdbNotFound):
            await svc.get(999_999)


async def test_stale_class_update_is_409(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        svc = ClassService(s)
        cls = await svc.create(
            ClassData(class_key="cmdb4lck", name="Alt", group_id=GROUP_RW), user_id=USER_ADMIN
        )
        await s.commit()
        data = ClassData(class_key="cmdb4lck", name="Neu", group_id=GROUP_RW, portal_visible=True)
        upd = await svc.update(
            cls.id, data, expected_lock_version=cls.lock_version, user_id=USER_ADMIN
        )
        assert (upd.name, upd.lock_version, upd.portal_visible) == ("Neu", 2, True)
        assert upd.number_prefix == "CMDB4LCK-"
        with pytest.raises(ClassConflict):
            await svc.update(cls.id, data, expected_lock_version=1, user_id=USER_ADMIN)
        with pytest.raises(ClassKeyInvalid):
            await svc.update(
                cls.id,
                ClassData(class_key="cmdb4other", name="Neu", group_id=GROUP_RW),
                expected_lock_version=2,
                user_id=USER_ADMIN,
            )
        assert (await svc.get(cls.id)).name == "Neu"


async def test_apply_template_creates_catalog_items_once(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await ClassService(s).apply_template("computer", group_id=GROUP_RW, user_id=USER_ADMIN)
        await s.commit()
        items = await CatalogService(s).items("ITSM::ConfigItem::Computer::Type")
        assert "Server" in [i.name for i in items]
        count = len(items)
        cls = await ClassService(s).get_by_key("computer")
        assert cls.current_definition_id is not None and cls.icon
        with pytest.raises(ClassKeyTaken):
            await ClassService(s).apply_template("computer", group_id=GROUP_RW, user_id=USER_ADMIN)
        assert len(await CatalogService(s).items("ITSM::ConfigItem::Computer::Type")) == count
        with pytest.raises(TemplateNotFound):
            await ClassService(s).apply_template("nope", group_id=GROUP_RW, user_id=USER_ADMIN)


async def test_apply_template_reuses_existing_catalog_items(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cat = CatalogService(s)
        await cat.create("ITSM::ConfigItem::Computer::Type", "server", "Server (eigen)")
        await ClassService(s).apply_template("computer", group_id=GROUP_RW, user_id=USER_ADMIN)
        names = [i.name for i in await cat.items("ITSM::ConfigItem::Computer::Type")]
        assert names.count("Server (eigen)") == 1


async def test_warning_state_id_and_resolve_name(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cat = CatalogService(s)
        assert await cat.resolve_name(DEPLOYMENT_STATE, "production") == await cat.resolve_name(
            DEPLOYMENT_STATE, "Production"
        )
        assert await cat.resolve_name(DEPLOYMENT_STATE, "Production") == await _dep(s, "production")
        assert await cat.resolve_name(DEPLOYMENT_STATE, "TEST/QA") == await _dep(s, "test_qa")
        assert await cat.resolve_name(DEPLOYMENT_STATE, "nonsense") is None
        warning = await cat.warning_state_id()
        assert warning == await _inc(s, "warning")
        assert (await cat.functionality_of([warning])).popitem()[1] == "warning"


async def test_state_ids_by_functionality(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        by_func = await CatalogService(s).state_ids_by_functionality(INCIDENT_STATE)
        assert by_func == {
            "operational": [await _inc(s, "operational")],
            "warning": [await _inc(s, "warning")],
            "incident": [await _inc(s, "incident")],
        }
        func = await CatalogService(s).functionality_of(
            [await _inc(s, "incident"), await _dep(s, "planned")]
        )
        assert sorted(func.values()) == ["incident", "preproductive"]


async def test_catalog_create_update_and_misconfigured_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cat = CatalogService(s)
        item = await cat.create("cmdb4::List", "a", "Alpha", position=5)
        with pytest.raises(CatalogItemExists):
            await cat.create("cmdb4::List", "a", "Again")
        upd = await cat.update(item.id, name="Alpha 2", valid=False, comment="x")
        assert (upd.name, upd.valid, upd.comment, upd.item_key) == ("Alpha 2", False, "x", "a")
        assert await cat.items("cmdb4::List") == []
        assert [i.id for i in await cat.items("cmdb4::List", include_invalid=True)] == [item.id]
        assert "cmdb4::List" in await cat.list_classes()
        # the lowest-position valid warning state wins; none valid -> misconfigured
        extra = await cat.create(
            INCIDENT_STATE, "cmdb4warn", "W0", functionality="warning", position=1
        )
        assert await cat.warning_state_id() == extra.id
        for it in await cat.items(INCIDENT_STATE):
            if it.functionality == "warning":
                await cat.update(it.id, valid=False)
        with pytest.raises(CatalogMisconfigured):
            await cat.warning_state_id()
        rows = (await s.execute(select(TiqoraCmdbCatalog.id))).scalars().all()
        assert extra.id in rows


async def test_searchable_change_enqueues_reindex_once(cmdb_db: CmdbWorld) -> None:
    reg = _registry()
    async with cmdb_db.factory() as s:
        try:
            cid = await make_class(s, key="cmdb4idx")
            dep, inc = await _dep(s, "production"), await _inc(s, "operational")
            now = utc_now()
            s.add(
                TiqoraCmdbCi(
                    number="CMDB4IDX-000001",
                    class_id=cid,
                    name="cmdb4-ci",
                    deployment_state_id=dep,
                    incident_state_id=inc,
                    cur_incident_state_id=inc,
                    created_by=USER_ADMIN,
                    created_at=now,
                    changed_by=USER_ADMIN,
                    changed_at=now,
                )
            )
            await s.flush()
            svc = ClassService(s, registry=reg)
            base = parse_attrs(SERVER_ATTRS)
            # no searchable change (label only): no job
            relabeled = [base[0].model_copy(update={"name": "Hersteller"}), *base[1:]]
            await svc.add_definition(cid, relabeled, comment=None, user_id=USER_ADMIN)
            jobs = (
                (await s.execute(select(TiqoraJob).where(TiqoraJob.kind == CMDB_REINDEX_KIND)))
                .scalars()
                .all()
            )
            assert jobs == []
            # Model becomes searchable: one job, deduped on class:<id>
            changed = [base[0], base[1].model_copy(update={"searchable": True}), *base[2:]]
            await svc.add_definition(cid, changed, comment=None, user_id=USER_ADMIN)
            again = [
                *changed[:1],
                changed[1].model_copy(update={"searchable": False}),
                *changed[2:],
            ]
            await svc.add_definition(cid, again, comment=None, user_id=USER_ADMIN)
            jobs = (
                (await s.execute(select(TiqoraJob).where(TiqoraJob.kind == CMDB_REINDEX_KIND)))
                .scalars()
                .all()
            )
            assert [(j.dedupe_key, j.status) for j in jobs] == [(f"class:{cid}", "queued")]
        finally:
            await s.rollback()
            cmdb_db.sql("DELETE FROM tiqora_job WHERE kind = :k", k=CMDB_REINDEX_KIND)


async def test_class_without_cis_needs_no_reindex(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4nci")
        base = parse_attrs(SERVER_ATTRS)
        changed = [base[0], base[1].model_copy(update={"searchable": True}), *base[2:]]
        await ClassService(s, registry=_registry()).add_definition(
            cid, changed, comment=None, user_id=USER_ADMIN
        )
        assert (await s.execute(select(TiqoraJob.id))).first() is None


def test_reindex_job_is_registered() -> None:
    assert get_registry().get(JOB_HANDLERS, CMDB_REINDEX_KIND) is CMDB_REINDEX_JOB
    assert CMDB_REINDEX_JOB.title_key == "jobs.kinds.core.cmdb_reindex"


async def test_searchable_change_reindexes_existing_cis(cmdb_db: CmdbWorld) -> None:
    """F10 end to end: Model becomes searchable, the job runs, old CIs are found."""
    from tiqora.cmdb.access import load_access
    from tiqora.cmdb.index import rebuild_index
    from tiqora.cmdb.reindex import reindex_class
    from tiqora.cmdb.schemas import CiSearchIn
    from tiqora.cmdb.search import search_cis
    from tiqora.filters.model import Filter

    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4rix")
        a = await make_ci(s, cid, "cmdb4-rix-a", attributes={"Model": [{"v": "R740"}]})
        b = await make_ci(s, cid, "cmdb4-rix-b", attributes={"Model": [{"v": "R750"}]})
        acc = await load_access(s, USER_ADMIN)

    def by_model(value: str) -> CiSearchIn:
        flt = {"field": "attr.Model", "op": "eq", "value": value}
        return CiSearchIn(
            class_ids=[cid], filter=Filter.model_validate({"version": 1, "where": flt})
        )

    async with cmdb_db.factory() as s:
        base = parse_attrs(SERVER_ATTRS)
        changed = [base[0], base[1].model_copy(update={"searchable": True}), *base[2:]]
        await ClassService(s).add_definition(cid, changed, comment=None, user_id=USER_ADMIN)
        await s.commit()
        # before the job: the filter exists but the old CIs have no index rows for it
        assert (await search_cis(s, acc, by_model("R740"))).items == []
        assert [i.id for i in (await search_cis(s, acc, CiSearchIn(q="R74"))).items] == []

    await run_jobs_until_idle(cmdb_db)

    async with cmdb_db.factory() as s:
        assert [i.id for i in (await search_cis(s, acc, by_model("R740"))).items] == [a]
        assert [i.id for i in (await search_cis(s, acc, CiSearchIn(q="R75"))).items] == [b]
        job = (
            await s.execute(select(TiqoraJob).where(TiqoraJob.kind == CMDB_REINDEX_KIND))
        ).scalar_one()
        assert job.status == "succeeded"

    # Model is not searchable any more: the stale rows go with the next rebuild
    async with cmdb_db.factory() as s:
        await ClassService(s).add_definition(cid, base, comment=None, user_id=USER_ADMIN)
        await s.commit()
    await run_jobs_until_idle(cmdb_db)
    async with cmdb_db.factory() as s:
        assert [i.id for i in (await search_cis(s, acc, CiSearchIn(q="R74"))).items] == []
        # batching: a batch size of 1 walks every CI and rebuilds both
        assert await reindex_class(cmdb_db.factory, cid, batch=1) == 2
        assert await rebuild_index(s, []) == 0
