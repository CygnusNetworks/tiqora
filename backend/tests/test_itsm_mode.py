"""ITSM mode: automatic default, guarded transitions, importer switch and rollback."""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import (
    PLAIN_URL_FIXTURES,
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    register_fake_mode_b,
    seed_packages,
    store,
    stored,
)
from tiqora.itsm import mode as mode_mod
from tiqora.itsm.mode import (
    SETTING_MODE,
    SETTING_MODE_META,
    SETTING_SWITCH,
    ItsmMode,
    ItsmModeState,
    ModeTransitionNotAllowed,
    allowed_transitions,
    auto_default,
    ensure_itsm_mode_initialised,
    get_itsm_mode,
    init_itsm_mode_at_startup,
    require_itsm_enabled,
    rollback_to_znuny,
    set_itsm_mode,
    switch_to_tiqora,
)
from tiqora.itsm.znuny_adapter.detect import ZnunyItsmInfo

ALL_MODES: tuple[ItsmMode, ...] = ("off", "znuny_readthrough", "tiqora")


def _info(*, core: bool, packages: Mapping[str, str] | None = None) -> ZnunyItsmInfo:
    return ZnunyItsmInfo(
        packages=MappingProxyType(dict(packages or {})),
        core_installed=core,
        cmdb_installed=core,
        change_installed=core,
        profile="itsm-6" if core else None,
        unsupported_reason=None,
    )


def _state(
    mode: ItsmMode, *, detected: bool, previous: ItsmMode | None = None, stored_: bool = True
) -> ItsmModeState:
    return ItsmModeState(
        mode=mode,
        source="auto",
        stored=stored_,
        detected=_info(core=detected),
        changed_at=None,
        changed_by=None,
        import_run_id=None,
        previous_mode=previous,
    )


def test_auto_default() -> None:
    assert auto_default(_info(core=True)) == "znuny_readthrough"
    assert auto_default(_info(core=False)) == "tiqora"


def _expected_transitions(
    mode: ItsmMode,
    *,
    cmdb_empty: bool,
    detected: bool,
    switched: bool,
    previous: ItsmMode | None,
) -> set[ItsmMode]:
    """The rules of open question 5 + rulings F4/R6, written out independently."""
    result: set[ItsmMode] = set()
    if mode != "off":
        result.add("off")
    if mode == "off":
        if switched:
            result.add("tiqora")
        elif previous is not None:
            result.add(previous)
        else:
            result.add("znuny_readthrough" if detected else "tiqora")
    if mode == "tiqora" and detected and cmdb_empty and not switched:
        result.add("znuny_readthrough")
    return result


_MATRIX = [
    (mode, cmdb_empty, detected, switched, previous)
    for mode in ALL_MODES
    for cmdb_empty in (True, False)
    for detected in (True, False)
    for switched in (True, False)
    for previous in ((None, "znuny_readthrough", "tiqora") if mode == "off" else (None,))
]


@pytest.mark.parametrize(("mode", "cmdb_empty", "detected", "switched", "previous"), _MATRIX)
def test_allowed_transitions_matrix(mode, cmdb_empty, detected, switched, previous) -> None:
    state = _state(mode, detected=detected, previous=previous)
    got = allowed_transitions(state, tiqora_cmdb_empty=cmdb_empty, switched=switched)
    assert got == _expected_transitions(
        mode, cmdb_empty=cmdb_empty, detected=detected, switched=switched, previous=previous
    )
    assert mode not in got  # never "to the same mode"


def test_a_to_b_is_never_an_admin_transition() -> None:
    for cmdb_empty, switched in itertools.product([True, False], repeat=2):
        state = _state("znuny_readthrough", detected=True)
        got = allowed_transitions(state, tiqora_cmdb_empty=cmdb_empty, switched=switched)
        assert got == {"off"}


def test_b_to_off_to_where() -> None:
    """B -> off -> ? closes the ``off`` loophole: back to B only, never straight to A."""
    off_after_b = _state("off", detected=True, previous="tiqora")
    assert allowed_transitions(off_after_b, tiqora_cmdb_empty=True) == {"tiqora"}
    # switched by an import: only B as well, whatever was stored as previous
    assert allowed_transitions(
        _state("off", detected=True, previous="tiqora"), tiqora_cmdb_empty=True, switched=True
    ) == {"tiqora"}


def test_a_to_off_with_itsm_gone_to_where() -> None:
    """A -> off (Znuny ITSM removed meanwhile) -> ?: back to A only (no re-detection)."""
    off_after_a = _state("off", detected=False, previous="znuny_readthrough")
    assert allowed_transitions(off_after_a, tiqora_cmdb_empty=True) == {"znuny_readthrough"}


