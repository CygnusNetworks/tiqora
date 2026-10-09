"""Working time between two instants + the calendar resolver + the reporting settings round trip.

Seed ids: 47520-47529 (queue 47520, sysconfig_default 47520; names contain ``rep47``).
The session-scoped containers are shared, so every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.reporting.settings import (
    REPORTING_SECTION,
    ReportingSettings,
    load_reporting_settings,
    reporting_time_zone,
)
from tiqora.reporting.working_time import (
    CalendarResolver,
    EscalationTargets,
    WorkingCalendar,
    due_at,
    from_epoch,
    to_epoch,
    working_minutes,
)
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny import escalation
from tiqora.znuny.escalation import destination_time_epoch, working_seconds_between
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

WH = {d: list(range(8, 17)) for d in ("Mon", "Tue", "Wed", "Thu", "Fri")}  # 08:00-17:00
TZ = "Europe/Berlin"
QUEUE_ID = 47520
_URLS: set[str] = set()
SYSCONFIG_ID = 47520
SETTING_FIELDS = ("time_zone", "near_miss_percent", "access")


def _epoch(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(TZ)).timestamp())


# --------------------------------------------------------------------------- pure


def test_same_working_day() -> None:
    assert (
        working_seconds_between(
            _epoch("2024-06-03T09:00"), _epoch("2024-06-03T11:30"), WH, {}, {}, TZ
        )
        == 9000
    )


def test_overnight_and_weekend_are_skipped() -> None:
    # Fri 16:00 -> Mon 09:00 = 1 h Friday + 1 h Monday
    assert (
        working_seconds_between(
            _epoch("2024-06-07T16:00"), _epoch("2024-06-10T09:00"), WH, {}, {}, TZ
        )
        == 7200
    )


def test_vacation_days_are_skipped() -> None:
    vd = {12: {25: "Weihnachten"}}
    assert (
        working_seconds_between(
            _epoch("2024-12-24T16:00"), _epoch("2024-12-26T09:00"), WH, vd, {}, TZ
        )
        == 7200
    )


def test_one_time_vacation_is_skipped() -> None:
    once = {2024: {6: {4: "Betriebsausflug"}}}
    assert (
        working_seconds_between(
            _epoch("2024-06-03T16:00"), _epoch("2024-06-05T09:00"), WH, {}, once, TZ
        )
        == 7200
    )


def test_stop_before_start_and_no_hours_give_zero() -> None:
    assert working_seconds_between(10_000, 5_000, WH, {}, {}, TZ) == 0
    assert working_seconds_between(10_000, 10_000, WH, {}, {}, TZ) == 0
    assert working_seconds_between(0, 86_400, {}, {}, {}, TZ) == 0
    assert working_seconds_between(0, 86_400, {"Mon": []}, {}, {}, TZ) == 0


def test_stop_inside_first_day_is_not_capped_to_the_whole_day() -> None:
    # R-DELTA: Mon 00:00 -> 10:00 with 08-17 hours is 2 h (a capped whole-day
    # chunk would give 9 h).
    assert (
        working_seconds_between(
            _epoch("2024-06-03T00:00"), _epoch("2024-06-03T10:00"), WH, {}, {}, TZ
        )
        == 7200
    )


def test_whole_day_chunk_needs_more_than_24h_remaining() -> None:
    # Mon 00:00 -> Wed 00:00 (48 h remaining): two full working days
    assert (
        working_seconds_between(
            _epoch("2024-06-03T00:00"), _epoch("2024-06-05T00:00"), WH, {}, {}, TZ
        )
        == 2 * 9 * 3600
    )
    # exactly 24 h remaining is not a chunk but still adds the same via the hour loop
    assert (
        working_seconds_between(
            _epoch("2024-06-03T00:00"), _epoch("2024-06-04T00:00"), WH, {}, {}, TZ
        )
        == 9 * 3600
    )


def test_dst_days_are_counted_in_local_hours() -> None:
    # 2024-03-31 is the spring-forward Sunday; Fri + Mon (2 x 9 h) are counted across it
    assert (
        working_seconds_between(
            _epoch("2024-03-29T00:00"), _epoch("2024-04-02T00:00"), WH, {}, {}, TZ
        )
        == 2 * 9 * 3600
    )


@pytest.mark.parametrize(
    "start", ["2024-03-29T15:10", "2024-10-25T07:00", "2024-06-05T12:34", "2024-06-08T03:00"]
)
@pytest.mark.parametrize("minutes", [1, 59, 60, 61, 480, 2000])
def test_inverse_of_destination_time(start: str, minutes: int) -> None:  # both DST weekends
    s = _epoch(start)
    dest = destination_time_epoch(s, minutes, WH, {}, {}, TZ)
    assert working_seconds_between(s, dest, WH, {}, {}, TZ) == minutes * 60


@pytest.mark.parametrize("name", ["", "0", None])
def test_falsy_vacation_name_is_no_vacation_day_like_perl(name: str | None) -> None:
    # DateTime.pm:869-870 `!$TimeVacationDays->{$M}->{$D}`: undef/""/"0" do not block the day.
    vd = {12: {25: name}}
    once = {2024: {12: {25: name}}}
    # Tue 24.12. 16:00 -> Thu 26.12. 09:00 = 1 h + 9 h (Wed) + 1 h
    for vacation, vacation_once in ((vd, {}), ({}, once)):
        assert (
            working_seconds_between(
                _epoch("2024-12-24T16:00"),
                _epoch("2024-12-26T09:00"),
                WH,
                vacation,  # type: ignore[arg-type]
                vacation_once,  # type: ignore[arg-type]
                TZ,
            )
            == 11 * 3600
        )
        # Add: 2 working hours from Tue 16:00 end Wed 09:00 (Wed is a working day)
        assert destination_time_epoch(
            _epoch("2024-12-24T16:00"),
            120,
            WH,
            vacation,  # type: ignore[arg-type]
            vacation_once,  # type: ignore[arg-type]
            TZ,
        ) == _epoch("2024-12-25T09:00")


def test_truthy_vacation_name_still_blocks_destination_time() -> None:
    assert destination_time_epoch(
        _epoch("2024-12-24T16:00"), 120, WH, {12: {25: "Weihnachten"}}, {}, TZ
    ) == _epoch("2024-12-26T09:00")


def test_working_hours_on_dst_days_count_local_hours() -> None:
    sunday = {"Sun": [1, 2, 3]}
    # 2024-10-27 has 25 local hours (02:00 occurs twice): hours 1, 2, 2, 3 = 4 h
    assert (
        working_seconds_between(
            _epoch("2024-10-27T00:00"), _epoch("2024-10-28T00:00"), sunday, {}, {}, TZ
        )
        == 4 * 3600
    )
    # 2024-03-31 has 23 local hours (02:00 does not exist): hours 1, 3 = 2 h
    assert (
        working_seconds_between(
            _epoch("2024-03-31T00:00"), _epoch("2024-04-01T00:00"), sunday, {}, {}, TZ
        )
        == 2 * 3600
    )


def test_working_minutes_none_without_hours() -> None:
    cal = WorkingCalendar(
        name=None, working_hours={}, vacation_days={}, vacation_days_once={}, time_zone=TZ
    )
    assert working_minutes(datetime(2024, 6, 3, 7), datetime(2024, 6, 3, 9), cal, "UTC") is None
    assert due_at(datetime(2024, 6, 3, 7), 30, cal, "UTC") == datetime(2024, 6, 3, 7, 30)


def test_working_minutes_truncates_like_znuny() -> None:
    cal = WorkingCalendar(
        name=None, working_hours=WH, vacation_days={}, vacation_days_once={}, time_zone=TZ
    )
    # storage UTC; 07:00Z = 09:00 Berlin (summer): 59 s later -> 0 whole minutes
    assert (
        working_minutes(datetime(2024, 6, 3, 7, 0), datetime(2024, 6, 3, 7, 0, 59), cal, "UTC") == 0
    )
    assert (
        working_minutes(datetime(2024, 6, 3, 7, 0), datetime(2024, 6, 3, 7, 1, 59), cal, "UTC") == 1
    )
    # 14:30Z-15:30Z = 16:30-17:30 Berlin -> only 30 working minutes
    assert (
        working_minutes(datetime(2024, 6, 3, 14, 30), datetime(2024, 6, 3, 15, 30), cal, "UTC")
        == 30
    )


def test_due_at_round_trips_storage_zone() -> None:
    cal = WorkingCalendar(
        name=None, working_hours=WH, vacation_days={}, vacation_days_once={}, time_zone=TZ
    )
    # 15:30Z = 17:30 Berlin (outside hours); +60 working min -> Tue 08:00-09:00 Berlin = 07:00Z
    assert due_at(datetime(2024, 6, 3, 15, 30), 60, cal, "UTC") == datetime(2024, 6, 4, 7, 0)
    start = datetime(2024, 6, 3, 12, 0)
    assert from_epoch(to_epoch(start, "UTC"), "UTC") == start
    assert from_epoch(to_epoch(start, TZ), TZ) == start


def test_to_epoch_is_the_escalation_function() -> None:
    assert to_epoch is escalation.to_epoch
    assert escalation._to_epoch is escalation.to_epoch
    assert escalation._calendar_config is escalation.calendar_config
    assert escalation._escalation_preferences is escalation.escalation_preferences


# --------------------------------------------------------------------------- DB

URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    """Delete only this module's rows; the tiqora_* tables may not exist yet."""
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine, tables=[TiqoraSettings.__table__])  # type: ignore[list-item]
        keys = [REPORTING_SECTION.storage_key(name) for name in SETTING_FIELDS]
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": QUEUE_ID})
            conn.execute(text("DELETE FROM sysconfig_default WHERE id = :id"), {"id": SYSCONFIG_ID})
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(keys)))
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _sysconfig_values() -> dict[str, Any]:
    return {
        "OTRSTimeZone": yaml_encode_effective("UTC"),
        "TimeZone::Calendar1Name": yaml_encode_effective("Berlin"),
        "TimeZone::Calendar1": yaml_encode_effective(TZ),
        "TimeWorkingHours::Calendar1": yaml_encode_effective(
            {d: [str(h) for h in hours] for d, hours in WH.items()}
        ),
        "TimeVacationDays::Calendar1": yaml_encode_effective({12: {25: "Weihnachten"}}),
        "TimeVacationDaysOneTime::Calendar1": yaml_encode_effective({}),
    }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_calendar_resolver_targets_and_caching(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    sync = create_engine(sync_url)
    with sync.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, first_response_time,"
                " update_time, solution_time, calendar_name,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, 1, 1, 1, 1, 0, 1, 60, 120, 480, '1',"
                " '2024-06-01 00:00:00', 1, '2024-06-01 00:00:00', 1)"
            ),
            {"id": QUEUE_ID, "name": f"rep47-queue-{QUEUE_ID}"},
        )
    sync.dispose()

    values = _sysconfig_values()
    fetched: list[str] = []

    async def fetch(name: str) -> Any | None:
        fetched.append(name)
        return values.get(name)

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            # ttl 0: SysConfig's own cache never answers, so only the resolver can avoid fetches
            resolver = CalendarResolver(s, SysConfig(fetch=fetch, ttl_seconds=0))
            targets = await resolver.targets(None, QUEUE_ID)
            assert targets == EscalationTargets(60, 120, 480, "1")
            assert await resolver.targets(None, QUEUE_ID) is targets
            assert await resolver.targets(None, QUEUE_ID + 1) == EscalationTargets(0, 0, 0, None)

            cal = await resolver.calendar(targets.calendar_name)
            assert cal.name == "1"
            assert cal.time_zone == TZ
            assert cal.has_working_hours
            assert cal.working_hours["Mon"] == list(range(8, 17))
            assert cal.vacation_days == {12: {25: "Weihnachten"}}

            first_calls = len(fetched)
            assert first_calls > 0
            assert await resolver.calendar("1") is cal
            assert len(fetched) == first_calls  # second call hit no SysConfig.get

            assert await resolver.storage_time_zone() == "UTC"
            n = len(fetched)
            assert await resolver.storage_time_zone() == "UTC"
            assert len(fetched) == n

            # no calendar name -> the default calendar (no TimeWorkingHours set -> no hours)
            default_cal = await resolver.calendar(None)
            assert default_cal.name is None
            assert default_cal.time_zone == "UTC"
            assert not default_cal.has_working_hours
    finally:
        await engine.dispose()


