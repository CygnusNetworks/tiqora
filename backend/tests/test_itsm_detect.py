"""Znuny ITSM detection and schema profile selection (Mode A, read-only)."""

from __future__ import annotations

import pytest

from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import (
    PLAIN_URL_FIXTURES,
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    seed_packages,
)
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache, detect_znuny_itsm
from tiqora.itsm.znuny_adapter.profiles import PROFILES, select_profile, version_matches

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("6.5.5", ">=6.5,<6.6", True),
        ("6.5", ">=6.5,<6.6", True),
        ("6.5.0", ">=6.5,<6.6", True),
        ("6.6.0", ">=6.5,<6.6", False),
        ("6.4.9", ">=6.5,<6.6", False),
        ("7.3.5", ">=7.3,<7.4", True),
        ("7.4.0", ">=7.3,<7.4", False),
        ("7.10.0", ">=7.3,<7.4", False),
        ("garbage", ">=6.5,<6.6", False),
    ],
)
def test_version_matches(version: str, spec: str, expected: bool) -> None:
    assert version_matches(version, spec) is expected


def test_version_spec_must_be_valid() -> None:
    with pytest.raises(ValueError, match="bad version spec"):
        version_matches("6.5.1", "~6.5")


def test_profiles_carry_the_survey_constants() -> None:
    assert set(PROFILES) == {"itsm-6", "itsm-7"}
    for profile in PROFILES.values():
        assert profile.definition_format == "yaml"
        assert profile.attribute_storage == "xml_storage"
        assert profile.xml_type_prefix == "ITSM::ConfigItem::"
        assert profile.xml_archive_prefix == "ITSM::ConfigItem::Archiv::"
        assert profile.service_itsm_columns == ("type_id", "criticality")
        assert profile.sla_itsm_columns == ("type_id", "min_time_bet_incidents")
        assert profile.ci_link_object == "ITSMConfigItem"
        assert profile.catalog_classes["ci_class"] == "ITSM::ConfigItem::Class"
        assert profile.catalog_classes["incident_state"] == "ITSM::Core::IncidentState"


@pytest.mark.parametrize(
    ("packages", "profile", "reason"),
    [
        ({"ITSMCore": "6.5.5", "ITSMConfigurationManagement": "6.5.4"}, "itsm-6", None),
        ({"ITSMCore": "7.3.3", "ITSMConfigurationManagement": "7.3.5"}, "itsm-7", None),
        ({"ITSMCore": "7.3.3"}, "itsm-7", None),  # no CMDB package: still a known line
        (
            {"ITSMCore": "7.3.3", "ITSMConfigurationManagement": "7.4.0"},
            None,
            "ITSMConfigurationManagement 7.4.0 not surveyed",
        ),
        ({"ITSMCore": "6.4.2"}, None, "ITSMCore 6.4.2 not surveyed"),
        (
            {"ITSMCore": "6.5.5", "ITSMConfigurationManagement": "7.3.5"},
            None,
            "ITSM packages of different Znuny lines are installed",
        ),
        ({}, None, "ITSMCore is not listed in package_repository"),
    ],
)
def test_select_profile(packages: dict[str, str], profile: str | None, reason: str | None) -> None:
    selected, why = select_profile(packages)
    assert (selected.name if selected else None) == profile
    assert why == reason


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_detect_on_itsm_db(dialect, profile, request) -> None:
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            info = await detect_znuny_itsm(session)
        assert info.core_installed and info.cmdb_installed and info.change_installed
        assert info.profile == profile
        assert info.unsupported_reason is None
        expected = profile_packages(profile)
        assert info.packages["ITSMCore"] == expected["ITSMCore"]
        assert (
            info.packages["ITSMConfigurationManagement"] == expected["ITSMConfigurationManagement"]
        )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_detect_without_package_rows_is_unsupported(dialect, profile, request) -> None:
    """The tables are there (the one predicate) but nothing says which line wrote them."""
    url = itsm_url(request, dialect, profile)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            info = await detect_znuny_itsm(session)
        assert info.core_installed is True
        assert info.profile is None
        assert info.unsupported_reason == "ITSMCore is not listed in package_repository"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
async def test_detect_plain_db(url_fixture, request) -> None:
    url = plain_url(request, url_fixture)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            info = await detect_znuny_itsm(session)
        assert info.core_installed is False
        assert info.cmdb_installed is False
        assert info.change_installed is False
        assert dict(info.packages) == {}
        assert info.profile is None
        assert info.unsupported_reason is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_detect_unsupported_version(dialect, request) -> None:
    url = itsm_url(request, dialect, "itsm-7")
    packages = profile_packages("itsm-7")
    packages["ITSMConfigurationManagement"] = "7.4.0"
    seed_packages(url, packages)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            info = await detect_znuny_itsm(session)
        assert info.core_installed is True
        assert info.profile is None
        assert info.unsupported_reason == "ITSMConfigurationManagement 7.4.0 not surveyed"
    finally:
        await engine.dispose()


async def test_only_installed_packages_count(request) -> None:
    url = itsm_url(request, "mysql", "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    from sqlalchemy import create_engine, text

    sync = create_engine(url)
    try:
        with sync.begin() as conn:
            conn.execute(
                text(
                    "UPDATE package_repository SET install_status = 'not installed'"
                    " WHERE name = 'ITSMCore'"
                )
            )
    finally:
        sync.dispose()
    clear_detect_cache()
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            info = await detect_znuny_itsm(session)
        assert "ITSMCore" not in info.packages
        assert info.profile is None
    finally:
        await engine.dispose()


async def test_detection_is_cached_for_a_minute(request) -> None:
    from sqlalchemy import create_engine, text

    url = itsm_url(request, "mysql", "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            first = await detect_znuny_itsm(session)
        assert first.profile == "itsm-6"
        sync = create_engine(url)
        try:
            with sync.begin() as conn:  # not through seed_packages: that clears the cache
                conn.execute(text("DELETE FROM package_repository"))
        finally:
            sync.dispose()
        async with factory() as session:  # a new session: its snapshot sees the deletion
            assert (await detect_znuny_itsm(session)) is first  # cached
            assert (await detect_znuny_itsm(session, use_cache=False)).profile is None
    finally:
        await engine.dispose()
