"""GiDebugger against an in-memory sink (pure)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from tiqora.gi.config import DebuggerConfig
from tiqora.gi.debugger import GiDebugger


@dataclass
class Row:
    communication_id: str
    communication_type: str
    webservice_id: int
    remote_ip: str | None
    name: str | None
    ticket_id: int | None
    debug_level: str
    summary: str
    content: str
    replay: Any


@dataclass
class MemorySink:
    rows: list[Row] = field(default_factory=list)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append(Row(**kwargs))


def _debugger(sink: MemorySink, threshold: str = "debug", test_mode: bool = False, **kw: Any):
    return GiDebugger(
        sink=sink,
        config=DebuggerConfig(threshold, test_mode),  # type: ignore[arg-type]
        webservice_id=1,
        communication_type="Requester",
        **kw,
    )


async def test_threshold_and_test_mode() -> None:
    sink = MemorySink()
    d = GiDebugger(
        sink=sink,
        config=DebuggerConfig("notice", False),
        webservice_id=1,
        communication_type="Requester",
    )
    await d.debug("a")
    await d.info("b")
    await d.notice("c", {"x": 1})
    await d.error("d")
    assert [r.summary for r in sink.rows] == ["c", "d"]
    t = GiDebugger(
        sink=sink,
        config=DebuggerConfig("debug", True),
        webservice_id=1,
        communication_type="Requester",
    )
    await t.error("nothing written")
    assert len(sink.rows) == 2


async def test_content_json_sorted_redacted_truncated() -> None:
    sink = MemorySink()
    d = _debugger(sink, secret_values=["tok-123456"], max_content_bytes=2048)
    assert re.fullmatch(r"[0-9a-f]{32}", d.communication_id)
    await d.debug("sorted", {"b": 1, "a": {"Password": "pw-1", "note": "uses tok-123456"}})
    assert sink.rows[0].content == json.dumps(
        {"a": {"Password": "***", "note": "uses ***"}, "b": 1}, sort_keys=True, indent=2
    )
    await d.debug("text", "header tok-123456 end")
    assert sink.rows[1].content == "header *** end"
    await d.debug("none")
    assert sink.rows[2].content == "No data provided"
    await d.debug("x" * 400, "ü" * 5000)  # 10000 bytes > 2048
    long_row = sink.rows[3]
    assert len(long_row.summary) == 255
    assert "gekürzt" in long_row.content and "10000" in long_row.content
    assert len(long_row.content.encode()) < 2048 + 80
    assert long_row.content.startswith("ü" * 100)


async def test_default_content_limit_is_200_kib() -> None:
    sink = MemorySink()
    d = _debugger(sink)
    await d.debug("big", "a" * (300 * 1024))
    content = sink.rows[0].content
    assert "gekürzt" in content
    assert 200 * 1024 <= len(content.encode()) < 200 * 1024 + 80


async def test_error_returns_znuny_shape_and_masks_secrets() -> None:
    sink = MemorySink()
    d = _debugger(sink, secret_values=["pw-secret"])
    assert await d.error("boom") == {"Success": 0, "ErrorMessage": "boom"}
    out = await d.error("login with pw-secret failed")
    assert out == {"Success": 0, "ErrorMessage": "login with *** failed"}
    assert sink.rows[1].summary == "login with *** failed"


async def test_ticket_id_and_replay_travel_with_the_rows() -> None:
    sink = MemorySink()
    replay = {"invoker": "SendTicket", "data": {"TicketID": 1}, "asynchronous": False}
    d = _debugger(
        sink, threshold="error", name="SendTicket", remote_ip="198.51.100.7", replay=replay
    )
    await d.info("below threshold")
    assert sink.rows == []
    d.ticket_id = 42
    await d.error("transport failed")  # threshold=error: the failing call is what gets recorded
    row = sink.rows[0]
    assert row.replay == replay and row.ticket_id == 42
    assert row.name == "SendTicket" and row.remote_ip == "198.51.100.7"
    assert row.communication_type == "Requester" and row.webservice_id == 1


async def test_add_secrets_masks_later_lines_only() -> None:
    sink = MemorySink()
    d = _debugger(sink)
    await d.debug("before gi51-token-late", "gi51-token-late")
    d.add_secrets("gi51-token-late", 7)  # type: ignore[arg-type]  # non-strings are ignored
    await d.error("after gi51-token-late", {"k": "x gi51-token-late"})
    assert sink.rows[0].summary == "before gi51-token-late"
    assert "gi51-token-late" not in sink.rows[1].summary + sink.rows[1].content


async def test_secret_with_quote_or_backslash_is_masked_in_dumps() -> None:
    """Final review m4: json.dumps escapes ``"`` and ``\\``; the masking must still hit."""
    secret = 'pa"ss\\word-ö'
    sink = MemorySink()
    debugger = _debugger(sink, secret_values=[secret])
    await debugger.debug("dict", {"Note": f"value {secret} end", "List": [secret]})
    await debugger.debug("raw json echo", json.dumps({"echo": secret}))
    await debugger.debug("raw ascii json echo", json.dumps({"echo": secret}, ensure_ascii=True))
    for row in sink.rows:
        assert "pa" not in row.content and "word" not in row.content, row.content
        assert "***" in row.content
