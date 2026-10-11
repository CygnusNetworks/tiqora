"""Generic Interface error handling: RequestRetry vectors of Znuny's RequestRetry.t and
ErrorHandling.t plus the Tiqora specifics (pure, in-memory sink)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from tiqora.gi.config import DebuggerConfig, ErrorHandlingModule
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.error_handling import (
    ERROR_STAGES,
    ErrorHandlingConfigError,
    PastExecutionData,
    handle_error,
    request_retry,
)

NOW = datetime(2026, 1, 15, 10, 0, 0)


def at(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


def s(seconds: int) -> str:
    return at(seconds).strftime("%Y-%m-%d %H:%M:%S")


def cfg(**kw: Any) -> dict[str, Any]:
    return {"ScheduleRetry": "1", "RetryIntervalStart": "60", "RetryIntervalFactor": "2", **kw}


def past(initial: int, count: int = 0, interval: int | None = None, retry: int | None = None):
    return PastExecutionData(at(initial), count, interval, at(retry) if retry is not None else None)


def data(**kw: Any) -> dict[str, Any]:
    base = {
        "ReSchedule": 0,
        "InitialRequestDateTime": s(0),
        "CurrentRequestDateTime": s(0),
        "CurrentRetryCount": 0,
        "MaximumRetryCountReached": 0,
        "MaximumRetryPeriodReached": 0,
    }
    return {**base, **kw}


# (name, module config, past, expected Data, expected ReScheduleData) - RequestRetry.t
BASE = {"ScheduleRetry": 1, "RetryIntervalStart": 60, "RetryCountMax": 100, "RetryPeriodMax": 86400}
RETRY_CASES: list[tuple[str, dict[str, Any], PastExecutionData | None, dict, dict]] = [
    (
        "Retry functionality disabled",
        {"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
        None,
        data(),
        {"ReSchedule": 0},
    ),
    (
        "Maximum retry count reached",
        {
            **BASE,
            "RetryIntervalStart": 0,
            "RetryIntervalFactor": 1,
            "RetryCountMax": 99,
            "RetryPeriodMax": 86401,
        },
        past(-86400, 99),
        data(InitialRequestDateTime=s(-86400), CurrentRetryCount=99, MaximumRetryCountReached=1),
        {"ReSchedule": 0},
    ),
    (
        "Maximum retry period reached",
        {**BASE, "RetryIntervalStart": 0, "RetryIntervalFactor": 1, "RetryCountMax": 100},
        past(-86400, 99),
        data(InitialRequestDateTime=s(-86400), CurrentRetryCount=99, MaximumRetryPeriodReached=1),
        {"ReSchedule": 0},
    ),
    (
        "Linear interval",
        {**BASE, "RetryIntervalFactor": 1},
        past(-60, 1, 60),
        data(ReSchedule=1, InitialRequestDateTime=s(-60), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(60),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-60),
                "RetryCount": 2,
                "RetryInterval": 60,
                "RetryDateTime": s(60),
            },
        },
    ),
    (
        "Linear interval, cutting to maximum period",
        {**BASE, "RetryIntervalFactor": 1},
        past(-86341, 1, 60),
        data(ReSchedule=1, InitialRequestDateTime=s(-86341), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(59),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-86341),
                "RetryCount": 2,
                "RetryInterval": 60,
                "RetryDateTime": s(59),
            },
        },
    ),
    (
        "Linear interval, execution time overdue (e.g. Daemon was down)",
        {**BASE, "RetryIntervalFactor": 1},
        past(-300, 1, 60, -240),
        data(
            ReSchedule=1,
            InitialRequestDateTime=s(-300),
            CurrentRequestDateTime=s(-240),
            CurrentRetryCount=1,
        ),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(1),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-300),
                "RetryCount": 2,
                "RetryInterval": 60,
                "RetryDateTime": s(1),
            },
        },
    ),
    (
        "Increasing interval (1.5 times)",
        {**BASE, "RetryIntervalFactor": 1.5},
        past(-60, 1, 60),
        data(ReSchedule=1, InitialRequestDateTime=s(-60), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(90),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-60),
                "RetryCount": 2,
                "RetryInterval": 90,
                "RetryDateTime": s(90),
            },
        },
    ),
    (
        "Increasing interval (2 times)",
        {**BASE, "RetryIntervalFactor": 2},
        past(-60, 1, 60),
        data(ReSchedule=1, InitialRequestDateTime=s(-60), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(120),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-60),
                "RetryCount": 2,
                "RetryInterval": 120,
                "RetryDateTime": s(120),
            },
        },
    ),
    (
        "Increasing interval (2 times), max reached",
        {**BASE, "RetryIntervalFactor": 2, "RetryIntervalMax": 100},
        past(-60, 1, 60),
        data(ReSchedule=1, InitialRequestDateTime=s(-60), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(100),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-60),
                "RetryCount": 2,
                "RetryInterval": 100,
                "RetryDateTime": s(100),
            },
        },
    ),
    (
        "Increasing interval with rounding",
        {**BASE, "RetryIntervalFactor": 1.5},
        past(-61, 1, 61),
        data(ReSchedule=1, InitialRequestDateTime=s(-61), CurrentRetryCount=1),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(91),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-61),
                "RetryCount": 2,
                "RetryInterval": 91,
                "RetryDateTime": s(91),
            },
        },
    ),
    (
        "Test with complete config and retry information",
        {
            "ScheduleRetry": 1,
            "RetryIntervalStart": 65,
            "RetryIntervalFactor": 1.5,
            "RetryIntervalMax": 120,
            "RetryCountMax": 4,
            "RetryPeriodMax": 300,
        },
        past(-123, 2, 97, -3),
        data(
            ReSchedule=1,
            InitialRequestDateTime=s(-123),
            CurrentRequestDateTime=s(-3),
            CurrentRetryCount=2,
        ),
        {
            "ReSchedule": 1,
            "ExecutionTime": s(117),
            "PastExecutionData": {
                "InitialRequestDateTime": s(-123),
                "RetryCount": 3,
                "RetryInterval": 120,
                "RetryDateTime": s(117),
            },
        },
    ),
]


@pytest.mark.parametrize(
    ("name", "config", "past_data", "expected_data", "expected_resched"),
    RETRY_CASES,
    ids=[c[0] for c in RETRY_CASES],
)
def test_retry_cases(name, config, past_data, expected_data, expected_resched) -> None:
    decision = request_retry(config, past_data, now=NOW)
    assert decision.data == expected_data
    assert decision.reschedule_data() == expected_resched
    assert decision.reschedule == bool(expected_resched["ReSchedule"])


# (name, config, expected ErrorMessage) - RequestRetry.t error vectors
CONFIG_ERROR_CASES: list[tuple[str, Any, str]] = [
    ("No ModuleConfig", None, "Got no ModuleConfig!"),
    ("Invalid ModuleConfig", [], "Got no ModuleConfig!"),
    ("Empty ModuleConfig", {}, "Got no ModuleConfig!"),
    ("No ScheduleRetry", {"1": None}, "Config param 'ScheduleRetry' is not a non-empty string!"),
    (
        "Empty ScheduleRetry",
        {"ScheduleRetry": None},
        "Config param 'ScheduleRetry' is not a non-empty string!",
    ),
    (
        "Invalid ScheduleRetry type",
        {"ScheduleRetry": {}},
        "Config param 'ScheduleRetry' is not a non-empty string!",
    ),
    (
        "No RetryIntervalStart",
        {"ScheduleRetry": 0},
        "Config param 'RetryIntervalStart' is not a non-empty string!",
    ),
    (
        "No RetryIntervalFactor",
        {"ScheduleRetry": 0, "RetryIntervalStart": 0},
        "Config param 'RetryIntervalFactor' is not a non-empty string!",
    ),
    (
        "RetryIntervalMax hash",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryIntervalMax": {},
        },
        "Config param 'RetryIntervalMax' is not a string!",
    ),
    (
        "RetryCountMax hash",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryCountMax": {},
        },
        "Config param 'RetryCountMax' is not a string!",
    ),
    (
        "RetryPeriodMax hash",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryPeriodMax": {},
        },
        "Config param 'RetryPeriodMax' is not a string!",
    ),
    (
        "ScheduleRetry a",
        {"ScheduleRetry": "a", "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
        "Config param 'ScheduleRetry' is not '0' or '1'!",
    ),
    (
        "Factor 0",
        {"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 0},
        "Config param 'RetryIntervalFactor' is not '1', '1.5' or '2'!",
    ),
    (
        "Start -1",
        {"ScheduleRetry": 0, "RetryIntervalStart": -1, "RetryIntervalFactor": 1},
        "Config param 'RetryIntervalStart' is not '0' or a positive integer!",
    ),
    (
        "IntervalMax a",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryIntervalMax": "a",
        },
        "Config param 'RetryIntervalMax' is not empty, '0' or a positive integer!",
    ),
    (
        "CountMax 0",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryCountMax": 0,
        },
        "Config param 'RetryCountMax' is not empty or a positive integer!",
    ),
    (
        "PeriodMax 1a",
        {
            **{"ScheduleRetry": 0, "RetryIntervalStart": 0, "RetryIntervalFactor": 1},
            "RetryPeriodMax": "1a",
        },
        "Config param 'RetryPeriodMax' is not empty or a positive integer!",
    ),
]


@pytest.mark.parametrize(
    ("name", "config", "message"), CONFIG_ERROR_CASES, ids=[c[0] for c in CONFIG_ERROR_CASES]
)
def test_config_error_messages(name, config, message) -> None:
    with pytest.raises(ErrorHandlingConfigError) as exc:
        request_retry(config, None, now=NOW)
    assert str(exc.value) == message


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            {"RetryDateTime": "blahfasel", "InitialRequestDateTime": s(0)},
            "RetryDateTime is invalid!",
        ),
        ({"InitialRequestDateTime": "blahfasel"}, "InitialRequestDateTime is invalid!"),
    ],
)
def test_invalid_past_times_raise_with_znuny_message(raw, message) -> None:
    with pytest.raises(ErrorHandlingConfigError) as exc:
        PastExecutionData.from_json(raw)
    assert str(exc.value) == message


def test_first_failure_schedules_start_interval() -> None:
    d = request_retry(cfg(), None, now=NOW)
    assert d.reschedule and d.execution_time == NOW + timedelta(seconds=60)
    assert d.past == PastExecutionData(NOW, 1, 60, NOW + timedelta(seconds=60))


def test_factor_and_cap() -> None:
    d = request_retry(cfg(RetryIntervalMax="300"), PastExecutionData(NOW, 3, 240, NOW), now=NOW)
    assert d.past is not None
    assert d.past.retry_interval == 300  # int(240*2)=480 capped


def test_factor_one_point_five_truncates() -> None:
    d = request_retry(cfg(RetryIntervalFactor="1.5"), PastExecutionData(NOW, 1, 75, NOW), now=NOW)
    assert d.past is not None
    assert d.past.retry_interval == 112  # int(112.5)


def test_period_clamps_to_initial_plus_period() -> None:
    p = PastExecutionData(NOW, 2, 120, NOW + timedelta(seconds=500))
    d = request_retry(cfg(RetryPeriodMax="600"), p, now=NOW + timedelta(seconds=500))
    assert d.execution_time == NOW + timedelta(seconds=600)


def test_count_and_period_reached() -> None:
    by_count = request_retry(cfg(RetryCountMax="3"), PastExecutionData(NOW, 3, 60, NOW), now=NOW)
    assert not by_count.reschedule and by_count.data["MaximumRetryCountReached"] == 1
    by_period = request_retry(
        cfg(RetryPeriodMax="100"), PastExecutionData(NOW, 1, 60, at(100)), now=at(100)
    )
    assert not by_period.reschedule and by_period.data["MaximumRetryPeriodReached"] == 1
    assert by_period.data["MaximumRetryCountReached"] == 0


def test_target_in_past_becomes_now_plus_one() -> None:
    p = PastExecutionData(at(-1000), 1, 10, at(-900))
    d = request_retry(cfg(), p, now=NOW)
    assert d.execution_time == at(1)


def test_interval_zero_start_also_waits_one_second() -> None:
    d = request_retry(cfg(RetryIntervalStart="0"), None, now=NOW)
    assert d.execution_time == at(1)


@pytest.mark.parametrize(
    "bad",
    [
        {"ScheduleRetry": "yes"},
        {"RetryIntervalFactor": "3"},
        {"RetryIntervalStart": "-1"},
        {"RetryCountMax": "0"},
        {"RetryPeriodMax": "x"},
    ],
)
def test_config_errors(bad: dict[str, str]) -> None:
    with pytest.raises(ErrorHandlingConfigError):
        request_retry(cfg(**bad), None, now=NOW)


def test_past_execution_json_round_trip() -> None:
    original = PastExecutionData(NOW, 2, 120, at(120))
    raw = original.to_json()
    assert raw == {
        "InitialRequestDateTime": "2026-01-15 10:00:00",
        "RetryCount": 2,
        "RetryInterval": 120,
        "RetryDateTime": "2026-01-15 10:02:00",
    }
    assert PastExecutionData.from_json(json.loads(json.dumps(raw))) == original
    assert PastExecutionData.from_json(None) is None
    assert PastExecutionData.from_json({}) is None


# ---------------------------------------------------------------- handle_error


@dataclass
class MemorySink:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)

    def summaries(self, level: str | None = None) -> list[str]:
        return [r["summary"] for r in self.rows if level is None or r["debug_level"] == level]


def make_debugger(sink: MemorySink) -> GiDebugger:
    return GiDebugger(
        sink=sink,
        config=DebuggerConfig("debug", False),
        webservice_id=1,
        communication_type="Requester",
    )


def module(
    key: str = "1",
    *,
    type_: str = "RequestRetry",
    invoker_filter: tuple[str, ...] = (),
    stage_filter: tuple[str, ...] = (),
    content_filter: str | None = None,
    stop: str | None = None,
    **config: str,
) -> ErrorHandlingModule:
    full = {"Type": type_, **cfg(**config)}
    return ErrorHandlingModule(
        key=key,
        type=type_,
        invoker_filter=invoker_filter,
        error_stage_filter=stage_filter,
        error_message_content_filter=content_filter,
        stop_after_match=stop,
        config=full,
    )


async def run(modules: list[ErrorHandlingModule], sink: MemorySink | None = None, **kw: Any):
    sink = sink or MemorySink()
    args: dict[str, Any] = {
        "invoker": "CreateTicket",
        "error_stage": "RequesterRequestPerform",
        "summary": "Summary 1",
        "data": {"Error": "boom"},
        "past": None,
        **kw,
    }
    result = await handle_error(modules=modules, debugger=make_debugger(sink), now=NOW, **args)
    return result, sink


async def test_no_modules_returns_summary_without_reschedule() -> None:
    result, sink = await run([])
    assert result.error_message == "Summary 1"
    assert result.reschedule is None and result.module_data == []
    assert sink.summaries("error") == ["Summary 1"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"error_stage": ""}, "Got no ErrorStage!"),
        ({"summary": ""}, "Got no Summary!"),
        ({"data": None}, "Got no Data!"),
    ],
)
async def test_missing_parameters(override: dict[str, Any], message: str) -> None:
    result, sink = await run([module()], **override)
    assert result.error_message == message
    assert result.reschedule is None
    assert sink.rows == []


async def test_error_stages_are_the_requester_stages() -> None:
    assert "RequesterRequestPerform" in ERROR_STAGES and len(set(ERROR_STAGES)) == 5


async def test_single_module_reschedules_and_logs() -> None:
    result, sink = await run([module("retry")])
    assert result.reschedule is not None and result.reschedule.reschedule
    assert result.reschedule.execution_time == at(60)
    assert result.module_data[0]["ModuleName"] == "retry"
    assert result.module_data[0]["ModuleData"]["ReSchedule"] == 1
    assert sink.summaries() == [
        "Summary 1",
        "Executed error handling module 'retry'",
        "Got reschedule decision from error handling 'retry'",
        "Request will be retried again at:",
    ]
    assert sink.rows[-1]["content"] == s(60)


async def test_chain_filters_and_stop_after_match() -> None:
    skipped_invoker = module("a", invoker_filter=("Other",))
    skipped_stage = module("b", stage_filter=("RequesterResponseMap",))
    skipped_content = module("c", content_filter="nothing-like-this")
    matched_content = module("d", content_filter=r'"Error": "bo+m"', stop="backend")
    second_same_type = module("e")
    result, _ = await run(
        [skipped_invoker, skipped_stage, skipped_content, matched_content, second_same_type]
    )
    assert [m["ModuleName"] for m in result.module_data] == ["d"]


async def test_stop_after_match_all_stops_loop_other_types_included() -> None:
    stopper = module("first", stop="all")
    result, _ = await run([stopper, module("second", type_="Other")])
    assert [m["ModuleName"] for m in result.module_data] == ["first"]


async def test_stop_after_match_backend_only_skips_same_type() -> None:
    result, sink = await run([module("one", stop="backend"), module("two"), module("x", type_="Y")])
    assert [m["ModuleName"] for m in result.module_data] == ["one"]
    assert any("Can't load error handling module 'Y'" in s_ for s_ in sink.summaries("notice"))


async def test_invoker_filter_needs_a_matching_invoker() -> None:
    mod = module("a", invoker_filter=("CreateTicket",))
    hit, _ = await run([mod])
    miss, _ = await run([mod], invoker="")
    assert len(hit.module_data) == 1 and miss.module_data == []


async def test_content_filter_searches_summary_and_dumped_data() -> None:
    in_summary, _ = await run([module(content_filter="Summary 1")], data={"x": 1})
    in_data, _ = await run([module(content_filter="timeout-here")], data={"msg": "timeout-here"})
    string_data, _ = await run([module(content_filter="^plain")], data="plain text")
    nowhere, _ = await run([module(content_filter="absent")])
    assert [len(r.module_data) for r in (in_summary, in_data, string_data, nowhere)] == [1, 1, 1, 0]


async def test_catastrophic_content_filter_is_a_non_match_with_a_note() -> None:
    sink = MemorySink()
    result, _ = await run(
        [module(content_filter=r"^(a|aa)+$")],
        sink,
        summary="a" * 60 + "c",
        data={"x": "a" * 60 + "c"},
    )
    assert result.module_data == []
    assert any("timed out" in x for x in sink.summaries("notice"))


async def test_broken_content_filter_is_a_non_match() -> None:
    result, sink = await run([module(content_filter="(")])
    assert result.module_data == []
    assert any("invalid ErrorMessageContentFilter" in x for x in sink.summaries("notice"))


async def test_module_config_error_is_logged_and_drops_reschedule() -> None:
    good = module("good")
    bad = module("bad", RetryIntervalFactor="3")
    result, sink = await run([good, bad])
    assert result.error_message == "Config param 'RetryIntervalFactor' is not '1', '1.5' or '2'!"
    assert result.reschedule is None and result.module_data == []
    assert sink.summaries("error")[-1] == result.error_message


async def test_last_module_decision_wins_like_znuny() -> None:
    first = module("first")
    second = module("second", ScheduleRetry="0")
    result, _ = await run([first, second])
    assert result.reschedule is not None and not result.reschedule.reschedule
    assert [m["ModuleName"] for m in result.module_data] == ["first", "second"]


async def test_past_execution_data_is_used() -> None:
    result, _ = await run([module("r")], past=PastExecutionData(NOW, 1, 60, NOW))
    assert result.reschedule is not None and result.reschedule.past is not None
    assert result.reschedule.past.retry_count == 2
    assert result.reschedule.past.retry_interval == 120
