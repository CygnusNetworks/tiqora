"""Pure tests of the wallboard redaction layer (E9, rulings on settings and report data).

No database: every function under test works on plain dicts and the extension registry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tiqora.dashboard.kiosk import (
    KIOSK_DERIVED_SETTINGS,
    KIOSK_RULES,
    KIOSK_SAFE_TICKET_FIELDS,
    KIOSK_SETTINGS_WHITELIST,
    MSG_HIDDEN,
    NON_PERSON_DIMENSIONS,
    PERSON_DIMENSIONS,
    kiosk_title,
    redact,
    redact_result,
    redact_settings,
)
from tiqora.dashboard.providers.report import MSG_NO_ACCESS
from tiqora.dashboard.runner import ERROR_MESSAGE
from tiqora.dashboard.schemas import WidgetDataOut
from tiqora.dashboard.settings_models import CORE_WIDGET_SETTINGS
from tiqora.domain.schemas import TicketListItem
from tiqora.extensions.core import get_registry
from tiqora.reporting.semantic import REPORT_SOURCES

TICKET: dict[str, Any] = {
    "id": 1,
    "tn": "2026101410000011",
    "title": "VPN gesperrt",
    "queue_id": 3,
    "queue_name": "Support",
    "state_id": 2,
    "lock_id": 1,
    "age_seconds": 99,
    "state": "open",
    "state_type": "open",
    "priority_id": 3,
    "priority": "3 normal",
    "customer_id": "Beispiel GmbH",
    "customer_user_id": "anna@example.org",
    "customer_email": "anna@example.org",
    "owner_name": "Anna Beispiel",
    "create_time": "2026-10-01T08:00:00Z",
    "change_time": "2026-10-01T09:00:00Z",
    "until_time": 0,
    "escalation_time": 5,
    "future_field": "x",
}
SECRETS = ("VPN gesperrt", "Beispiel GmbH", "anna@example.org", "Anna Beispiel", "future_field")


def _assert_neutral(item: dict[str, Any]) -> None:
    TicketListItem.model_validate(item)  # the wallboard gets a complete TicketListItem
    assert item["tn"] == TICKET["tn"] and item["queue_name"] == "Support"
    assert item["state_id"] == 2 and item["lock_id"] == 1 and item["age_seconds"] == 99
    assert item["title"] is None and item["owner_id"] == 0 and item["owner_name"] is None
    assert item["customer_id"] is None and item["customer_user_id"] is None
    assert item["customer_email"] is None and item["first_from"] is None
    assert item["channel"] == "email" and item["links"] == []
    assert item["chat_display_name"] is None and item["chat_username"] is None
    assert "future_field" not in item
    assert not any(s in json.dumps(item) for s in SECRETS)


def test_kiosk_safe_fields_are_ticket_list_item_fields() -> None:
    assert set(TicketListItem.model_fields) >= KIOSK_SAFE_TICKET_FIELDS
    assert {"state_id", "lock_id", "age_seconds"} <= KIOSK_SAFE_TICKET_FIELDS


def test_redaction_whitelist_builds_neutral_ticket_list_item() -> None:
    out = redact("core.ticket-list", {"items": [TICKET], "total": 1}, show_details=False)
    assert out.status == "ok" and out.data is not None
    _assert_neutral(out.data["items"][0])
    assert out.data["total"] == 1


def test_redaction_neutral_item_for_escalations_and_reminders() -> None:
    for key in ("core.escalations", "core.reminders"):
        data = {"items": [{"ticket": TICKET, "epoch": 5, "kind": "solution"}], "total": 1}
        out = redact(key, data, show_details=False)
        assert out.data is not None
        _assert_neutral(out.data["items"][0]["ticket"])


def test_redaction_survives_a_source_missing_required_fields() -> None:
    out = redact("core.ticket-list", {"items": [{"tn": "1", "title": "x"}]}, show_details=False)
    assert out.data is not None
    item = TicketListItem.model_validate(out.data["items"][0])
    assert item.title is None and item.tn == "1"


def test_details_keep_ticket_fields_but_still_run_through_the_rule() -> None:
    out = redact("core.ticket-list", {"items": [TICKET], "total": 1}, show_details=True)
    assert out.data is not None and out.data["items"][0]["title"] == "VPN gesperrt"


def test_escalations_and_reminders_redact_the_nested_ticket() -> None:
    for key in ("core.escalations", "core.reminders"):
        data = {"items": [{"ticket": TICKET, "epoch": 5, "kind": "solution"}], "total": 1}
        out = redact(key, data, show_details=False)
        assert out.data is not None
        text = json.dumps(out.data)
        assert not any(s in text for s in SECRETS)
        assert out.data["items"][0]["epoch"] == 5 and out.data["items"][0]["ticket"]["tn"]


def test_unknown_provider_key_is_forbidden_on_kiosk() -> None:
    for details in (False, True):
        out = redact("acme.plugin-widget", {"anything": 1}, show_details=details)
        assert out.status == "forbidden" and out.error == MSG_HIDDEN and out.data is None
    for key in ("core.notes", "core.links", "core.recent-views"):
        assert key not in KIOSK_RULES
        assert redact(key, {}, show_details=True).status == "forbidden"


def test_calendar_hidden_without_details_and_team_reduced_to_counts() -> None:
    agenda = {"items": [{"title": "Termin bei Beispiel GmbH"}]}
    assert redact("core.calendar-agenda", agenda, show_details=False).status == "forbidden"
    shown = redact("core.calendar-agenda", agenda, show_details=True)
    assert shown.status == "ok" and shown.data == agenda

    # the provider cut ``online`` to the widget limit; ``by_status`` counts everyone online
    team = {
        "online": [
            {"id": 1, "full_name": "Anna Beispiel", "status": "available"},
            {"id": 2, "full_name": "Boris Beispiel", "status": "in_chat"},
            {"id": 3, "full_name": "Cora Beispiel", "status": "available"},
        ],
        "total_online": 5,
        "by_status": {"available": 3, "in_chat": 2},
    }
    out = redact("core.team-presence", team, show_details=False)
    assert out.data == {"total_online": 5, "by_status": {"available": 3, "in_chat": 2}}
    assert out.data is not None and sum(out.data["by_status"].values()) == out.data["total_online"]
    assert "Beispiel" not in json.dumps(out.data)
    assert redact("core.team-presence", team, show_details=True).data == team
    # an answer without ``by_status`` (older provider) is still counted from the shown people
    legacy = {k: v for k, v in team.items() if k != "by_status"}
    assert redact("core.team-presence", legacy, show_details=False).data == {
        "total_online": 5,
        "by_status": {"available": 2, "in_chat": 1},
    }


def test_chat_queue_drops_items_and_never_carries_a_visitor_name() -> None:
    chat = {
        "enabled": True,
        "waiting": 2,
        "oldest_waiting_since": "2026-10-01T08:00:00Z",
        "mine": 1,
        "items": [{"session_id": "s", "tn": "1", "title": "t", "visitor_name": "Max Beispiel"}],
    }
    plain = redact("core.chat-queue", chat, show_details=False)
    assert plain.data is not None and "items" not in plain.data and plain.data["waiting"] == 2
    detailed = redact("core.chat-queue", chat, show_details=True)
    assert detailed.data is not None and "visitor_name" not in json.dumps(detailed.data)
    assert detailed.data["items"][0]["tn"] == "1"


def test_ai_provider_costs_always_dropped() -> None:
    ai = {
        "ai_escalated": 2,
        "runs_today": {"draft": 4},
        "budgets": [
            {
                "kind": "provider_cost",
                "subject_name": "Hetzner",
                "used": 1.5,
                "limit": 5,
                "currency": "EUR",
            },
            {
                "kind": "queue_tokens_day",
                "subject_name": "Support",
                "used": 10,
                "limit": 100,
                "currency": "EUR",
            },
        ],
    }
    # E9: only provider cost budgets (money) and the currency are dropped; queue token budgets
    # stay with and without details (queue names leave through the queue overview as well)
    for details in (False, True):
        out = redact("core.ai-status", ai, show_details=details)
        assert out.data is not None
        assert out.data["ai_escalated"] == 2 and out.data["runs_today"] == {"draft": 4}
        assert [b["kind"] for b in out.data["budgets"]] == ["queue_tokens_day"]
        assert out.data["budgets"][0]["subject_name"] == "Support"
        assert "currency" not in json.dumps(out.data)
        assert "Hetzner" not in json.dumps(out.data)


def _report(dimensions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "report": {"key": "rep:7", "kind": "saved", "name": "Intern"},
        "display": "auto",
        "deferred": False,
        "definition": {
            "source": "tickets",
            "dimensions": dimensions,
            "measures": [{"key": "tickets"}],
            "visualization": {"type": "bar"},
            "filter": {
                "where": {"field": "customer_user_id", "op": "eq", "value": "a@example.org"}
            },
            "parameters": [{"field": "customer", "default": "Beispiel GmbH"}],
        },
        "result": {"report_key": "rep:7", "columns": [], "rows": []},
        "ignored_filters": [],
        **extra,
    }


def test_report_without_details_drops_filter_and_parameters_and_keeps_render_data() -> None:
    out = redact("core.report", _report([{"key": "queue"}]), show_details=False)
    assert out.status == "ok" and out.data is not None
    assert set(out.data["definition"]) == {"source", "dimensions", "measures", "visualization"}
    assert "a@example.org" not in json.dumps(out.data) and "Beispiel GmbH" not in json.dumps(
        out.data
    )
    assert out.data["report"]["key"] is None and out.data["report"]["name"] is None
    assert "report_key" not in out.data["result"]


@pytest.mark.parametrize("key", sorted(PERSON_DIMENSIONS))
def test_report_with_person_dimension_is_forbidden_without_details(key: str) -> None:
    for widget in ("core.report", "core.backlog-aging"):
        out = redact(widget, _report([{"key": key}]), show_details=False)
        assert out.status == "forbidden" and out.error == MSG_HIDDEN
    # a runtime alternative that names a person counts as well
    sneaky = _report([{"key": "queue", "choices": [key]}])
    assert redact("core.report", sneaky, show_details=False).status == "forbidden"
    assert redact("core.report", _report([{"key": key}]), show_details=True).status == "ok"


def test_report_with_unclassified_dimension_fails_closed() -> None:
    out = redact("core.report", _report([{"key": "brand_new_dimension"}]), show_details=False)
    assert out.status == "forbidden"


def test_report_result_without_definition_fails_closed() -> None:
    data = _report([{"key": "queue"}])
    del data["definition"]
    assert redact("core.report", data, show_details=False).status == "forbidden"


def test_deferred_report_passes_without_a_definition() -> None:
    data = {
        "report": {"key": None, "kind": "inline", "name": None},
        "deferred": True,
        "kiosk": True,
    }
    out = redact("core.heatmap", data, show_details=False)
    assert out.status == "ok" and out.data is not None and out.data["deferred"] is True


def test_person_dimensions_cover_every_registered_source() -> None:
    assert not PERSON_DIMENSIONS & NON_PERSON_DIMENSIONS
    sources = get_registry().items(REPORT_SOURCES)
    assert sources
    seen: set[str] = set()
    for src in sources:
        for dim in src.dimensions:
            seen.add(dim.key)
            assert dim.key in PERSON_DIMENSIONS | NON_PERSON_DIMENSIONS, (
                f"{src.key}.{dim.key} is not classified in reporting/person_dimensions.py"
            )
            if dim.labels in {"user", "customer_company", "customer_user", "ticket"}:
                assert dim.key in PERSON_DIMENSIONS, f"{src.key}.{dim.key} labels people"
    assert seen >= PERSON_DIMENSIONS


def test_redact_result_passes_non_ok_statuses_without_data() -> None:
    pending = WidgetDataOut(status="pending")
    assert redact_result("core.counter", pending, False).status == "pending"
    assert (
        redact_result("core.counter", WidgetDataOut(status="error", error="x"), True).error == "x"
    )
    assert (
        redact_result("acme.x", WidgetDataOut(status="ok", data={"a": 1}), True).status
        == "forbidden"
    )
    ok = WidgetDataOut(status="ok", data={"items": [TICKET], "total": 1})
    assert redact_result("core.ticket-list", ok, False).data is not None


def test_every_whitelisted_type_has_a_rule_and_a_settings_model() -> None:
    assert set(KIOSK_SETTINGS_WHITELIST) == set(KIOSK_RULES)
    for key, allowed in KIOSK_SETTINGS_WHITELIST.items():
        assert allowed <= set(CORE_WIDGET_SETTINGS[key].model_fields), key


def test_settings_leaving_the_server_stay_inside_the_whitelist() -> None:
    """One widget of every core type, with a filter source naming a customer."""
    source = {
        "kind": "filter",
        "filter": {"where": {"field": "customer_user_id", "op": "eq", "value": "anna@example.org"}},
    }
    full: dict[str, dict[str, Any]] = {}
    for key, model in CORE_WIDGET_SETTINGS.items():
        values: dict[str, Any] = {}
        if "source" in model.model_fields:
            values["source"] = source
        if key == "core.report":
            values["report_key"] = "424242"
            values["parameters"] = {"customer": "Beispiel GmbH"}
        if key == "core.notes":
            values["markdown"] = "Zugang: geheim"
        if key == "core.links":
            values["items"] = [{"label": "Intern", "url": "https://intranet.example.org"}]
        settings = model.model_validate(values).model_dump(mode="json")
        full[key] = settings
    assert set(full) == set(CORE_WIDGET_SETTINGS) and len(full) == 16
    for key, settings in full.items():
        for details in (False, True):
            out = redact_settings(key, settings, details)
            allowed = KIOSK_SETTINGS_WHITELIST.get(key, frozenset()) | KIOSK_DERIVED_SETTINGS.get(
                key, frozenset()
            )
            assert set(out) <= allowed, key
            text = json.dumps(out)
            assert not any(
                bad in text
                for bad in (
                    "customer_user_id",
                    "anna@example.org",
                    "424242",
                    "geheim",
                    "intranet",
                    "Beispiel GmbH",
                )
            ), key
    # notes, links and recent views have no kiosk rule: nothing of their settings leaves
    for key in ("core.notes", "core.links", "core.recent-views"):
        assert redact_settings(key, full[key], True) == {}
    assert redact_settings("core.counter", full["core.counter"], False) == {"tone": "neutral"}
    assert redact_settings("acme.plugin", {"a": 1}, True) == {}


def test_title_override_only_with_details() -> None:
    assert kiosk_title("Kunde Beispiel GmbH", False) is None
    assert kiosk_title("Kunde Beispiel GmbH", True) == "Kunde Beispiel GmbH"
    assert kiosk_title(None, True) is None


def test_preset_name_survives_for_preset_sources_only() -> None:
    for name in ("mine_open", "mine_new", "unowned_new", "escalated", "ai_escalated"):
        src = {"kind": "preset", "preset": name}
        for key in ("core.counter", "core.ticket-list"):
            assert redact_settings(key, {"source": src, "tone": "ok"}, False).get("preset") == name
    for src in (
        {"kind": "saved_search", "id": 7},
        {
            "kind": "filter",
            "filter": {"where": {"field": "customer_user_id", "op": "eq", "value": "a"}},
        },
        {"kind": "preset", "preset": "<script>"},
    ):
        out = redact_settings("core.counter", {"source": src}, True)
        assert "preset" not in out and "source" not in out
    assert "preset" not in redact_settings(
        "core.queue-overview", {"source": {"kind": "preset"}}, True
    )


def test_error_texts_are_generic_without_details() -> None:
    """A provider error may quote a saved report's name or a stored filter literal."""
    gone = WidgetDataOut(status="error", error="Der Bericht „Kunde Beispiel GmbH“ wurde gelöscht.")
    for key in ("core.report", "core.sla-gauge", "core.counter"):
        out = redact_result(key, gone, False)
        assert out.status == "error" and out.data is None
        assert out.error == ERROR_MESSAGE
        assert "Beispiel" not in json.dumps(out.model_dump())
    shown = redact_result("core.report", gone, True)
    assert shown.status == "error" and shown.error == gone.error
    # constant texts of the other statuses stay
    forbidden = WidgetDataOut(status="forbidden", error=MSG_NO_ACCESS)
    assert redact_result("core.report", forbidden, False).error == forbidden.error


def test_chat_queue_never_sends_the_link_creators_own_count() -> None:
    chat = {"enabled": True, "waiting": 3, "oldest_waiting_since": None, "mine": 2, "items": []}
    for details in (False, True):
        out = redact("core.chat-queue", chat, show_details=details)
        assert out.data is not None and "mine" not in out.data and out.data["waiting"] == 3
