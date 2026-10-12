"""Generic Interface error handling (G5).

Ports of ``Kernel/GenericInterface/ErrorHandling.pm`` (``HandleError``, requester side) and
``ErrorHandling/RequestRetry.pm`` of Znuny 6.5. Pure: no DB, no network; the only side
effect is writing through the :class:`~tiqora.gi.debugger.GiDebugger`.

Deliberate differences from Znuny:

* Only the ``RequestRetry`` module type exists (Znuny's ``GenericInterface::ErrorHandling::Module``
  registration is SysConfig-driven; no other module ships with Znuny). Other types are logged
  and skipped, as for an unregistered module.
* A time budget (``REGEX_TIMEOUT_SECONDS``) guards ``ErrorMessageContentFilter``; a timeout or a
  broken pattern counts as "no match" and is noted in the debugger.
* Times are naive UTC with second resolution (Znuny ``DateTime`` strings).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from tiqora.api.compat.mapping.perl import perl_str
from tiqora.gi.config import ErrorHandlingModule
from tiqora.gi.debugger import GiDebugger
from tiqora.util.perl_regex import REGEX_TIMEOUT_SECONDS, PerlRegexError, compile_perl_regex

__all__ = [
    "ERROR_STAGES",
    "ErrorHandlingConfigError",
    "HandleErrorResult",
    "PastExecutionData",
    "RetryDecision",
    "handle_error",
    "request_retry",
]

ERROR_STAGES: Final = (
    "RequesterRequestPrepare",
    "RequesterRequestMap",
    "RequesterRequestPerform",
    "RequesterResponseMap",
    "RequesterResponseProcess",
)

SUPPORTED_MODULE_TYPES: Final = frozenset({"RequestRetry"})

_TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_POSITIVE_INT = re.compile(r"[1-9][0-9]*\Z")


class ErrorHandlingConfigError(Exception):
    """Invalid module config or past execution data; the message is Znuny's verbatim."""


def _fmt(moment: datetime) -> str:
    return moment.strftime(_TIME_FORMAT)


def _parse_time(raw: Any, message: str) -> datetime:
    text = perl_str(raw).strip()
    try:
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d")
        parsed = datetime.strptime(text, _TIME_FORMAT)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ErrorHandlingConfigError(message) from None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class PastExecutionData:
    initial_request_at: datetime
    retry_count: int
    retry_interval: int | None
    retry_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "InitialRequestDateTime": _fmt(self.initial_request_at),
            "RetryCount": self.retry_count,
        }
        if self.retry_interval is not None:
            out["RetryInterval"] = self.retry_interval
        if self.retry_at is not None:
            out["RetryDateTime"] = _fmt(self.retry_at)
        return out

    @classmethod
    def from_json(cls, raw: Any) -> PastExecutionData | None:
        """Parse stored data; ``None`` when there is none. Unparseable times raise
        :class:`ErrorHandlingConfigError` with Znuny's message."""
        if not isinstance(raw, Mapping) or not raw:
            return None
        initial_raw = raw.get("InitialRequestDateTime")
        if initial_raw is None or perl_str(initial_raw) == "":
            return None
        initial = _parse_time(initial_raw, "InitialRequestDateTime is invalid!")
        retry_raw = raw.get("RetryDateTime")
        retry_at = (
            _parse_time(retry_raw, "RetryDateTime is invalid!")
            if retry_raw is not None and perl_str(retry_raw) != ""
            else None
        )
        interval_raw = raw.get("RetryInterval")
        try:
            interval = (
                int(float(perl_str(interval_raw)))
                if interval_raw is not None and perl_str(interval_raw) != ""
                else None
            )
            count = int(float(perl_str(raw.get("RetryCount") or 0)))
        except ValueError:
            raise ErrorHandlingConfigError("Past execution data is invalid!") from None
        return cls(initial, count, interval, retry_at)


@dataclass(frozen=True, slots=True)
class RetryDecision:
    reschedule: bool
    execution_time: datetime | None
    past: PastExecutionData | None
    data: dict[str, Any]

    def reschedule_data(self) -> dict[str, Any]:
        """Znuny's ``ReScheduleData`` structure."""
        if not self.reschedule or self.execution_time is None or self.past is None:
            return {"ReSchedule": 0}
        return {
            "ReSchedule": 1,
            "ExecutionTime": _fmt(self.execution_time),
            "PastExecutionData": self.past.to_json(),
        }


