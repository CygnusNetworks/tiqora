"""CMDB mode seam, class-group access and the settings section (both dialects)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    GROUP_RW,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    client_for,
    make_class,
    set_mode,
)
from tiqora.cmdb.access import (
    load_access,
    readable_class_ids,
    require_class_access,
    writable_class_ids,
)
from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.gate import (
    CmdbUnavailable,
    current_itsm_mode,
    require_tiqora_cmdb,
    reset_mode_cache,
)
from tiqora.cmdb.settings import CMDB_SECTION, CmdbSettings, load_cmdb_settings
from tiqora.extensions import get_registry
from tiqora.itsm.mode import get_itsm_mode
from tiqora.settings_sections.section import SETTINGS_SECTIONS
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

GROUP_GATE = 740013


async def test_access_groups(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a = await load_access(s, USER_A)
        b = await load_access(s, USER_B)
        adm = await load_access(s, USER_ADMIN)
    assert a.can_write_group(GROUP_RW) and a.can_read_group(GROUP_RO)
    assert not a.can_write_group(GROUP_RO)
    assert b.can_read_group(GROUP_RO) and not b.can_read_group(GROUP_RW)
    assert adm.is_admin and adm.can_write_group(GROUP_HIDDEN)
    assert not a.is_admin and not a.can_read_group(GROUP_HIDDEN)


async def test_require_class_access_hides_unreadable_as_not_found(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        with pytest.raises(CmdbNotFound):
            await require_class_access(s, await load_access(s, USER_A), cid, "ro")
        with pytest.raises(CmdbNotFound):
            await require_class_access(s, await load_access(s, USER_A), 999_999, "ro")


async def test_readable_but_not_writable_is_forbidden(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ro = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        rw = await make_class(s, key="cmdb4rw", group_id=GROUP_RW)
        hidden = await make_class(s, key="cmdb4hid2", group_id=GROUP_HIDDEN)
        a = await load_access(s, USER_A)
        assert (await require_class_access(s, a, ro, "ro")).id == ro
        with pytest.raises(CmdbForbidden):
            await require_class_access(s, a, ro, "rw")
        assert (await require_class_access(s, a, rw, "rw")).id == rw
        assert await readable_class_ids(s, a) == sorted([ro, rw])
        assert await writable_class_ids(s, a) == [rw]
        b = await load_access(s, USER_B)
        assert await readable_class_ids(s, b) == [ro]
        assert await writable_class_ids(s, b) == []
        adm = await load_access(s, USER_ADMIN)
        assert await readable_class_ids(s, adm) == sorted([ro, rw, hidden])
        assert (await require_class_access(s, adm, hidden, "rw")).id == hidden


async def test_access_group_setting_gates_non_admins(cmdb_db: CmdbWorld) -> None:
    cmdb_db.sql(
        "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:g, 'cmdb4-gate', 1, NOW(), 1, NOW(), 1)"
        if cmdb_db.dialect == "mysql"
        else "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:g, 'cmdb4-gate', 1, now(), 1, now(), 1)",
        g=GROUP_GATE,
    )
    try:
        async with cmdb_db.factory() as s:
            await save_section_values(
                s, CMDB_SECTION, {"access_group": "cmdb4-gate"}, secret_key="k"
            )
            await s.commit()
        async with cmdb_db.factory() as s:
            assert not (await load_access(s, USER_A)).can_read_group(GROUP_RW)
            assert (await load_access(s, USER_ADMIN)).is_admin
        cmdb_db.sql(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, 'rw', "
            + ("NOW()" if cmdb_db.dialect == "mysql" else "now()")
            + ", 1, "
            + ("NOW()" if cmdb_db.dialect == "mysql" else "now()")
            + ", 1)",
            u=USER_A,
            g=GROUP_GATE,
        )
        async with cmdb_db.factory() as s:
            assert (await load_access(s, USER_A)).can_write_group(GROUP_RW)
    finally:
        cmdb_db.sql("DELETE FROM group_user WHERE group_id = :g", g=GROUP_GATE)
        cmdb_db.sql("DELETE FROM permission_groups WHERE id = :g", g=GROUP_GATE)
        cmdb_db.sql(
            "DELETE FROM tiqora_settings WHERE "
            + ("`key`" if cmdb_db.dialect == "mysql" else '"key"')
            + " LIKE 'cmdb.%'"
        )


async def test_mode_seam_defaults_to_tiqora_and_reads_setting(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        assert await current_itsm_mode(s) == "tiqora"
        await require_tiqora_cmdb(s)
    set_mode(cmdb_db, "znuny_readthrough")
    async with cmdb_db.factory() as s:
        assert await current_itsm_mode(s) == "znuny_readthrough"
        state = await get_itsm_mode(s)  # the gate is the ITSM core's resolver, nothing of its own
        assert state.stored and state.mode == "znuny_readthrough"
        with pytest.raises(CmdbUnavailable) as e:
            await require_tiqora_cmdb(s)
    assert e.value.mode == "znuny_readthrough"
    set_mode(cmdb_db, "off")
    async with cmdb_db.factory() as s:
        with pytest.raises(CmdbUnavailable) as e2:
            await require_tiqora_cmdb(s)
    assert e2.value.mode == "off"


async def test_mode_seam_detects_znuny_itsm_marker_tables(cmdb_db: CmdbWorld) -> None:
    engine = create_engine(cmdb_db.sync_url)
    try:
        existing = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    if {"general_catalog", "cip_allocate"} & existing:
        pytest.skip("Znuny ITSM tables already present in the test schema")
    cmdb_db.sql("CREATE TABLE general_catalog (id INT)")
    reset_mode_cache()
    try:
        async with cmdb_db.factory() as s:  # only one marker table: still Mode B
            assert await current_itsm_mode(s) == "tiqora"
        cmdb_db.sql("CREATE TABLE cip_allocate (id INT)")
        reset_mode_cache()
        try:
            async with cmdb_db.factory() as s:
                assert await current_itsm_mode(s) == "znuny_readthrough"
                state = await get_itsm_mode(s)
                assert not state.stored and state.detected.core_installed
            async with client_for(cmdb_db, USER_A) as c:  # auto default Mode A: CMDB routes 409
                r = await c.get("/api/v1/cmdb/classes")
            assert r.status_code == 409, r.text
            set_mode(cmdb_db, "tiqora")  # a stored mode wins over detection
            async with cmdb_db.factory() as s:
                assert await current_itsm_mode(s) == "tiqora"
        finally:
            cmdb_db.sql("DROP TABLE cip_allocate")
    finally:
        cmdb_db.sql("DROP TABLE general_catalog")
        reset_mode_cache()


async def test_mode_seam_caches_the_marker_table_check(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ITSM core's detection is cached: gated requests do not inspect the schema each time."""
    from tiqora.itsm.znuny_adapter import detect

    calls: list[int] = []
    real = detect._table_flags

    def counting(conn: Any) -> Any:
        calls.append(1)
        return real(conn)

    monkeypatch.setattr(detect, "_table_flags", counting)
    reset_mode_cache()
    for _ in range(3):
        async with cmdb_db.factory() as s:
            assert await current_itsm_mode(s) == "tiqora"
    set_mode(cmdb_db, "tiqora")  # a stored mode needs no extra probe either
    for _ in range(3):
        async with cmdb_db.factory() as s:
            await require_tiqora_cmdb(s)
    assert len(calls) == 1
    reset_mode_cache()


async def test_settings_defaults(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        values = await load_cmdb_settings(s)
    assert values == CmdbSettings()
    assert values.import_max_mb == 25 and values.access_group == ""
    with pytest.raises(ValueError):
        CmdbSettings(import_max_mb=26)


def test_cmdb_settings_section_registered() -> None:
    assert get_registry().get(SETTINGS_SECTIONS, "cmdb") is CMDB_SECTION
