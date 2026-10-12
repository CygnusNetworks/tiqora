import pytest

from tiqora.dashboard.settings_models import (
    CORE_WIDGET_SETTINGS,
    SettingsError,
    validate_widget_settings,
)


@pytest.mark.parametrize("key", ["builtin.volume", "42", "9223372036854775807", "builtin.a_1"])
def test_report_key_pattern_accepts(key: str) -> None:
    out = validate_widget_settings("core.report", {"report_key": key})
    assert out["report_key"] == key
    assert out["max_rows"] == 10
    assert out["display"] == "auto"


@pytest.mark.parametrize("key", ["0", "01", "builtin.", "../x", "builtin.A", "", "builtin.a b"])
def test_report_key_pattern_rejects(key: str) -> None:
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.report", {"report_key": key})
    assert e.value.path == "settings.report_key"


def test_report_key_is_required() -> None:
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.report", {})
    assert e.value.path == "settings.report_key"
    assert e.value.message == "Dieses Feld ist erforderlich."


@pytest.mark.parametrize("rows", [0, 51])
def test_report_settings_bounds(rows: int) -> None:
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.report", {"report_key": "builtin.x", "max_rows": rows})
    assert e.value.message == "Der Wert muss zwischen 1 und 50 liegen."
    assert e.value.path == "settings.max_rows"


def test_report_parameter_and_dimension_choice_bounds() -> None:
    base = {"report_key": "builtin.x"}
    ok = validate_widget_settings(
        "core.report",
        {
            **base,
            "parameters": {f"p{i}": [1] for i in range(10)},
            "dimension_choices": {"0": "queue"},
        },
    )
    assert len(ok["parameters"]) == 10
    with pytest.raises(SettingsError) as e:
        validate_widget_settings(
            "core.report", {**base, "parameters": {f"p{i}": 1 for i in range(11)}}
        )
    assert e.value.path == "settings.parameters"
    with pytest.raises(SettingsError) as e:
        validate_widget_settings("core.report", {**base, "dimension_choices": {"10": "queue"}})
    assert e.value.path.startswith("settings.dimension_choices")


def test_report_unknown_field_and_time_range() -> None:
    with pytest.raises(SettingsError):
        validate_widget_settings("core.report", {"report_key": "builtin.x", "x": 1})
    with pytest.raises(SettingsError):
        validate_widget_settings("core.report", {"report_key": "builtin.x", "time_range": "1y"})
    out = validate_widget_settings("core.report", {"report_key": "builtin.x", "time_range": "7d"})
    assert out["time_range"] == "7d"


def test_other_new_settings_defaults_and_bounds() -> None:
    assert validate_widget_settings("core.sla-gauge", {}) == {
        "time_range": "30d",
        "target_percent": 90,
    }
    assert validate_widget_settings("core.backlog-aging", {}) == {"max_rows": 8}
    assert validate_widget_settings("core.heatmap", {}) == {"time_range": "90d"}
    assert validate_widget_settings("core.chat-queue", {}) == {"limit": 5}
    assert validate_widget_settings("core.ai-status", {}) == {}
    for type_key, bad in [
        ("core.sla-gauge", {"target_percent": 49}),
        ("core.sla-gauge", {"target_percent": 101}),
        ("core.backlog-aging", {"max_rows": 16}),
        ("core.heatmap", {"time_range": "7d"}),
        ("core.chat-queue", {"limit": 11}),
        ("core.ai-status", {"x": 1}),
    ]:
        with pytest.raises(SettingsError):
            validate_widget_settings(type_key, bad)


def test_new_types_registered() -> None:
    assert {
        "core.report",
        "core.sla-gauge",
        "core.backlog-aging",
        "core.heatmap",
        "core.chat-queue",
        "core.ai-status",
    } <= set(CORE_WIDGET_SETTINGS)
