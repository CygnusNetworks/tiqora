"""CTI call popup (spec A2): call state in Redis, the PBX webhook
``POST /channels/phone/events``, ``GET /phone/calls/active`` / dismiss, and the
``call_event`` SSE filter.

No Docker: an in-memory fake Redis stands in (same approach as
``test_events_sse.py``); the extension→agent lookup is patched out here and
covered against a real ``user_preferences`` table in
``test_phone_extension_db.py``.
"""

from __future__ import annotations

import fnmatch
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiqora.api.deps import get_current_user, get_db, get_redis
from tiqora.channels.phone import cti
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL

T0 = datetime(2026, 9, 29, 10, 0, 0, tzinfo=UTC)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.published: list[tuple[str, str]] = []
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.store[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        self._check()
        return self.store.get(key)

    async def scan_iter(self, match: str | None = None) -> Any:
        self._check()
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(m) for c, m in self.published if c == TIQORA_EVENTS_CHANNEL]


def _resolver(mapping: dict[str, list[int]]) -> Any:
    async def _resolve(extension: str) -> list[int]:
        return mapping.get(extension, [])

    return _resolve


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


def test_parse_extensions_splits_and_dedupes() -> None:
    assert cti.parse_extensions(" 100, 101 ,,100;PJSIP/200 ") == ["100", "101", "PJSIP/200"]
    assert cti.parse_extensions(None) == []
    assert cti.parse_extensions("") == []


def test_normalize_extensions_validates() -> None:
    assert cti.normalize_extensions("100, 101") == "100,101"
    assert cti.normalize_extensions("  ") is None
    assert cti.normalize_extensions(None) is None
    with pytest.raises(ValueError):
        cti.normalize_extensions("100, bad value!")
    with pytest.raises(ValueError):
        cti.normalize_extensions("1" * 65)


def test_extension_matches_case_insensitive() -> None:
    assert cti.extension_matches("100,PJSIP/Anna", "pjsip/anna")
    assert not cti.extension_matches("100,101", "10")
    assert not cti.extension_matches(None, "100")


# ---------------------------------------------------------------------------
# Call state machine
# ---------------------------------------------------------------------------


async def _event(
    redis: _FakeRedis,
    event: str,
    *,
    ext: str | None = "100",
    at: datetime = T0,
    number: str | None = "+49 228 5550101",
    mapping: dict[str, list[int]] | None = None,
    call_id: str = "c1",
) -> cti.CallEventResult:
    return await cti.apply_call_event(
        cast(Any, redis),
        cti.CallEventIn(
            event=cast(Any, event),
            call_id=call_id,
            caller_number=number,
            extension=ext,
            timestamp=at,
        ),
        resolve_users=_resolver(mapping if mapping is not None else {"100": [7], "101": [8]}),
    )


async def test_ringing_creates_state_with_ttl_and_publishes_to_target_users() -> None:
    redis = _FakeRedis()
    result = await _event(redis, "ringing")

    assert result.recipients == [7]
    key = "tiqora:call:c1"
    assert redis.ttls[key] == cti.CALL_TTL_SECONDS
    stored = json.loads(redis.store[key])
    assert stored["state"] == "ringing"
    assert stored["number"] == "+49 228 5550101"
    assert stored["user_ids"] == [7]

    [msg] = redis.events()
    assert msg["type"] == "call_event"
    assert msg["user_ids"] == [7]
    assert msg["event"] == "ringing"
    assert msg["call"]["call_id"] == "c1"
    assert msg["call"]["state"] == "ringing"


async def test_unknown_extension_without_state_is_ignored() -> None:
    redis = _FakeRedis()
    result = await _event(redis, "ringing", ext="999")
    assert result.recipients == []
    assert redis.store == {}
    assert redis.published == []


async def test_ring_group_unions_then_answer_narrows_and_tells_the_others() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing", ext="100")
    await _event(redis, "ringing", ext="101")
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert sorted(stored["user_ids"]) == [7, 8]

    answered = await _event(redis, "answered", ext="101", at=T0 + timedelta(seconds=5))
    # Agent 7 still gets the event so their card can go away.
    assert sorted(answered.recipients) == [7, 8]
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["state"] == "answered"
    assert stored["user_ids"] == [8]
    assert stored["answered_at"].startswith("2026-09-29T10:00:05")


async def test_answered_by_one_agent_is_recorded_for_the_owner_prefill() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing", ext="100")
    await _event(redis, "ringing", ext="101")
    ringing = json.loads(redis.store["tiqora:call:c1"])
    assert ringing["answered_by_user_id"] is None

    await _event(redis, "answered", ext="101", at=T0 + timedelta(seconds=5))
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["answered_by_user_id"] == 8
    assert redis.events()[-1]["call"]["answered_by_user_id"] == 8
    # Survives hangup and is what list_active_calls hands the popup.
    await _event(redis, "hangup", ext=None, number=None, at=T0 + timedelta(seconds=60))
    [call] = await cti.list_active_calls(cast(Any, redis), 8, now=T0 + timedelta(seconds=61))
    assert call.answered_by_user_id == 8


