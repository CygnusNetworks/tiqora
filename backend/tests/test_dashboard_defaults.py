from tiqora.dashboard.defaults import DEFAULT_TEMPLATE_KEY, default_widgets
from tiqora.dashboard.layout import normalize_rects
from tiqora.dashboard.settings_models import validate_widget_settings


def test_default_template_reproduces_todays_home_page() -> None:
    ws = default_widgets()
    assert DEFAULT_TEMPLATE_KEY == "core.default"
    assert [w.type_key for w in ws] == (
        ["core.counter"] * 4
        + ["core.escalations", "core.ticket-list", "core.ticket-list", "core.ticket-list"]
        + ["core.reminders", "core.recent-views", "core.queue-overview"]
    )
    assert [w.settings["source"]["preset"] for w in ws[:4]] == [
        "mine_open",
        "mine_new",
        "unowned_new",
        "escalated",
    ]
    assert ws[3].settings["tone"] == "danger"
    assert ws[4].settings == {"limit": 8, "within_hours": 0}
    assert ws[7].title_override == "Team-Eingang (zum Übernehmen)"
    assert {w.id for w in ws}.isdisjoint({w.id for w in default_widgets()})


def test_default_template_is_valid_and_overlap_free() -> None:
    ws = default_widgets()
    for w in ws:
        validate_widget_settings(w.type_key, w.settings)
    rects = {w.id: w.layout.lg for w in ws}
    assert normalize_rects(list(rects.items())) == rects
    assert max(r.y + r.h for r in rects.values()) == 20
