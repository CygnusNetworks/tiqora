"""Response and request models of the change-management agent API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ChangeStatusOut(BaseModel):
    mode: Literal["off", "znuny_readthrough", "tiqora"]
    available: bool
    reason: Literal["ok", "itsm_off", "mode_a", "setup_missing"]
    legacy: Literal["none", "live", "archive"]
    can_manage: bool
    can_see_legacy: bool
    setup_complete_change: bool
