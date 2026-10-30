"""Change-management settings section: schema, defaults, thresholds, registration, labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tiqora.extensions import get_registry
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION, ItsmChangeSettings
from tiqora.settings_sections.section import SETTINGS_SECTIONS, validate_section_schema

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"


def test_section_schema_valid() -> None:
    validate_section_schema(ITSM_CHANGE_SECTION)
    cfg = ItsmChangeSettings()
    assert (cfg.risk_medium_from, cfg.risk_high_from, cfg.risk_very_high_from) == (10, 20, 35)
    assert cfg.approval_timeout == "notify" and cfg.cab_allow_self_approval is False
    assert cfg.manager_group == "" and cfg.legacy_change_group == "itsm-change"
    # the mirrored fields have fixed names; nothing to configure (F15)
    assert "type_field" not in ItsmChangeSettings.model_fields
    assert "phase_field" not in ItsmChangeSettings.model_fields


def test_section_is_registered() -> None:
    keys = {s.key for s in get_registry().items(SETTINGS_SECTIONS)}
    assert "itsm_change" in keys


@pytest.mark.parametrize(
    ("medium", "high", "very_high"),
    [(20, 20, 35), (30, 20, 35), (10, 20, 20), (10, 40, 35)],
)
def test_risk_thresholds_must_increase(medium: int, high: int, very_high: int) -> None:
    with pytest.raises(ValidationError) as err:
        ItsmChangeSettings(
            risk_medium_from=medium, risk_high_from=high, risk_very_high_from=very_high
        )
    assert "aufsteigen" in str(err.value)
    ItsmChangeSettings(risk_medium_from=5, risk_high_from=6, risk_very_high_from=7)


def test_timeout_only_notify_or_reject() -> None:
    assert ItsmChangeSettings(approval_timeout="reject").approval_timeout == "reject"
    with pytest.raises(ValidationError):
        ItsmChangeSettings(approval_timeout="approve")  # type: ignore[arg-type]


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_field_has_label_and_help(lang: str) -> None:
    tree = json.loads((LOCALES / f"{lang}.json").read_text("utf-8"))["admin"]["settings"]
    block = tree["itsmChange"]
    assert isinstance(block["title"], str) and block["title"]
    missing = [
        name
        for name in ItsmChangeSettings.model_fields
        if not (
            isinstance(block["fields"].get(name, {}).get("label"), str)
            and isinstance(block["fields"][name].get("help"), str)
        )
    ]
    assert missing == []
