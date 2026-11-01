"""Approvals settings section (A4): schema, defaults and validation. Own rows 970600-970649."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests._approvals_helpers import approvals_session
from tiqora.approvals.settings import APPROVALS_SECTION, load_approval_settings
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.settings_sections.section import validate_section_schema
from tiqora.settings_sections.store import SettingsValidationError, save_section_values

SETTINGS = Settings(environment="test")
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


def test_section_schema_valid() -> None:
    validate_section_schema(APPROVALS_SECTION)
    assert APPROVALS_SECTION.key == "approvals"
    assert APPROVALS_SECTION.title_key == "admin.settings.approvals.title"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_settings_defaults(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture, world=False) as session:
        # a stray ``approvals.*`` row of another module would show here
        stray = (
            await session.execute(
                select(TiqoraSettings.key).where(TiqoraSettings.key.like("approvals.%"))
            )
        ).all()
        assert stray == []
        got = await load_approval_settings(session, SETTINGS)
    assert got.model_dump() == {
        "enabled": True,
        "default_due_hours": 0,
        "reminder_interval_hours": 24,
        "reminder_max": 3,
        "email_links_enabled": True,
        "link_ttl_hours": 168,
        "max_links_per_approver": 5,
        "customer_approvers_enabled": False,
        "portal_approvals_enabled": True,
        "history_entries": True,
        "decision_note": False,
        "mail_requester_on_decision": True,
        "mcp_decide_enabled": False,
    }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_settings_validation(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture, world=False) as session:
        with pytest.raises(SettingsValidationError) as zero:
            await save_section_values(
                session, APPROVALS_SECTION, {"link_ttl_hours": 0}, secret_key=SETTINGS.secret_key
            )
        assert "link_ttl_hours" in zero.value.args[0]
        with pytest.raises(SettingsValidationError) as high:
            await save_section_values(
                session,
                APPROVALS_SECTION,
                {"reminder_interval_hours": 721},
                secret_key=SETTINGS.secret_key,
            )
        assert "reminder_interval_hours" in high.value.args[0]
        # a valid change round-trips (and is rolled back with the session)
        await save_section_values(
            session,
            APPROVALS_SECTION,
            {"link_ttl_hours": 24, "mcp_decide_enabled": True},
            secret_key=SETTINGS.secret_key,
        )
        got = await load_approval_settings(session, SETTINGS)
        assert got.link_ttl_hours == 24 and got.mcp_decide_enabled is True
