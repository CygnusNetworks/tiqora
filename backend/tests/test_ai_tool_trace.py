"""Unit tests for the draft tool-trace exposure on the agent AI API.

The trace (``role == "tool"`` wire messages recorded at draft time) is shown
to the *agent* next to a draft; it must never leak into anything a customer
could see — the accept flow only ever posts the body the agent submits, and
``AiDraftOut`` carries the trace as a separate structured field.
"""

from __future__ import annotations

import json
from datetime import datetime

from tiqora.ai.llm import LlmMessage, ToolCall
from tiqora.ai.runtime import _tool_trace_wire
from tiqora.api.v1.ai import AiDraftOut, _draft_out, parse_tool_trace


def test_tool_trace_wire_pairs_arguments_with_their_result() -> None:
    """Arguments live on the assistant message, results on the tool message —
    ``tool_call_id`` is what puts them back together."""
    messages = [
        LlmMessage(role="user", content="Kein Internet"),
        LlmMessage(
            role="assistant",
            tool_calls=[ToolCall(id="c1", name="kb_search", arguments={"query": "IPTV"})],
        ),
        LlmMessage(role="tool", tool_call_id="c1", name="kb_search", content="3 Treffer"),
    ]
    (step,) = _tool_trace_wire(messages)
    assert step["name"] == "kb_search"
    assert step["content"] == "3 Treffer"
    assert step["arguments"] == '{"query": "IPTV"}'


def test_tool_trace_wire_keeps_a_result_whose_call_it_cannot_find() -> None:
    # A result without a matching call must still appear: losing a step
    # entirely would be worse than showing it without its arguments.
    messages = [LlmMessage(role="tool", tool_call_id="gone", name="kb_search", content="x")]
    (step,) = _tool_trace_wire(messages)
    assert "arguments" not in step
    assert step["content"] == "x"


class _FakeDraft:
    id = 1
    ticket_id = 9600
    kind = "reply"
    subject = "Re: Hilfe"
    body = "Hallo, hier die Antwort."
    based_on_article_id = 42
    status = "open"
    source = "manual"
    accepted_article_id = None
    create_time = datetime(2026, 7, 23, 12, 0, 0)
    tool_trace_json = json.dumps(
        [
            {"role": "tool", "tool_call_id": "a", "name": "kb_search", "content": "3 Treffer"},
            {"role": "tool", "tool_call_id": "b", "name": "get_ticket", "content": "{...}"},
        ]
    )


def test_parse_tool_trace_happy_path() -> None:
    trace = parse_tool_trace(_FakeDraft.tool_trace_json)
    assert [(t.name, t.content) for t in trace] == [
        ("kb_search", "3 Treffer"),
        ("get_ticket", "{...}"),
    ]


def test_parse_tool_trace_carries_call_arguments() -> None:
    """What a tool was *asked* is the point of reading a trace.

    Nine kb_search entries say nothing without the queries, so the recorded
    entry keeps the arguments alongside the result.
    """
    raw = json.dumps(
        [
            {
                "role": "tool",
                "tool_call_id": "a",
                "name": "kb_search",
                "content": "3 Treffer",
                "arguments": '{"query": "IPTV StudNet"}',
            }
        ]
    )
    (step,) = parse_tool_trace(raw)
    assert step.arguments == '{"query": "IPTV StudNet"}'


def test_parse_tool_trace_reads_traces_recorded_before_arguments_existed() -> None:
    # Rows written by an older release have no "arguments" key at all; they
    # must keep rendering rather than disappear from the trace.
    (step,) = parse_tool_trace(
        json.dumps([{"role": "tool", "name": "kb_search", "content": "3 Treffer"}])
    )
    assert step.arguments is None
    assert step.content == "3 Treffer"


def test_parse_tool_trace_degrades_on_bad_payloads() -> None:
    assert parse_tool_trace(None) == []
    assert parse_tool_trace("") == []
    assert parse_tool_trace("not json") == []
    assert parse_tool_trace('{"role": "tool"}') == []  # not a list
    # Entries without string content are dropped; missing name gets a label.
    raw = json.dumps([{"content": 5}, "x", {"content": "ok"}])
    trace = parse_tool_trace(raw)
    assert [(t.name, t.content) for t in trace] == [("tool", "ok")]


def test_draft_out_carries_trace_separately_from_body() -> None:
    out = _draft_out(_FakeDraft())
    assert isinstance(out, AiDraftOut)
    assert out.body == "Hallo, hier die Antwort."
    assert [t.name for t in out.tool_trace] == ["kb_search", "get_ticket"]
    # The trace never merges into the customer-facing body text.
    assert "kb_search" not in out.body
    assert "3 Treffer" not in out.body


def test_draft_out_without_trace() -> None:
    class NoTrace(_FakeDraft):
        tool_trace_json = None

    assert _draft_out(NoTrace()).tool_trace == []