def test_off_without_previous_uses_the_auto_default() -> None:
    assert allowed_transitions(_state("off", detected=True), tiqora_cmdb_empty=True) == {
        "znuny_readthrough"
    }
    assert allowed_transitions(_state("off", detected=False), tiqora_cmdb_empty=True) == {"tiqora"}


# --- database ---------------------------------------------------------------------------------


async def _mode(factory: Any) -> ItsmModeState:
    async with factory() as session:
        return await get_itsm_mode(session, fresh=True)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_auto_default_persisted_once(dialect, profile, request) -> None:
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    engine, factory = make_factory(url)
    try:
        before = await _mode(factory)
        assert before.stored is False and before.mode == "znuny_readthrough"
        first = await ensure_itsm_mode_initialised(factory)
        assert first.mode == "znuny_readthrough"
        assert first.source == "auto" and first.stored is True
        assert first.changed_by is None and first.import_run_id is None
        assert stored(url, SETTING_MODE) == "znuny_readthrough"
        meta = json.loads(stored(url, SETTING_MODE_META) or "{}")
        assert meta["source"] == "auto" and meta["changed_at"]

        marker = stored(url, SETTING_MODE_META)
        second = await ensure_itsm_mode_initialised(factory)
        assert second.mode == "znuny_readthrough" and second.stored is True
        assert stored(url, SETTING_MODE_META) == marker  # not rewritten
        assert (await _mode(factory)).mode == "znuny_readthrough"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
async def test_plain_db_defaults_to_tiqora(url_fixture, request) -> None:
    url = plain_url(request, url_fixture)
    engine, factory = make_factory(url)
    try:
        state = await ensure_itsm_mode_initialised(factory)
        assert state.mode == "tiqora" and state.source == "auto"
        assert stored(url, SETTING_MODE) == "tiqora"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_later_znuny_itsm_install_does_not_flip(dialect, request, monkeypatch) -> None:
    """Mode B stays after Znuny ITSM is installed later."""
    url = itsm_url(request, dialect, "itsm-6")
    engine, factory = make_factory(url)
    try:
        real_detect = mode_mod.detect_znuny_itsm

        async def _no_itsm(session: Any, *, use_cache: bool = True) -> ZnunyItsmInfo:
            return _info(core=False)

        monkeypatch.setattr(mode_mod, "detect_znuny_itsm", _no_itsm)
        assert (await ensure_itsm_mode_initialised(factory)).mode == "tiqora"

        monkeypatch.setattr(mode_mod, "detect_znuny_itsm", real_detect)  # "the packages arrive"
        again = await ensure_itsm_mode_initialised(factory)
        assert again.mode == "tiqora"
        now = await _mode(factory)
        assert now.mode == "tiqora" and now.stored is True
        assert now.detected.core_installed is True  # seen, but not acted upon
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_concurrent_initialisation_keeps_first(dialect, request, monkeypatch) -> None:
    """Two processes detect different things at the same time: both end with one stored value."""
    url = itsm_url(request, dialect, "itsm-6")
    engine, factory = make_factory(url)
    try:
        barrier = asyncio.Barrier(2)
        calls = itertools.count()

        async def _racy_detect(session: Any, *, use_cache: bool = True) -> ZnunyItsmInfo:
            n = next(calls)
            if n < 2:  # both initialisers have read "nothing stored" before either inserts
                await barrier.wait()
            return _info(core=n % 2 == 0)  # first caller sees ITSM, second does not

        monkeypatch.setattr(mode_mod, "detect_znuny_itsm", _racy_detect)
        a, b = await asyncio.gather(
            ensure_itsm_mode_initialised(factory), ensure_itsm_mode_initialised(factory)
        )
        assert a.mode == b.mode  # the loser read the winner's value
        assert a.stored and b.stored
        assert stored(url, SETTING_MODE) == a.mode
        meta = json.loads(stored(url, SETTING_MODE_META) or "{}")
        assert meta["source"] == "auto"
    finally:
        await engine.dispose()


