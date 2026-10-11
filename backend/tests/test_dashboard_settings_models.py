from typing import Any

import pytest
from pydantic import ValidationError

from tiqora.dashboard.schemas import DashboardSaveIn
from tiqora.dashboard.settings_models import SettingsError, safe_link_url, validate_widget_settings


def _widget(i: int) -> dict[str, Any]:
    return {
        "id": f"widget-{i:04d}",
        "type_key": "core.notes",
        "settings": {},
        "layout": {"lg": {"x": 0, "y": i, "w": 3, "h": 1}},
    }


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        " javascript:alert(1)",
        "JaVaScRiPt:1",
        "data:text/html,x",
        "//evil.example.org",
        "vbscript:x",
        "\x01javascript:x",
    ],
)
def test_link_url_scheme_whitelist(url: str) -> None:
    with pytest.raises(SettingsError) as e:
        safe_link_url(url)
    assert "Adresse" in e.value.message


@pytest.mark.parametrize(
    "url",
    ["/agent/queues", "https://example.org/a?b=1", "http://example.org", "mailto:team@example.org"],
)
def test_safe_link_urls_pass(url: str) -> None:
    assert safe_link_url(url) == url


def test_links_settings_reject_unsafe_url() -> None:
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.links", {"items": [{"label": "x", "url": "javascript:1"}]})
    assert e.value.message == "Diese Adresse ist nicht erlaubt."
    assert e.value.path == "settings.items.0.url"
    ok = validate_widget_settings("core.links", {"items": [{"label": "x", "url": "/agent"}]})
    assert ok["items"] == [{"label": "x", "url": "/agent"}]


def test_widget_count_and_settings_size_limits() -> None:
    body = {"name": "x", "version": 1, "widgets": [_widget(i) for i in range(41)]}
    with pytest.raises(ValidationError):
        DashboardSaveIn.model_validate(body)
    body["widgets"] = [_widget(i) for i in range(40)]
    assert len(DashboardSaveIn.model_validate(body).widgets) == 40
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("org.example.big", {"x": "a" * 21_000})
    assert e.value.path == "settings"
    assert e.value.message == "Die Einstellungen sind zu groß."
    with pytest.raises(SettingsError):
        validate_widget_settings("core.notes", {"markdown": "a" * 5001})


def test_duplicate_widget_ids_and_blank_name_are_rejected() -> None:
    w = _widget(1)
    with pytest.raises(ValidationError, match="Widget-IDs müssen eindeutig sein."):
        DashboardSaveIn.model_validate({"name": "x", "version": 1, "widgets": [w, w]})
    with pytest.raises(ValidationError):
        DashboardSaveIn.model_validate({"name": "   ", "version": 1, "widgets": []})
    assert (
        DashboardSaveIn.model_validate({"name": " Start ", "version": 1, "widgets": []}).name
        == "Start"
    )


def test_defaults_are_applied_and_unknown_fields_rejected() -> None:
    assert validate_widget_settings("core.ticket-list", {})["columns"] == [
        "tn",
        "title",
        "customer",
        "state",
        "due",
    ]
    assert validate_widget_settings("core.ticket-list", {})["source"] == {
        "kind": "preset",
        "preset": "mine_open",
    }
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.counter", {"colour": "red"})
    assert e.value.path == "settings.colour"


def test_ticket_list_limit_and_columns_bounds() -> None:
    for bad in (
        {"limit": 0},
        {"limit": 51},
        {"columns": []},
        {"columns": ["tn", "tn"]},
        {"columns": ["bogus"]},
    ):
        with pytest.raises(SettingsError):
            validate_widget_settings("core.ticket-list", bad)
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.ticket-list", {"limit": 51})
    assert e.value.path == "settings.limit"
    assert e.value.message == "Der Wert muss zwischen 1 und 50 liegen."


def test_escalations_defaults_to_upcoming_deadlines() -> None:
    assert validate_widget_settings("core.escalations", {}) == {"limit": 8, "within_hours": 8}


def test_unknown_widget_type_settings_pass_through_unchanged() -> None:
    assert validate_widget_settings("org.example.weather", {"city": "Bonn"}) == {"city": "Bonn"}


def _link_url_cases() -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "frontend/src/dashboard/settings/linkUrlCases.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _link_url_cases(), ids=lambda c: repr(c["url"]))
def test_link_url_shared_cases_match_the_client_mirror(case: dict[str, Any]) -> None:
    """The same cases run in `frontend/src/dashboard/settings/linkUrl.test.ts`."""
    if case["ok"]:
        assert safe_link_url(case["url"]) == case["url"]
    else:
        with pytest.raises(SettingsError):
            safe_link_url(case["url"])
