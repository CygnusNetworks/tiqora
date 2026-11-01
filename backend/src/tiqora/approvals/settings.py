"""Approvals settings section (admin form from the schema; stored as ``approvals.<field>``)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.settings_store import get_setting_bool
from tiqora.settings_sections.section import SettingsSection
from tiqora.settings_sections.store import load_section_values


class ApprovalSettings(BaseModel):
    #: the module switch: off hides and refuses everything, deletes nothing (docs/approvals.md)
    enabled: bool = True
    default_due_hours: int = Field(0, ge=0, le=8760)  # 0 = no deadline unless the step sets one
    reminder_interval_hours: int = Field(24, ge=0, le=720)  # 0 = no reminders
    reminder_max: int = Field(3, ge=0, le=20)
    email_links_enabled: bool = True
    link_ttl_hours: int = Field(168, ge=1, le=2160)
    max_links_per_approver: int = Field(5, ge=1, le=20)
    customer_approvers_enabled: bool = False
    #: only effective with customer approvers on and the portal on
    portal_approvals_enabled: bool = True
    #: ticket_history "Misc" lines
    history_entries: bool = True
    #: internal note on the final decision
    decision_note: bool = False
    mail_requester_on_decision: bool = True
    #: MCP tool ``approval_decide`` records a binding vote; off unless an admin allows it
    mcp_decide_enabled: bool = False


APPROVALS_SECTION = SettingsSection(
    key="approvals",
    model=ApprovalSettings,
    title_key="admin.settings.approvals.title",
    i18n_prefix="admin.settings.approvals",
)


async def load_approval_settings(session: AsyncSession, settings: Settings) -> ApprovalSettings:
    """The one way every approvals module loads its settings (the section has no secrets)."""
    values = await load_section_values(session, APPROVALS_SECTION, secret_key=settings.secret_key)
    return ApprovalSettings.model_validate(values.model_dump())


async def approvals_enabled(session: AsyncSession) -> bool:
    """The module switch alone: one row, read fresh on every call (no cache, all roles)."""
    return await get_setting_bool(session, APPROVALS_SECTION.storage_key("enabled"), True)
