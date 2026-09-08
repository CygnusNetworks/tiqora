"""UTC handling on the read and write boundaries.

Regression cover for the "every UI timestamp is 1-2h early" bug: Znuny stores
UTC (``OTRSTimeZone`` = UTC) but naive datetimes were serialized without an
offset, so the frontend parsed them as browser-local time.
"""

from __future__ import annotations

import inspect
import typing
from datetime import UTC, datetime

from pydantic import BaseModel
from pydantic.functional_serializers import PlainSerializer

from tiqora.api.v1.admin import schemas as admin_schemas
from tiqora.db.engine import _utc_connect_args
from tiqora.domain.schemas import ArticleListItem, HistoryEntry, UtcDateTime


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


def test_admin_user_out_round_trips_utc() -> None:
    user = admin_schemas.UserOut(
        id=29,
        login="mansfeld",
        title=None,
        first_name="Timo",
        last_name="Mansfeld",
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
