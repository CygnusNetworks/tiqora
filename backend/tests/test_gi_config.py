"""Pure tests of the requester config model (``tiqora.gi.config``)."""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from tests._gi_fixtures import requester_config
from tiqora.gi.config import is_ticket_event, parse_debugger, parse_requester


def _requester(invokers: dict[str, Any], **kwargs: Any) -> Any:
    return parse_requester(requester_config(invokers=invokers, **kwargs), huge_xml=False)


def test_parse_minimal_requester() -> None:
    cfg = requester_config(
        invokers={
            "SendTicket": {
                "Type": "Ticket::Generic",
                "Events": [
                    {"Event": "TicketCreate", "Asynchronous": "1"},
                    {
                        "Event": "TicketStateUpdate",
                        "Asynchronous": "0",
                        "Condition": {
                            "ConditionLinking": "and",
                            "Condition": {
                                "1": {
                                    "Type": "and",
                                    "Fields": {"Queue": {"Type": "String", "Match": "Raw"}},
                                }
                            },
                        },
                    },
                ],
            }
        }
    )
    r = parse_requester(cfg, huge_xml=False)
    inv = r.invokers["SendTicket"]
    assert inv.supported and [e.event for e in inv.events] == ["TicketCreate", "TicketStateUpdate"]
    assert inv.events[0].asynchronous is True and inv.events[1].asynchronous is False
    assert inv.events[1].condition is not None and inv.events[0].condition is None
    assert r.transport is not None and r.transport.type == "HTTP::REST"
    assert r.transport.config["Host"] == "https://crm.example.org"


def test_events_skip_bad_entries() -> None:
    r = _requester(
        {
            "SendTicket": {
                "Type": "Ticket::Generic",
                "Events": [
                    "TicketCreate",
                    {"Event": ""},
                    {"Asynchronous": "1"},
                    {"Event": None},
                    {"Event": "TicketCreate"},
                ],
            }
        }
    )
    assert [e.event for e in r.invokers["SendTicket"].events] == ["TicketCreate"]
    assert r.unsupported_events == ()


def test_unsupported_type_and_mapping_error_flagged() -> None:
    r = _requester(
        {
            "Other": {"Type": "Test::Test", "Events": [{"Event": "TicketCreate"}]},
            "BadMap": {
                "Type": "Ticket::Generic",
                "MappingOutbound": {"Type": "Nope"},
                "MappingInbound": {
                    "Type": "Simple",
                    "Config": {
                        "KeyMapDefault": {"MapType": "Keep"},
                        "ValueMapDefault": {"MapType": "Keep"},
                    },
                },
            },
            "NoMap": {"Type": "Ticket::Generic", "MappingOutbound": {}},
        }
    )
    assert r.invokers["Other"].supported is False
    bad = r.invokers["BadMap"]
    assert bad.supported is True
    assert bad.mapping_error is not None and "MappingOutbound" in bad.mapping_error
    assert bad.mapping_outbound is None and bad.mapping_inbound is not None
    none = r.invokers["NoMap"]
    assert none.mapping_error is None and none.mapping_outbound is None
    assert list(r.invokers) == ["BadMap", "NoMap", "Other"]  # sorted by name


def test_invoker_options_parsed() -> None:
    r = _requester(
        {
            "A": {
                "Type": "Ticket::Generic",
                "GetAllArticleAttachments": "1",
                "SortOutbound": ["x"],
                "OperationName": "Op",
            }
        }
    )
    inv = r.invokers["A"]
    assert inv.get_all_article_attachments is True
    assert inv.sort_outbound == ["x"] and inv.operation_name == "Op"


def test_error_handling_priority_order_and_missing_keys() -> None:
    mods = {
        "a": {
            "Type": "RequestRetry",
            "InvokerFilter": "SendTicket",
            "StopAfterMatch": "all",
            "ErrorStageFilter": ["RequestPrepare", "ResponseMap"],
            "ErrorMessageContentFilter": "timeout",
        },
        "b": {"Type": "Other"},
    }
    cfg = requester_config(invokers={"X": {"Type": "Ticket::Generic"}}, error_handling=mods)
    cfg["Requester"]["ErrorHandlingPriority"] = ["b", "missing", "a"]
    r = parse_requester(cfg, huge_xml=False)
    assert tuple(m.key for m in r.error_handling) == ("b", "a")
    a = r.error_handling[1]
    assert a.type == "RequestRetry" and a.invoker_filter == ("SendTicket",)
    assert a.error_stage_filter == ("RequestPrepare", "ResponseMap")
    assert a.error_message_content_filter == "timeout" and a.stop_after_match == "all"
    assert r.error_handling[0].invoker_filter == () and r.error_handling[0].stop_after_match is None


def test_debugger_threshold_defaults_to_error() -> None:
    assert parse_debugger({}).threshold == "error"
    with structlog.testing.capture_logs() as logs:
        assert parse_debugger({"Debugger": {"DebugThreshold": "loud"}}).threshold == "error"
    assert any(e["event"] == "gi_debug_threshold_invalid" for e in logs)
    cfg = parse_debugger({"Debugger": {"DebugThreshold": "notice", "TestMode": "0"}})
    assert cfg.threshold == "notice" and cfg.test_mode is False
    assert parse_debugger({"Debugger": {"TestMode": "1"}}).test_mode is True


def test_non_ticket_events_listed_unsupported() -> None:
    r = _requester(
        {
            "SendTicket": {
                "Type": "Ticket::Generic",
                "Events": [{"Event": "QueueUpdate"}, {"Event": "TicketCreate"}],
            }
        }
    )
    assert r.unsupported_events == (("SendTicket", "QueueUpdate"),)
    assert [e.event for e in r.invokers["SendTicket"].events] == ["TicketCreate"]


def test_missing_requester_section() -> None:
    r = parse_requester({"Provider": {}}, huge_xml=False)
    assert r.transport is None and r.invokers == {} and r.error_handling == ()


@pytest.mark.parametrize(
    ("event", "ok"),
    [
        ("TicketCreate", True),
        ("ArticleCreate", True),
        ("TicketDynamicFieldUpdate_Foo", True),
        ("TicketDynamicFieldUpdate_", False),
        ("TicketQueueUpdate", True),
        ("QueueUpdate", False),
        ("ArticleSend", False),
    ],
)
def test_is_ticket_event(event: str, ok: bool) -> None:
    assert is_ticket_event(event) is ok