def _is_container(value: Any) -> bool:
    return isinstance(value, (Mapping, list, tuple))


def _string_with_data(value: Any) -> bool:
    return value is not None and not _is_container(value) and perl_str(value) != ""


def _check_module_config(config: Mapping[str, Any]) -> dict[str, str]:
    """``_ModuleConfigCheck``; returns the normalised string values."""
    for name in ("ScheduleRetry", "RetryIntervalStart", "RetryIntervalFactor"):
        if not _string_with_data(config.get(name)):
            raise ErrorHandlingConfigError(f"Config param '{name}' is not a non-empty string!")
    values: dict[str, str] = {
        name: perl_str(config[name])
        for name in ("ScheduleRetry", "RetryIntervalStart", "RetryIntervalFactor")
    }
    for name in ("RetryIntervalMax", "RetryCountMax", "RetryPeriodMax"):
        value = config.get(name)
        if _is_container(value):
            raise ErrorHandlingConfigError(f"Config param '{name}' is not a string!")
        values[name] = perl_str(value)
    if values["ScheduleRetry"] not in ("0", "1"):
        raise ErrorHandlingConfigError("Config param 'ScheduleRetry' is not '0' or '1'!")
    if values["RetryIntervalFactor"] not in ("1", "1.5", "2"):
        raise ErrorHandlingConfigError(
            "Config param 'RetryIntervalFactor' is not '1', '1.5' or '2'!"
        )
    messages = {
        "RetryIntervalStart": "Config param 'RetryIntervalStart' is not '0' or a positive integer!",
        "RetryIntervalMax": (
            "Config param 'RetryIntervalMax' is not empty, '0' or a positive integer!"
        ),
        "RetryCountMax": "Config param 'RetryCountMax' is not empty or a positive integer!",
        "RetryPeriodMax": "Config param 'RetryPeriodMax' is not empty or a positive integer!",
    }
    for name in ("RetryIntervalStart", "RetryIntervalMax", "RetryCountMax", "RetryPeriodMax"):
        value = values[name]
        if value == "" or _POSITIVE_INT.match(value):
            continue
        if name in ("RetryIntervalStart", "RetryIntervalMax") and value == "0":
            continue
        raise ErrorHandlingConfigError(messages[name])
    return values


def request_retry(
    module_config: Mapping[str, Any], past: PastExecutionData | None, *, now: datetime
) -> RetryDecision:
    """``ErrorHandling::RequestRetry::Run``. Raises :class:`ErrorHandlingConfigError`."""
    if not isinstance(module_config, Mapping) or not module_config:
        raise ErrorHandlingConfigError("Got no ModuleConfig!")
    cfg = _check_module_config(module_config)

    now = now.replace(microsecond=0)
    retry_count = past.retry_count if past is not None else 0
    current = past.retry_at if past is not None and past.retry_at is not None else now
    initial = past.initial_request_at if past is not None else now

    data: dict[str, Any] = {
        "ReSchedule": 0,
        "InitialRequestDateTime": _fmt(initial),
        "CurrentRequestDateTime": _fmt(current),
        "CurrentRetryCount": retry_count,
        "MaximumRetryCountReached": 0,
        "MaximumRetryPeriodReached": 0,
    }
    no_retry = RetryDecision(False, None, None, data)

    if cfg["ScheduleRetry"] == "0":
        return no_retry

    count_max = int(cfg["RetryCountMax"]) if cfg["RetryCountMax"] else 0
    if count_max and retry_count >= count_max:
        data["MaximumRetryCountReached"] = 1
        return no_retry

    period_max = int(cfg["RetryPeriodMax"]) if cfg["RetryPeriodMax"] else 0
    delta = int(abs((current - initial).total_seconds()))
    if period_max and delta >= period_max:
        data["MaximumRetryPeriodReached"] = 1
        return no_retry

    if past is not None and past.retry_interval is not None:
        interval = int(past.retry_interval * float(cfg["RetryIntervalFactor"]))
        if cfg["RetryIntervalMax"] != "" and interval > int(cfg["RetryIntervalMax"]):
            interval = int(cfg["RetryIntervalMax"])
    else:
        interval = int(cfg["RetryIntervalStart"])

    target: datetime | None = None
    if period_max and delta + interval >= period_max:
        target = initial + timedelta(seconds=period_max)
    if target is None:
        target = current + timedelta(seconds=interval)

    # Minimum wait of one second after the request (DoS guard), also after a stalled daemon.
    if target <= now:
        target = now + timedelta(seconds=1)

    data["ReSchedule"] = 1
    new_past = PastExecutionData(initial, retry_count + 1, interval, target)
    return RetryDecision(True, target, new_past, data)