async def test_calendar_config_keeps_null_vacation_names_falsy() -> None:
    values = _sysconfig_values()
    values["TimeVacationDays::Calendar1"] = yaml_encode_effective({12: {25: None, 26: "Stefan"}})
    values["TimeVacationDaysOneTime::Calendar1"] = yaml_encode_effective({2024: {6: {4: None}}})

    async def fetch(name: str) -> Any | None:
        return values.get(name)

    _, vacation, once, _tz = await escalation.calendar_config(
        SysConfig(fetch=fetch, ttl_seconds=0), "1"
    )
    assert vacation == {12: {25: "", 26: "Stefan"}}  # not the string "None"
    assert once == {2024: {6: {4: ""}}}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_reporting_settings_round_trip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)  # also creates tiqora_settings
    sync = create_engine(sync_url)
    with sync.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation,"
                " is_invisible, is_readonly, is_required, is_valid, has_configlevel,"
                " user_modification_possible, user_modification_active, xml_content_raw,"
                " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'OTRSTimeZone', 'rep47', 'Core', 0, 0, 0, 1, 0, 0, 0, 'x', 'x',"
                " 'Framework.xml', :eff, 0, '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": SYSCONFIG_ID, "eff": yaml_encode_effective("Europe/Paris").decode()},
        )
    sync.dispose()

    settings = Settings()
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            defaults = await load_reporting_settings(s, settings)
            assert defaults == ReportingSettings()
            assert defaults.time_zone == ""
            # empty zone falls back to the Znuny default zone (seeded OTRSTimeZone)
            assert await reporting_time_zone(s, defaults) == "Europe/Paris"

            await save_section_values(
                s,
                REPORTING_SECTION,
                {"time_zone": "Europe/Berlin", "near_miss_percent": 25, "access": "stats_group"},
                secret_key=settings.secret_key,
            )
            await s.commit()
            loaded = await load_reporting_settings(s, settings)
            assert loaded.time_zone == "Europe/Berlin"
            assert loaded.near_miss_percent == 25
            assert loaded.access == "stats_group"
            assert loaded.cache_seconds == 300  # untouched fields keep their defaults
            assert await reporting_time_zone(s, loaded) == "Europe/Berlin"

            # an invalid stored zone is ignored -> default zone again
            bad = loaded.model_copy(update={"time_zone": "Mars/Olympus"})
            assert await reporting_time_zone(s, bad) == "Europe/Paris"
    finally:
        await engine.dispose()
