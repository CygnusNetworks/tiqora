"""ITSM dialog screens of ``GET /reference/dynamic-fields`` (Task 21).

Both dialects. On an ITSM database the Znuny configuration is used; with the SysConfig key absent
(no ITSM packages) the field lists the packages ship (Task 3 survey) apply, but only for dynamic
fields that exist and are valid. Rows are rolled back with the session (band 941600-941699).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text

from tests.test_itsm_summary import World, world_for
from tiqora.itsm.summary import ITSM_SCREEN_DEFAULTS
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db


@pytest.fixture(params=["mysql", "postgresql"])
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


ADDTL = "AgentTicketAddtlITSMField"
DECISION = "AgentTicketDecision"


async def _screen(w: World, screen: str) -> list[dict[str, Any]]:
    got = await w.client.get("/api/v1/reference/dynamic-fields", params={"screen": screen})
    assert got.status_code == 200, got.text
    body: list[dict[str, Any]] = got.json()
    return body


async def test_itsm_screens_from_sysconfig(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    addtl = await _screen(w, ADDTL)
    assert {f["name"] for f in addtl} == {
        "ITSMRepairStartTime",
        "ITSMRecoveryStartTime",
        "ITSMDueDate",
    }
    assert {f["field_type"] for f in addtl} == {"DateTime"}  # internal fields are offered here
    decision = await _screen(w, DECISION)
    by_name = {f["name"]: f for f in decision}
    assert set(by_name) == {"ITSMDecisionResult", "ITSMDecisionDate"}
    assert by_name["ITSMDecisionResult"]["field_type"] == "Dropdown"
    assert "Approved" in (by_name["ITSMDecisionResult"]["possible_values"] or {})
    assert by_name["ITSMDecisionResult"]["required"] is False

    # the Znuny setting decides, not the shipped list: one field, required
    original = SysConfig.get

    async def patched(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == f"Ticket::Frontend::{DECISION}###DynamicField":
            return {"ITSMDecisionDate": "2"}
        if name == f"Ticket::Frontend::{ADDTL}###DynamicField":
            return {}  # an admin who emptied the screen: no fallback to the shipped list
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", patched)
    only = await _screen(w, DECISION)
    assert [(f["name"], f["required"]) for f in only] == [("ITSMDecisionDate", True)]
    assert await _screen(w, ADDTL) == []


async def test_itsm_screens_fallback_without_znuny_itsm(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Key absent (no Znuny ITSM): Task 3 defaults, limited to fields that exist and are valid."""
    original = SysConfig.get

    async def absent(self: SysConfig, name: str, default: Any = None) -> Any:
        if name in (
            f"Ticket::Frontend::{ADDTL}###DynamicField",
            f"Ticket::Frontend::{DECISION}###DynamicField",
        ):
            return default
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", absent)
    assert {f["name"] for f in await _screen(w, ADDTL)} == set(ITSM_SCREEN_DEFAULTS[ADDTL])
    assert {f["name"] for f in await _screen(w, DECISION)} == set(ITSM_SCREEN_DEFAULTS[DECISION])
    await w.session.execute(
        text("UPDATE dynamic_field SET valid_id = 2 WHERE name = 'ITSMDueDate'")
    )
    assert {f["name"] for f in await _screen(w, ADDTL)} == {
        "ITSMRepairStartTime",
        "ITSMRecoveryStartTime",
    }


async def test_other_screens_still_hide_internal_fields(w: World) -> None:
    """The internal-field filter applies to every non-ITSM screen; unknown screens are 422."""
    phone = await _screen(w, "AgentTicketPhone")
    assert not any(f["name"].startswith("ITSM") for f in phone)
    bad = await w.client.get("/api/v1/reference/dynamic-fields", params={"screen": "Nope"})
    assert bad.status_code == 422