async def test_startup_hook_never_raises(caplog) -> None:
    class _Broken:
        def __call__(self) -> Any:
            raise RuntimeError("database is down")

    await init_itsm_mode_at_startup(_Broken())  # type: ignore[arg-type]


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_importer_switch_records_run(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    engine, factory = make_factory(url)
    try:
        assert (await ensure_itsm_mode_initialised(factory)).mode == "znuny_readthrough"
        async with factory() as session:
            state = await switch_to_tiqora(session, user_id=1, import_run_id=7)
            assert state.mode == "tiqora" and state.source == "importer"
            assert state.import_run_id == 7 and state.changed_by == 1
            await session.commit()
        got = await _mode(factory)
        assert (got.mode, got.source, got.import_run_id, got.changed_by) == (
            "tiqora",
            "importer",
            7,
            1,
        )
        switch = json.loads(stored(url, SETTING_SWITCH) or "{}")
        assert switch["by"] == 1 and switch["run_id"] == 7 and switch["switched_at"]
        # it is not repeatable: already in Mode B
        async with factory() as session:
            with pytest.raises(ModeTransitionNotAllowed) as exc:
                await switch_to_tiqora(session, user_id=1, import_run_id=8)
            assert exc.value.reason == "already_in_mode"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_rollback_to_znuny_after_switch(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    register_fake_mode_b(request, empty=False)  # a non-empty Tiqora CMDB must not stop the importer
    engine, factory = make_factory(url)
    try:
        await ensure_itsm_mode_initialised(factory)
        async with factory() as session:
            await switch_to_tiqora(session, user_id=1, import_run_id=7)
            await session.commit()
        async with factory() as session:
            with pytest.raises(ModeTransitionNotAllowed) as exc:  # admins cannot do this
                await set_itsm_mode(session, "znuny_readthrough", user_id=1)
            assert exc.value.reason == "tiqora_to_znuny_importer_only"
            await session.rollback()
        async with factory() as session:
            state = await rollback_to_znuny(session, user_id=2, import_run_id=9)
            assert state.mode == "znuny_readthrough" and state.source == "importer"
            assert state.import_run_id == 9 and state.changed_by == 2
            await session.commit()
        assert stored(url, SETTING_SWITCH) is None
        got = await _mode(factory)
        assert (got.mode, got.source, got.import_run_id) == ("znuny_readthrough", "importer", 9)
        async with factory() as session:  # no longer in Mode B: a second rollback is refused
            with pytest.raises(ModeTransitionNotAllowed):
                await rollback_to_znuny(session, user_id=2, import_run_id=10)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_off_returns_to_tiqora_while_switched(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    engine, factory = make_factory(url)
    try:
        await ensure_itsm_mode_initialised(factory)
        async with factory() as session:
            await switch_to_tiqora(session, user_id=1, import_run_id=7)
            await session.commit()
        async with factory() as session:
            off = await set_itsm_mode(session, "off", user_id=3)
            assert off.mode == "off" and off.source == "admin" and off.previous_mode == "tiqora"
            await session.commit()
        async with factory() as session:
            for target in ("znuny_readthrough",):  # the auto default after detection: not allowed
                with pytest.raises(ModeTransitionNotAllowed) as exc:
                    await set_itsm_mode(session, target, user_id=3)  # type: ignore[arg-type]
                assert exc.value.reason == "off_returns_to_tiqora_while_switched"
            await session.rollback()
        async with factory() as session:
            back = await set_itsm_mode(session, "tiqora", user_id=3)
            assert back.mode == "tiqora" and back.previous_mode is None
            await session.commit()
        assert stored(url, SETTING_SWITCH) is not None  # still switched
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_off_remembers_the_mode_before(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    engine, factory = make_factory(url)
    try:
        await ensure_itsm_mode_initialised(factory)  # Mode A
        async with factory() as session:
            await set_itsm_mode(session, "off", user_id=3)
            await session.commit()
        async with factory() as session:
            assert (await require_itsm_enabled_or_none(session)) is None  # gated while off
            with pytest.raises(ModeTransitionNotAllowed) as exc:
                await set_itsm_mode(session, "tiqora", user_id=3)  # not via the off loophole
            assert exc.value.reason == "off_returns_to_previous_mode"
            await session.rollback()
        async with factory() as session:
            back = await set_itsm_mode(session, "znuny_readthrough", user_id=3)
            assert back.mode == "znuny_readthrough" and back.source == "admin"
            await session.commit()
        async with factory() as session:
            assert (await require_itsm_enabled(session)).mode == "znuny_readthrough"
    finally:
        await engine.dispose()


async def require_itsm_enabled_or_none(session: Any) -> ItsmModeState | None:
    from fastapi import HTTPException

    try:
        return await require_itsm_enabled(session)
    except HTTPException as exc:
        assert exc.status_code == 409 and exc.detail == {"code": "itsm_off"}
        return None


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_invalid_stored_mode_falls_back_to_the_default(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "banana")
    engine, factory = make_factory(url)
    try:
        state = await _mode(factory)
        assert state.stored is False and state.mode == "znuny_readthrough"
    finally:
        await engine.dispose()