async def test_answered_on_a_shared_extension_names_nobody() -> None:
    redis = _FakeRedis()
    mapping = {"100": [7], "200": [7, 9]}
    await _event(redis, "ringing", ext="100", mapping=mapping)
    await _event(redis, "answered", ext="200", mapping=mapping)
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["user_ids"] == [7, 9]
    assert stored["answered_by_user_id"] is None


async def test_answered_without_extension_names_nobody() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "answered", ext=None)
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["state"] == "answered"
    assert stored["answered_by_user_id"] is None


async def test_call_state_stored_before_answered_by_existed_still_loads() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    stored = json.loads(redis.store["tiqora:call:c1"])
    del stored["answered_by_user_id"]
    redis.store["tiqora:call:c1"] = json.dumps(stored)
    await _event(redis, "answered")
    assert json.loads(redis.store["tiqora:call:c1"])["answered_by_user_id"] == 7


async def test_hangup_ends_call_and_keeps_number_and_users() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "answered", at=T0 + timedelta(seconds=3))
    result = await _event(redis, "hangup", ext=None, number=None, at=T0 + timedelta(seconds=95))

    assert result.recipients == [7]
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["state"] == "ended"
    assert stored["number"] == "+49 228 5550101"
    assert stored["ended_at"].startswith("2026-09-29T10:01:35")
    # A late answered after hangup does not resurrect the call.
    await _event(redis, "answered", at=T0 + timedelta(seconds=100))
    assert json.loads(redis.store["tiqora:call:c1"])["state"] == "ended"


async def test_click_to_dial_call_follows_the_dialplan_events() -> None:
    redis = _FakeRedis()
    registered = await cti.register_dialed_call(
        cast(Any, redis), call_id="tiqora-1", user_id=7, extension="100", number="01717630944"
    )
    assert registered.click_to_dial is True
    assert registered.state == "ringing"
    assert registered.ringing_at is None

    # Agent picked up the desk phone, the PBX dials out.
    await _event(redis, "ringing", call_id="tiqora-1", number="01717630944")
    # The trunk leg answers: its extension maps to nobody.
    await _event(redis, "answered", call_id="tiqora-1", ext="", at=T0 + timedelta(seconds=8))
    result = await _event(
        redis, "hangup", call_id="tiqora-1", ext=None, at=T0 + timedelta(seconds=68)
    )

    assert result.recipients == [7]
    stored = json.loads(redis.store["tiqora:call:tiqora-1"])
    assert stored["click_to_dial"] is True
    assert stored["direction"] == "outbound"
    assert stored["user_ids"] == [7]
    assert stored["ringing_at"].startswith("2026-09-29T10:00:00")
    assert stored["answered_at"].startswith("2026-09-29T10:00:08")
    assert stored["state"] == "ended"
    assert [e["event"] for e in redis.events()] == ["ringing", "ringing", "answered", "hangup"]


async def test_click_to_dial_hung_up_before_answer_has_no_answered_at() -> None:
    redis = _FakeRedis()
    await cti.register_dialed_call(
        cast(Any, redis), call_id="tiqora-2", user_id=7, extension="100", number="01717630944"
    )
    await _event(redis, "ringing", call_id="tiqora-2")
    await _event(redis, "hangup", call_id="tiqora-2", ext=None, at=T0 + timedelta(seconds=20))
    stored = json.loads(redis.store["tiqora:call:tiqora-2"])
    assert stored["state"] == "ended"
    assert stored["answered_at"] is None


async def test_hangup_for_unknown_call_and_extension_is_ignored() -> None:
    redis = _FakeRedis()
    result = await _event(redis, "hangup", ext=None)
    assert result.recipients == []
    assert redis.store == {}


async def test_naive_timestamp_is_utc() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing", at=datetime(2026, 9, 29, 10, 0, 0))
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["ringing_at"] == "2026-09-29T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Active calls + dismiss
# ---------------------------------------------------------------------------


async def test_active_calls_filters_user_recent_and_dismissed() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing", call_id="live")
    await _event(redis, "ringing", call_id="old")
    await _event(redis, "hangup", call_id="old", at=T0 + timedelta(seconds=10))
    await _event(redis, "ringing", call_id="other", ext="101")
    await _event(redis, "ringing", call_id="gone")
    assert await cti.dismiss_call(cast(Any, redis), "gone", 7) is True

    now = T0 + timedelta(minutes=10)
    calls = await cti.list_active_calls(cast(Any, redis), 7, now=now)
    assert sorted(c.call_id for c in calls) == ["live", "old"]

    later = T0 + timedelta(minutes=16)
    calls = await cti.list_active_calls(cast(Any, redis), 7, now=later)
    assert [c.call_id for c in calls] == ["live"]


