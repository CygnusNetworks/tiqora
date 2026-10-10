"""Counter provider: parity with the home-page summary, viewer scope, samples (both dialects).

World of ``_dashboard_helpers`` (band 49710-49749): A sees QUEUE_1, B sees QUEUE_2, C has no
queue at all. ``a_open`` carries the AI handoff flag, ``a_escalated`` a breached SLA.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests._dashboard_helpers import GROUP_2, USER_A, USER_B, USER_C, Env, open_env
from tiqora.dashboard.layout import Rect
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers
from tiqora.dashboard.providers.presets import TICKET_PRESETS, preset_kwargs
from tiqora.dashboard.runner import run_batch, run_widget
from tiqora.dashboard.schemas import WidgetIn, WidgetLayouts
from tiqora.domain.ticket_service import TicketService
from tiqora.extensions import get_registry
from tiqora.extensions.registry import Registry

pytestmark = pytest.mark.db

#: preset -> key of ``count_dashboard_summary``
SUMMARY_KEY = {
    "mine_open": "my_open",
    "mine_new": "my_new",
    "unowned_new": "unowned_new",
    "escalated": "escalated",
    "ai_escalated": "ai_escalated",
}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1


def counter(preset: str, wid: str = "c") -> W:
    return W(wid, "core.counter", settings={"source": {"kind": "preset", "preset": preset}})


def registry() -> Registry:
    reg = Registry()
    for p in core_providers():
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


async def summary_of(env: Env, user_id: int) -> dict[str, int]:
    async with env.session() as s:
        return await TicketService(s).count_dashboard_summary(user_id)


def test_presets_match_the_design_table() -> None:
    assert set(TICKET_PRESETS) == set(SUMMARY_KEY)
    assert preset_kwargs("mine_open", 7) == {"state_type": "open", "owner_id": 7}
    assert preset_kwargs("mine_new", 7) == {"state_type": "new", "owner_id": 7}
    assert preset_kwargs("unowned_new", 7) == {"state_type": "new", "owner_id": 1}
    assert preset_kwargs("escalated", 7) == {"state_type": "open", "escalating_within": 0}
    assert preset_kwargs("ai_escalated", 7) == {"state_type": "open", "ai_escalated": True}
    with pytest.raises(KeyError):
        preset_kwargs("nope", 7)


def test_counter_is_registered_in_the_core_registry() -> None:
    p = get_registry().get(DASHBOARD_PROVIDERS, "core.counter")
    assert p is not None and p.version == 1 and p.timeout_seconds == 5.0


@pytest.mark.parametrize("preset", list(SUMMARY_KEY))
async def test_counter_equals_dashboard_summary(env: Env, preset: str) -> None:
    summary = await summary_of(env, USER_A)
    out = await run_widget(env.factory, registry(), USER_A, counter(preset))
    assert out is not None and out.status == "ok" and out.data is not None
    assert out.data["value"] == summary[SUMMARY_KEY[preset]]  # parity with today's numbers
    # and with the ticket list the tile stands for
    async with env.session() as s:
        page = await TicketService(s).list_tickets(
            USER_A, limit=1, offset=0, **preset_kwargs(preset, USER_A)
        )
    assert out.data["value"] == page.total


async def test_world_has_something_to_count_for_every_preset(env: Env) -> None:
    """Guards the parity test against comparing 0 with 0."""
    summary = await summary_of(env, USER_A)
    assert all(summary[k] >= 1 for k in SUMMARY_KEY.values()), summary
    assert summary["escalated"] == 1 and summary["ai_escalated"] == 1


async def test_counter_for_a_user_without_queues_is_zero(env: Env) -> None:
    out = await run_widget(env.factory, registry(), USER_C, counter("mine_open"))
    assert out is not None and out.status == "ok"
    assert out.data == {"value": 0, "delta": None, "spark": [0]}


async def test_counter_writes_one_sample_per_counter_and_day(env: Env) -> None:
    reg = registry()
    await run_batch(env.factory, reg, USER_B, [counter("mine_open", "c1"), counter("mine_new")])
    out = await run_batch(env.factory, reg, USER_B, [counter("mine_open", "c1")])
    assert out["c1"].data is not None and out["c1"].data["spark"] == [1]
    rows = await env.sample_rows(USER_B)
    assert [(k, v) for k, _, v in rows] == [("preset:mine_new", 0), ("preset:mine_open", 1)]
    assert {d for _, d, _ in rows} == {env.now.date().isoformat()}


async def test_previews_never_write_samples(env: Env) -> None:
    reg = registry()
    before = await env.sample_keys(USER_B)
    for _ in range(2):
        out = await run_widget(env.factory, reg, USER_B, counter("unowned_new"), preview=True)
        assert out is not None and out.status == "ok"
        assert out.data == {"value": 0, "delta": None, "spark": [0]}
    assert await env.sample_keys(USER_B) == before == set()


async def test_batch_data_uses_viewer_not_owner(env: Env) -> None:
    """A shares a dashboard with a ``mine_open`` counter; each viewer gets their own number."""
    widget = WidgetIn(
        id="cccccccc",
        type_key="core.counter",
        settings={"source": {"kind": "preset", "preset": "mine_open"}},
        layout=WidgetLayouts(lg=Rect(x=0, y=0, w=3, h=2)),
    )
    shared = await env.make_personal(
        USER_A, name="dash497 geteilt", widgets=[widget], share_group_ids=[GROUP_2]
    )
    a_summary, b_summary = await summary_of(env, USER_A), await summary_of(env, USER_B)
    r_b = await env.as_user(USER_B).get(f"/api/v1/dashboards/{shared.id}/data")
    assert r_b.status_code == 200
    assert r_b.json()["widgets"]["cccccccc"]["data"]["value"] == b_summary["my_open"] == 1
    r_a = await env.as_user(USER_A).get(f"/api/v1/dashboards/{shared.id}/data")
    assert r_a.json()["widgets"]["cccccccc"]["data"]["value"] == a_summary["my_open"]
    assert a_summary["my_open"] != b_summary["my_open"]
    # each viewer's history is their own
    assert await env.sample_keys(USER_B) == {"preset:mine_open"}
    assert await env.sample_keys(USER_A) == {"preset:mine_open"}
