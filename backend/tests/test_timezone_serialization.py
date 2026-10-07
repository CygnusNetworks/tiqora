"""UTC handling on the read and write boundaries.

Regression cover for the "every UI timestamp is 1-2h early" bug: Znuny stores
UTC (``OTRSTimeZone`` = UTC) but naive datetimes were serialized without an
offset, so the frontend parsed them as browser-local time.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import typing
from datetime import UTC, datetime

from pydantic import BaseModel
from pydantic.functional_serializers import PlainSerializer

import tiqora.api.v1 as api_v1
from tiqora.api.v1 import ai as ticket_ai
from tiqora.api.v1.admin import schemas as admin_schemas
from tiqora.api.v1.tickets import AiOriginOut, TimeAccountingReportEntry
from tiqora.db.engine import _utc_connect_args
from tiqora.domain.schemas import ArticleListItem, HistoryEntry, UtcDateTime, as_naive_utc


class _Model(BaseModel):
    ts: UtcDateTime


def test_naive_datetime_serialized_as_utc_aware() -> None:
    # A bare DB value (naive) must round-trip to JSON WITH a UTC offset so
    # `new Date(...)` in the browser reads the correct instant.
    m = _Model(ts=datetime(2026, 7, 27, 8, 6, 18))
    assert m.model_dump(mode="json")["ts"] == "2026-07-27T08:06:18+00:00"


def test_aware_datetime_normalized_to_utc() -> None:
    # An already-aware value in another zone is converted, not double-shifted.
    m = _Model(ts=datetime(2026, 7, 27, 10, 6, 18, tzinfo=UTC))
    assert m.model_dump(mode="json")["ts"] == "2026-07-27T10:06:18+00:00"


def test_python_mode_keeps_datetime() -> None:
    # Internal callers using mode="python" still get a real datetime.
    m = _Model(ts=datetime(2026, 7, 27, 8, 6, 18))
    assert isinstance(m.model_dump(mode="python")["ts"], datetime)


def test_real_response_models_use_utc_serializer() -> None:
    art = ArticleListItem(
        id=1,
        ticket_id=1,
        sender_type_id=1,
        communication_channel_id=1,
        is_visible_for_customer=True,
        create_time=datetime(2026, 7, 27, 8, 6, 18),
        create_by=1,
    )
    assert art.model_dump(mode="json")["create_time"].endswith("+00:00")

    hist = HistoryEntry(
        id=1,
        ticket_id=1,
        name="x",
        rendered="x",
        history_type_id=1,
        owner_id=1,
        create_time=datetime(2026, 7, 27, 8, 6, 18),
        create_by=1,
    )
    assert hist.model_dump(mode="json")["create_time"].endswith("+00:00")


# Request models: these datetimes are *parsed* from client input, never
# serialized back out, so the UTC serializer is neither needed nor wanted.
_REQUEST_MODELS_WITH_DATETIME = {"ApiKeyCreate", "ApiKeyUpdate", "ErasureSelectorIn"}


def _mentions_datetime(annotation: object) -> bool:
    if annotation is datetime:
        return True
    return any(_mentions_datetime(arg) for arg in typing.get_args(annotation))


def _has_utc_serializer(annotation: object, metadata: list[object]) -> bool:
    """The serializer lands in ``field.metadata`` for a bare ``UtcDateTime`` but
    stays nested inside the annotation for ``UtcDateTime | None`` — check both."""
    if any(isinstance(m, PlainSerializer) for m in metadata):
        return True

    def walk(node: object) -> bool:
        if any(isinstance(m, PlainSerializer) for m in getattr(node, "__metadata__", ())):
            return True
        return any(walk(arg) for arg in typing.get_args(node))

    return walk(annotation)


def test_admin_response_models_serialize_utc() -> None:
    """Every datetime a response model hands out must carry a UTC offset.

    ``api/v1/admin/schemas.py`` originally used a bare ``datetime``, so the whole
    admin surface (user list, queues, roles, …) rendered every timestamp 1-2h
    early. Walking the module keeps a newly added model from regressing it.
    """
    offenders: list[str] = []
    for name, model in inspect.getmembers(admin_schemas, inspect.isclass):
        if not issubclass(model, BaseModel) or name in _REQUEST_MODELS_WITH_DATETIME:
            continue
        if model.__module__ != admin_schemas.__name__:
            continue
        for field_name, field in model.model_fields.items():
            if not _mentions_datetime(field.annotation):
                continue
            if not _has_utc_serializer(field.annotation, list(field.metadata)):
                offenders.append(f"{name}.{field_name}")

    assert offenders == [], (
        f"{len(offenders)} admin response field(s) serialize a naive datetime and will "
        f"display in the wrong timezone: {offenders}"
    )


def test_ticket_ai_response_models_serialize_utc() -> None:
    """The ticket zoom's AI panel and summary marker read these; the marker
    showed "Zusammenfassung bis hier" 2h early next to correctly localised
    articles."""
    models = [
        model
        for _, model in inspect.getmembers(ticket_ai, inspect.isclass)
        if issubclass(model, BaseModel) and model.__module__ == ticket_ai.__name__
    ]
    offenders = [
        f"{model.__name__}.{field_name}"
        for model in [*models, AiOriginOut]
        for field_name, field in model.model_fields.items()
        if _mentions_datetime(field.annotation)
        and not _has_utc_serializer(field.annotation, list(field.metadata))
    ]
    assert offenders == []


# Request models are parsed from client input, never serialized back out.
_REQUEST_MODEL_NAME = re.compile(r"(Request|Params|In|Create|Update)$")


def test_every_v1_response_model_serializes_utc() -> None:
    """Route modules declare their own response models, so the central
    ``UtcDateTime`` only helps where it is used. A bare ``datetime`` there showed
    e.g. time-accounting bookings at their UTC wall time (08:08 instead of
    10:08 CEST). Walk the whole ``tiqora.api.v1`` package so a new model
    cannot regress it."""
    offenders: list[str] = []
    for info in pkgutil.walk_packages(api_v1.__path__, f"{api_v1.__name__}."):
        module = importlib.import_module(info.name)
        for name, model in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(model, BaseModel)
                or model.__module__ != module.__name__
                or _REQUEST_MODEL_NAME.search(name)
            ):
                continue
            offenders.extend(
                f"{info.name}.{name}.{field_name}"
                for field_name, field in model.model_fields.items()
                if _mentions_datetime(field.annotation)
                and not _has_utc_serializer(field.annotation, list(field.metadata))
            )
    assert offenders == [], (
        f"{len(offenders)} response field(s) serialize a naive datetime and will "
        f"display in the wrong timezone: {offenders}"
    )


def test_time_accounting_entry_round_trips_utc() -> None:
    entry = TimeAccountingReportEntry(
        id=3,
        ticket_id=1,
        time_unit=30.0,
        create_time=datetime(2026, 10, 7, 8, 8, 35),
        create_by=5,
    )
    assert entry.model_dump(mode="json")["create_time"] == "2026-10-07T08:08:35+00:00"


def test_report_date_bounds_normalised_to_naive_utc() -> None:
    # The UI sends local midnight (CEST) as a UTC instant; the column is naive UTC.
    assert as_naive_utc(datetime(2026, 10, 6, 22, 0, tzinfo=UTC)) == datetime(2026, 10, 6, 22, 0)
    assert as_naive_utc(datetime(2026, 10, 7, 0, 0)) == datetime(2026, 10, 7, 0, 0)
    # A non-UTC offset is converted, not just stripped.
    plus_two = datetime.fromisoformat("2026-10-07T00:00:00+02:00")
    assert as_naive_utc(plus_two) == datetime(2026, 10, 6, 22, 0)


def test_ai_state_summary_time_round_trips_utc() -> None:
    state = ticket_ai.AiStateOut(
        manual_assist_available=True,
        summary_available=True,
        can_summarize=False,
        operation_mode_ready=True,
        drafts=[],
        summary_body="…",
        last_summary_upto_article_id=42,
        summary_created_at=datetime(2026, 9, 25, 9, 39, 6),
    )
    assert state.model_dump(mode="json")["summary_created_at"] == "2026-09-25T09:39:06+00:00"


def test_admin_user_out_round_trips_utc() -> None:
    user = admin_schemas.UserOut(
        id=9001,
        login="emuster",
        title=None,
        first_name="Erika",
        last_name="Muster",
        valid_id=1,
        create_time=datetime(2026, 9, 8, 15, 43, 57),
        change_time=datetime(2026, 9, 8, 15, 43, 57),
    )
    dumped = user.model_dump(mode="json")
    assert dumped["create_time"] == "2026-09-08T15:43:57+00:00"
    assert dumped["change_time"] == "2026-09-08T15:43:57+00:00"


def test_engine_pins_session_timezone_to_utc() -> None:
    assert _utc_connect_args("mysql+aiomysql://u:p@h/db") == {
        "init_command": "SET time_zone = '+00:00'"
    }
    assert _utc_connect_args("postgresql+asyncpg://u:p@h/db") == {
        "server_settings": {"timezone": "UTC"}
    }
    assert _utc_connect_args("sqlite+aiosqlite://") == {}