async def test_dismiss_unknown_or_foreign_call() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    assert await cti.dismiss_call(cast(Any, redis), "nope", 7) is False
    assert await cti.dismiss_call(cast(Any, redis), "c1", 99) is False
    redis.published.clear()
    assert await cti.dismiss_call(cast(Any, redis), "c1", 7) is True
    [msg] = redis.events()
    assert msg["event"] == "dismissed"
    assert msg["user_ids"] == [7]


async def test_dismissed_agent_is_not_renotified() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "ringing", ext="101")
    assert await cti.dismiss_call(cast(Any, redis), "c1", 7) is True
    result = await _event(redis, "hangup", ext=None, at=T0 + timedelta(seconds=30))
    assert result.recipients == [8]


async def test_handled_dismisses_card_for_all_notified_agents() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "ringing", ext="101")
    redis.published.clear()
    result = await _event(redis, "handled", ext=None, number=None, at=T0 + timedelta(seconds=25))

    assert result.recipients == [7, 8]
    [msg] = redis.events()
    assert msg["event"] == "dismissed"
    assert msg["user_ids"] == [7, 8]
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["state"] == "ended"
    assert stored["ended_at"].startswith("2026-09-29T10:00:25")
    assert stored["dismissed_user_ids"] == [7, 8]
    for uid in (7, 8):
        assert await cti.list_active_calls(cast(Any, redis), uid, now=T0) == []


async def test_handled_after_answered_is_ignored() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "answered", at=T0 + timedelta(seconds=3))
    redis.published.clear()
    result = await _event(redis, "handled", ext=None, at=T0 + timedelta(seconds=9))

    assert result.recipients == []
    assert redis.published == []
    assert json.loads(redis.store["tiqora:call:c1"])["state"] == "answered"
    assert [c.call_id for c in await cti.list_active_calls(cast(Any, redis), 7, now=T0)] == ["c1"]


async def test_handled_for_unknown_call_is_ignored() -> None:
    redis = _FakeRedis()
    result = await _event(redis, "handled", ext=None)
    assert result.recipients == []
    assert redis.store == {}


async def test_handled_twice_notifies_nobody_the_second_time() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    assert (await _event(redis, "handled", ext=None)).recipients == [7]
    assert (await _event(redis, "handled", ext=None)).recipients == []


async def test_late_hangup_after_handled_does_not_reopen() -> None:
    redis = _FakeRedis()
    await _event(redis, "ringing")
    await _event(redis, "handled", ext=None, at=T0 + timedelta(seconds=25))
    redis.published.clear()
    late = await _event(redis, "hangup", ext=None, at=T0 + timedelta(seconds=60))
    ring = await _event(redis, "ringing", at=T0 + timedelta(seconds=61))

    assert late.recipients == [] and ring.recipients == []
    assert redis.events() == []
    stored = json.loads(redis.store["tiqora:call:c1"])
    assert stored["state"] == "ended"
    assert stored["ended_at"].startswith("2026-09-29T10:00:25")
    assert stored["dismissed_user_ids"] == [7]
    assert await cti.list_active_calls(cast(Any, redis), 7, now=T0) == []


# ---------------------------------------------------------------------------
# SSE filter
# ---------------------------------------------------------------------------


def test_should_forward_call_event_only_to_target_users() -> None:
    from tiqora.api.v1.events import _should_forward

    raw = json.dumps({"type": "call_event", "user_ids": [7, 8], "event": "ringing", "call": {}})
    assert _should_forward(raw, set(), user_id=7) is True
    assert _should_forward(raw, set(), user_id=9) is False
    assert _should_forward(raw, set()) is False
    bad = json.dumps({"type": "call_event", "user_ids": "7"})
    assert _should_forward(bad, set(), user_id=7) is False


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

_USER = AuthenticatedUser(
    id=7, login="agent7", first_name="Ada", last_name="Agent", auth_method="session"
)


def _app(
    redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    secret: str | None = "s3cret",
    mapping: dict[str, list[int]] | None = None,
) -> Any:
    from tiqora.api.app import create_app
    from tiqora.api.v1 import channels_phone

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[get_redis] = lambda: redis

    async def _fake_db() -> Any:
        yield MagicMock()

    app.dependency_overrides[get_db] = _fake_db
    monkeypatch.setattr(channels_phone, "channel_enabled", AsyncMock(return_value=enabled))
    monkeypatch.setattr(channels_phone, "channel_setting", AsyncMock(return_value=secret))
    table = mapping if mapping is not None else {"100": [7]}

    async def _users(_session: Any, extension: str) -> list[int]:
        return table.get(extension, [])

    monkeypatch.setattr(channels_phone, "users_for_extension", _users)
    return app