@dataclass(frozen=True, slots=True)
class HandleErrorResult:
    error_message: str
    reschedule: RetryDecision | None
    module_data: list[dict[str, Any]]


def _missing(value: Any) -> bool:
    """Perl ``!$value`` for a scalar or reference."""
    if value is None:
        return True
    if _is_container(value):
        return False
    return perl_str(value) in ("", "0")


def _dump(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


async def _filter_matches(
    pattern_text: str, texts: Sequence[str], debugger: GiDebugger, key: str
) -> bool:
    try:
        pattern = compile_perl_regex(pattern_text, verbose=False)
    except PerlRegexError:
        await debugger.notice(f"Error handling module '{key}': invalid ErrorMessageContentFilter")
        return False
    for text in texts:
        try:
            if pattern.search(text, timeout=REGEX_TIMEOUT_SECONDS) is not None:
                return True
        except TimeoutError:
            await debugger.notice(
                f"Error handling module '{key}': ErrorMessageContentFilter timed out"
            )
            return False
    return False


async def handle_error(
    *,
    modules: Sequence[ErrorHandlingModule],
    invoker: str,
    error_stage: str,
    summary: str,
    data: Any,
    past: PastExecutionData | None,
    debugger: GiDebugger,
    now: datetime,
) -> HandleErrorResult:
    """``ErrorHandling::HandleError`` for the requester."""
    if _missing(error_stage):
        return HandleErrorResult("Got no ErrorStage!", None, [])
    if _missing(summary):
        return HandleErrorResult("Got no Summary!", None, [])
    if _missing(data):
        return HandleErrorResult("Got no Data!", None, [])

    await debugger.error(summary, data)

    if not modules:
        return HandleErrorResult(summary, None, [])

    module_data: list[dict[str, Any]] = []
    decision: RetryDecision | None = None
    stopped_types: set[str] = set()
    filter_texts: list[str] | None = None

    for module in modules:
        if module.type in stopped_types:
            continue
        if module.type not in SUPPORTED_MODULE_TYPES:
            await debugger.notice(
                f"Can't load error handling module '{module.type}' for entry '{module.key}'"
            )
            continue
        if module.invoker_filter and (not invoker or invoker not in module.invoker_filter):
            continue
        if module.error_stage_filter and error_stage not in module.error_stage_filter:
            continue
        if module.error_message_content_filter:
            if filter_texts is None:
                filter_texts = [summary, data if isinstance(data, str) else _dump(data)]
            if not await _filter_matches(
                module.error_message_content_filter, filter_texts, debugger, module.key
            ):
                continue

        try:
            result = request_retry(module.config, past, now=now)
        except ErrorHandlingConfigError as exc:
            message = str(exc)
            await debugger.error(message)
            return HandleErrorResult(message, None, [])

        entry = {
            "ModuleConfig": module.config,
            "ModuleData": result.data,
            "ModuleName": module.key,
        }
        module_data.append(entry)
        await debugger.debug(f"Executed error handling module '{module.key}'", entry)

        decision = result
        await debugger.info(
            f"Got reschedule decision from error handling '{module.key}'",
            result.reschedule_data(),
        )

        if module.stop_after_match == "all":
            break
        if module.stop_after_match == "backend":
            stopped_types.add(module.type)

    if decision is not None and decision.reschedule and decision.execution_time is not None:
        await debugger.notice("Request will be retried again at:", _fmt(decision.execution_time))

    return HandleErrorResult(summary, decision, module_data)
