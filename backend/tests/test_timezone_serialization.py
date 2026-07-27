"""UTC handling on the read and write boundaries.

Regression cover for the "every UI timestamp is 1-2h early" bug: Znuny stores
UTC (``OTRSTimeZone`` = UTC) but naive datetimes were serialized without an
offset, so the frontend parsed them as browser-local time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

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


def test_engine_pins_session_timezone_to_utc() -> None:
    assert _utc_connect_args("mysql+aiomysql://u:p@h/db") == {
        "init_command": "SET time_zone = '+00:00'"
    }
    assert _utc_connect_args("postgresql+asyncpg://u:p@h/db") == {
        "server_settings": {"timezone": "UTC"}
    }
    assert _utc_connect_args("sqlite+aiosqlite://") == {}