async def _post_event(app: Any, body: dict[str, Any], secret: str | None = "s3cret") -> Any:
    from httpx import ASGITransport, AsyncClient

    headers = {"X-Tiqora-Phone-Secret": secret} if secret else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/channels/phone/events", json=body, headers=headers)


RINGING = {"event": "ringing", "call_id": "1727600000.42", "caller_number": "+492285550101",
           "extension": "100"}  # fmt: skip


async def test_webhook_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_FakeRedis(), monkeypatch)
    assert (await _post_event(app, RINGING, secret=None)).status_code == 401
    assert (await _post_event(app, RINGING, secret="wrong")).status_code == 401


async def test_webhook_404_when_channel_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_FakeRedis(), monkeypatch, enabled=False)
    assert (await _post_event(app, RINGING)).status_code == 404


async def test_webhook_accepts_and_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    app = _app(redis, monkeypatch)
    resp = await _post_event(app, RINGING)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "delivered_to": 1}
    [msg] = redis.events()
    assert msg["user_ids"] == [7]
    assert msg["call"]["number"] == "+492285550101"


async def test_webhook_accepts_form_encoded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asterisk's CURL() posts form data — JSON quoting inside dialplan
    function arguments is not practical, so the endpoint takes both."""
    from httpx import ASGITransport, AsyncClient

    redis = _FakeRedis()
    app = _app(redis, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/channels/phone/events",
            data={**RINGING, "timestamp": "1790676000"},
            headers={"X-Tiqora-Phone-Secret": "s3cret"},
        )
        assert resp.status_code == 202, resp.text
        bad = await client.post(
            "/api/v1/channels/phone/events",
            data={**RINGING, "event": "nope"},
            headers={"X-Tiqora-Phone-Secret": "s3cret"},
        )
        assert bad.status_code == 422
    [msg] = redis.events()
    assert msg["call"]["ringing_at"] == "2026-09-29T10:00:00+00:00"


async def test_webhook_accepts_form_encoded_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    redis = _FakeRedis()
    app = _app(redis, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Tiqora-Phone-Secret": "s3cret"}
        await client.post("/api/v1/channels/phone/events", data=RINGING, headers=headers)
        resp = await client.post(
            "/api/v1/channels/phone/events",
            data={"event": "handled", "call_id": RINGING["call_id"]},
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        assert resp.json() == {"accepted": True, "delivered_to": 1}
        bad = await client.post(
            "/api/v1/channels/phone/events",
            data={"event": "nope", "call_id": RINGING["call_id"]},
            headers=headers,
        )
        assert bad.status_code == 422
    assert redis.events()[-1]["event"] == "dismissed"


async def test_webhook_unknown_extension_is_202(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    app = _app(redis, monkeypatch)
    resp = await _post_event(app, {**RINGING, "extension": "555"})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "delivered_to": 0}
    assert redis.published == []


async def test_webhook_validates_event(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_FakeRedis(), monkeypatch)
    assert (await _post_event(app, {**RINGING, "event": "bogus"})).status_code == 422
    assert (await _post_event(app, {**RINGING, "call_id": ""})).status_code == 422
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/channels/phone/events",
            content=b"{not json",
            headers={"X-Tiqora-Phone-Secret": "s3cret", "Content-Type": "application/json"},
        )
        assert resp.status_code == 422


async def test_webhook_503_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    redis.fail = True
    app = _app(redis, monkeypatch)
    assert (await _post_event(app, RINGING)).status_code == 503


async def test_active_calls_and_dismiss_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    redis = _FakeRedis()
    app = _app(redis, monkeypatch)
    await _post_event(app, RINGING)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/phone/calls/active")
        assert resp.status_code == 200
        [call] = resp.json()
        assert call["call_id"] == "1727600000.42"
        assert call["state"] == "ringing"
        assert call["direction"] == "inbound"
        assert call["ringing_at"] is not None

        resp = await client.post("/api/v1/phone/calls/1727600000.42/dismiss")
        assert resp.status_code == 204
        assert (await client.get("/api/v1/phone/calls/active")).json() == []
        resp = await client.post("/api/v1/phone/calls/nope/dismiss")
        assert resp.status_code == 404


def test_phone_path_is_events_area() -> None:
    from tiqora.domain.api_key_scopes import path_to_area

    assert path_to_area("/api/v1/phone/calls/active") == "events"
