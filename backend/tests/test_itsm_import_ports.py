"""Ports and facades: protocol conformance and both-dialect smoke tests (band 56000-56019)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_db import ITSM_PARAMS, to_async_url
from tests._itsm_fixtures import seed_ci, seed_ci_link
from tests._itsm_mode_helpers import profile_packages
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.importer import facades, ports
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache


def test_facades_implement_the_ports() -> None:
    # runtime_checkable protocols: attribute presence is enough to catch renames
    for cls, proto in (
        (facades.ZnunyAdapterSource, ports.ImportSource),
        (facades.CmdbServiceWriter, ports.CmdbWritePort),
        (facades.CmdbServiceReader, ports.CmdbReadPort),
        (facades.ItsmModeFacade, ports.ItsmModePort),
    ):
        missing = [n for n in proto.__protocol_attrs__ if not hasattr(cls, n)]  # type: ignore[attr-defined]
        assert missing == [], f"{cls.__name__} lacks {missing}"


def test_link_map_key_is_stable() -> None:
    link = ports.ZnunyLink(
        "ITSMConfigItem",
        "7",
        "Ticket",
        "42",
        "RelevantTo",
        "Valid",
        created_at=ports.EPOCH,
        created_by=1,
    )
    assert link.map_key == "ITSMConfigItem:7>Ticket:42:RelevantTo"


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_mode_facade_without_znuny_itsm_is_tiqora(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    engine = create_async_engine(to_async_url(sync_url))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            p = facades.get_ports(session)
            assert isinstance(p.source, ports.ImportSource)
            assert isinstance(p.writer, ports.CmdbWritePort)
            assert isinstance(p.reader, ports.CmdbReadPort)
            assert isinstance(p.mode, ports.ItsmModePort)
            assert await p.mode.znuny_itsm_present() is False  # core schema only
            assert await p.mode.effective_mode() == "tiqora"
            assert await p.mode.is_switched() is False
            assert await p.source.profile() is None
            info = await p.source.info()
            assert (info.core_installed, info.cmdb_installed) == (False, False)
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_source_reads_cis_versions_and_links(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        clear_detect_cache()
        for name, version in profile_packages(profile).items():
            await session.execute(
                text(
                    "INSERT INTO package_repository (name, version, vendor, install_status,"
                    " content, create_time, create_by, change_time, change_by)"
                    " VALUES (:n, :v, 'Znuny GmbH', 'installed', '',"
                    " '2000-01-01 00:00:00', 1, '2000-01-01 00:00:00', 1)"
                ),
                {"n": name, "v": version},
            )
        p = facades.get_ports(session)
        assert await p.mode.znuny_itsm_present() is True
        assert await p.mode.effective_mode() == "znuny_readthrough"
        assert await p.source.profile() == reader.profile.name
        start = await p.source.ci_count()
        a = await seed_ci(
            session,
            profile,
            number="56000001",
            class_name="Computer",
            name="itim56 srv-a",
            versions=[
                {"attributes": {"Vendor": "Example A"}},
                {"name": "itim56 srv-a2", "attributes": {"Vendor": "Example B"}},
            ],
        )
        b = await seed_ci(
            session, profile, number="56000002", class_name="Computer", name="itim56 srv-b"
        )
        await seed_ci_link(session, source_key=a, target_key=b, link_type="DependsOn")
        await seed_ci_link(
            session, source_key=77056, target_key=a, link_type="RelevantTo", source_object="Ticket"
        )

        assert await p.source.ci_count() == start + 2
        page = await p.source.ci_ids_page(a - 1, 2)
        assert page == [a, b]
        cis = await p.source.cis([b, a])
        assert [c.id for c in cis] == [a, b]  # id order
        assert cis[0].number == "56000001" and cis[0].last_version_id is not None

        versions = await p.source.versions([a, b])
        assert [v.name for v in versions[a]] == ["itim56 srv-a", "itim56 srv-a2"]
        assert versions[a][0].id < versions[a][1].id
        node = versions[a][1].xml_data
        assert node["Vendor"][1]["Content"] == "Example B"

        links = await p.source.links_for_cis([a])
        assert sorted(link.map_key for link in links) == sorted(
            [
                f"ITSMConfigItem:{a}>ITSMConfigItem:{b}:DependsOn",
                f"Ticket:77056>ITSMConfigItem:{a}:RelevantTo",
            ]
        )
        assert await p.source.links_for_cis([]) == []
        # a link between two requested CIs is reported once
        both = await p.source.links_for_cis([a, b])
        assert len([x for x in both if x.type_name == "DependsOn"]) == 1

        newest = await p.source.max_ci_change_time()
        assert newest is not None
        assert await p.source.count_cis_changed_since(datetime(2000, 1, 1)) >= 2
        assert await p.source.count_cis_changed_since(datetime(2999, 1, 1)) == 0
        items = await p.source.catalog_items()
        assert any(i.catalog_class == "ITSM::ConfigItem::Class" and i.valid for i in items)
        defs = await p.source.class_definitions()
        assert defs and all(d.raw and d.version >= 1 for d in defs)
        with pytest.raises(NotImplementedError):
            await p.source.attachments([a], with_data=False)
        clear_detect_cache()
