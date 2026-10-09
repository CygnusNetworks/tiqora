"""Checkbox SysConfig reads in process code go through ``SysConfig.get_bool`` (Perl truth).

Znuny reads ``CustomerGroupSupport`` / ``Ticket::Service::KeepChildren`` in Perl boolean
context (``CustomerGroup.pm:266``, ``Service.pm:188``): any value but undef/""/"0" is on.
A string allow-list (``"1"``/``"True"``/``"true"``) read e.g. ``2`` as off.
"""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.domain.ticket_acl import customer_group_ids
from tiqora.process.dialog_form import customer_service_map
from tiqora.znuny.sysconfig import SysConfig

VALID, INVALID = 1, 2


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> list[Any]:
        return [r[0] if isinstance(r, tuple) else r for r in self._rows]


class _Session:
    """Answers ``execute`` calls in order with canned rows."""

    def __init__(self, *results: list[Any]) -> None:
        self._results = list(results)

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(self._results.pop(0))


def _sysconfig(values: dict[str, Any]) -> SysConfig:
    async def fetch(name: str) -> Any:
        return values.get(name)

    return SysConfig(fetch=fetch)


@pytest.mark.parametrize(
    ("raw", "support_on"),
    [("0", False), ("", False), (None, False), ("1", True), ("2", True), ("--- '1'\n", True)],
)
async def test_customer_group_support_is_perl_truthy(raw: str | None, support_on: bool) -> None:
    groups = [(10, "users"), (11, "support")]
    # valid groups, then (support on only) the customer's group_customer_user rows
    session = _Session(groups, [])
    sysconfig = _sysconfig({"CustomerGroupSupport": raw, "CustomerGroupAlwaysGroups": None})
    ids = await customer_group_ids(session, "cust", "ro", sysconfig=sysconfig)  # type: ignore[arg-type]
    # off: every valid group; on: no rows, only the AlwaysGroups default ``users``
    assert ids == ({10} if support_on else {10, 11})


@pytest.mark.parametrize(("raw", "kept"), [("0", False), ("1", True), ("2", True)])
async def test_service_keep_children_is_perl_truthy(raw: str, kept: bool) -> None:
    services = [(1, "Par", INVALID), (2, "Par::Kid", VALID), (3, "Other", VALID)]
    session = _Session(services, [(2,), (3,)])
    sysconfig = _sysconfig({"Ticket::Service::KeepChildren": raw})
    out = await customer_service_map(session, "cust", sysconfig)  # type: ignore[arg-type]
    assert out == ({2: "Par::Kid", 3: "Other"} if kept else {3: "Other"})
