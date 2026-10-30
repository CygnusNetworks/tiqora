"""Golden-master: the statistics Znuny's ITSM packages install map as the checklist says.

The ITSM peers (``znuny-6.5-itsm``, ``znuny-7.3-itsm``) carry the 47 statistics of
ITSMServiceLevelManagement in ``xml_storage``. Tiqora reads them (``load_stats``), maps them
(``map_stat``) and the result must equal the mapping of the shipped fixture file and the parity
checklist ``PARITY`` of ``backend/tests/test_znuny_itsm_stats_parity.py``.

    GOLDEN_PEER=znuny-6.5-itsm just golden-up && just golden-seed
    GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm just golden-test -k itsm_stats_import
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_znuny_itsm_stats_inventory import MODULE_PREFIX, TITLES, itsm_fixture
from tests.test_znuny_itsm_stats_parity import PARITY

from tiqora.reporting.znuny_stats.mapper import StatLookups, StatMapping, map_stat
from tiqora.reporting.znuny_stats.model import ZnunyStat
from tiqora.reporting.znuny_stats.reader import load_stat_lookups, load_stats

pytestmark = [pytest.mark.golden, pytest.mark.itsm_peer]

MIN_MATCHED = 40
_FLS_200 = "ITSMStats-200-ITSMTicketFirstLevelSolutionRate.xml"
_IDS_AS_STORED = "IDs wie in Znuny hinterlegt – bitte Typ und Status prüfen."


def _key(stat: ZnunyStat) -> tuple[str, str]:
    return (stat.object_module, stat.title)


def _shape(m: StatMapping) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    assert m.definition is not None
    return (
        m.target,
        m.definition.source,
        tuple(d.key for d in m.definition.dimensions),
        tuple(x.key for x in m.definition.measures),
    )


async def _installed(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[list[ZnunyStat], StatLookups]:
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT DISTINCT xml_key FROM xml_storage WHERE xml_type = 'Stats'")
            )
        ).all()
        stats, errors, truncated = await load_stats(session, [int(r[0]) for r in rows])
        lookups = await load_stat_lookups(session, 1)
    assert errors == []
    assert not truncated
    # The peer runs in no Tiqora ITSM mode; the checklist is about Mode B (Tiqora leads).
    return stats, replace(lookups, itsm_mode="tiqora")


@pytest.mark.asyncio
async def test_installed_itsm_stats_map_like_the_fixtures(
    golden_session_factory: async_sessionmaker[AsyncSession],
    golden_peer_id: str,
) -> None:
    stats, lookups = await _installed(golden_session_factory)
    fixtures = {_key(itsm_fixture(n)): n for n in TITLES}
    assert len(fixtures) == len(TITLES), "fixture (object module, title) pairs are not unique"
    by_key: dict[tuple[str, str], list[ZnunyStat]] = {}
    for stat in stats:
        by_key.setdefault(_key(stat), []).append(stat)

    matched = 0  # distinct fixtures found on the peer
    copies = 0  # peer statistics compared (the ITSM bundle installs each stat several times)
    for key, name in sorted(fixtures.items(), key=lambda kv: kv[1]):
        if key in by_key:
            matched += 1
        for db_stat in by_key.get(key, []):
            copies += 1
            from_db = map_stat(db_stat, lookups, is_admin=True)
            from_file = map_stat(itsm_fixture(name), lookups, is_admin=True)
            assert from_db.definition is not None, f"{name}: peer stat {db_stat.stat_id}"
            assert _shape(from_db) == _shape(from_file), name
            assert _shape(from_db) == PARITY[name], name

    only_peer = sorted(
        f"{m.removeprefix(MODULE_PREFIX)}: {t}"
        for (m, t) in by_key
        if (m, t) not in fixtures and m.startswith(MODULE_PREFIX) and "ITSM" in m
    )
    only_fixture = sorted(n for k, n in fixtures.items() if k not in by_key)
    print(f"\n[golden {golden_peer_id}] matched {matched} of {len(fixtures)} fixtures")
    print(f"[golden {golden_peer_id}] peer stats compared: {copies}")
    print(f"[golden {golden_peer_id}] ITSM-module stats on the peer without fixture: {only_peer}")
    print(f"[golden {golden_peer_id}] fixtures without a peer stat: {only_fixture}")
    assert matched >= MIN_MATCHED


@pytest.mark.asyncio
async def test_installed_fls_stats_carry_the_id_issue(
    golden_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stats, lookups = await _installed(golden_session_factory)
    wanted = _key(itsm_fixture(_FLS_200))
    db_stats = [s for s in stats if _key(s) == wanted]
    assert db_stats, "statistic 200 is not installed on the peer"
    for db_stat in db_stats:
        assert db_stat.origin == "db"
        m = map_stat(db_stat, lookups, is_admin=True)
        assert m.definition is not None
        approx = [(i.element, i.reason) for i in m.issues if i.severity == "approximated"]
        assert ("UseAsXvalue.TypeIDs", _IDS_AS_STORED) in approx, db_stat.stat_id
        assert ("UseAsRestriction.StateIDs", _IDS_AS_STORED) in approx, db_stat.stat_id
        assert m.definition.parameters == [], db_stat.stat_id
