"""``core.report``: any visible report as a dashboard tile, run as the viewer (both dialects).

Seed band 61320-61379 (names contain ``dash3``): tickets 61330-61332 with history / article ids
61340-61342. The world of ``_dashboard3_helpers`` supplies A (``ro`` Q1), B (``ro`` Q2) and
ADMIN. Tickets: ``recent_q1`` (3 days old) and ``old_q1`` (20 days old) in Q1, ``recent_q2``
(3 days old) in Q2, all created through Email. Saved reports are named ``dash3 ...``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any, NamedTuple

import pytest
from sqlalchemy import create_engine, text

import tiqora.reporting.jobs as report_jobs
import tiqora.reporting.run_service as run_service
from tests._dashboard3_helpers import ADMIN, GROUP_1, GROUP_2, Q1, Q2, A, B, Env3, open_env3
from tests._dashboard_helpers import run_widget_fn
from tiqora.config import Settings
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.providers import (
    DASHBOARD_PROVIDERS,
    DashboardProvider,
    ProviderContext,
    core_providers,
)
from tiqora.dashboard.providers.report import (
    SLA_GAUGE_DEFINITION,
    drillable_dimensions,
    effective_time_range,
    gauge_figures,
    report_provider,
    run_report_widget,
)
from tiqora.dashboard.settings_models import ReportWidgetSettings
from tiqora.extensions import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.runner import JobRunner
from tiqora.jobs.service import utc_now
from tiqora.reporting.definition import ReportDefinition, validate_definition
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.rights import ReportRights
from tiqora.reporting.saved import SavedReportIn, create_saved_report, delete_saved_report
from tiqora.reporting.settings import REPORTING_SECTION
from tiqora.reporting.state import load_state
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 61330, 61349
T_RECENT_Q1, T_OLD_Q1, T_RECENT_Q2 = 61330, 61331, 61332
TICKETS = (T_RECENT_Q1, T_OLD_Q1, T_RECENT_Q2)
SETTINGS = Settings(environment="test")
NOT_SHARED = "Diesen Bericht gibt es nicht oder er ist für Sie nicht freigegeben."
CHANNEL_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "channel"}],
    "measures": [{"key": "tickets"}],
    "time_range": {"kind": "relative", "unit": "day", "count": 30, "include_current": True},
}
DAY_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "created", "grain": "day"}],
    "measures": [{"key": "tickets"}],
    "time_range": {"kind": "relative", "unit": "day", "count": 30, "include_current": True},
}
_BAND = {"lo": LO, "hi": HI}
_WIPE = (
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export'))",
    "DELETE FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export')",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE name LIKE 'dash3%')",
    "DELETE FROM tiqora_report WHERE name LIKE 'dash3%'",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)


class MemoryCache:
    """In-memory stand-in for the Redis result cache (``get`` / ``set(ex=)``)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


class W:
    def __init__(
        self, wid: str, type_key: str, settings: dict[str, Any], *, pinned: bool = False
    ) -> None:
        self.id = wid
        self.type_key = type_key
        self.type_version = 1
        self.settings = settings
        self.pinned = pinned


def registry() -> Registry:
    reg = Registry()
    for p in core_providers():
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


def _sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
    engine.dispose()


def _seed(sync_url: str) -> None:
    _sql(sync_url, _WIPE)
    noon = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
    plan = (
        (T_RECENT_Q1, Q1, noon - timedelta(days=3)),
        (T_OLD_Q1, Q1, noon - timedelta(days=20)),
        (T_RECENT_Q2, Q2, noon - timedelta(days=3)),
    )
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        h_new = int(
            conn.execute(
                text("SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
            ).scalar_one()
        )
        sender = int(
            conn.execute(
                text("SELECT id FROM article_sender_type WHERE name = 'customer'")
            ).scalar_one()
        )
        chan = int(
            conn.execute(
                text("SELECT id FROM communication_channel WHERE name = 'Email'")
            ).scalar_one()
        )
        state = int(
            conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        )
        for n, (tid, queue, at) in enumerate(plan):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, :s, NULL, NULL,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20261011{tid}", "title": f"dash3 {tid}", "q": queue,
                 "s": state, "t": at},
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, 1, 3, :s, :t, 1, :t, 1)"
                ),
                {"id": LO + 10 + n, "n": f"%%20261011{tid}%%dash3-queue-{queue}%%3 normal%%new",
                 "ht": h_new, "tid": tid, "q": queue, "s": state, "t": at},
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, 1, :t, 1)"
                ),
                {"id": LO + 10 + n, "tid": tid, "s": sender, "c": chan, "t": at},
            )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        _seed(e.sync_url)
        async with e.factory() as s:
            await rebuild_ticket_facts(s, TICKETS, now=utc_now())
            await s.commit()
        try:
            yield e
        finally:
            _sql(e.sync_url, _WIPE)


# --- helpers -------------------------------------------------------------------------------


async def run_tile(
    env: Env3, uid: int, settings: dict[str, Any], *, pinned: bool = False, **kw: Any
) -> Any:
    kw.setdefault("app_settings", SETTINGS)
    out = await run_widget_fn(
        env.factory, registry(), uid, W("dash3wid", "core.report", settings, pinned=pinned), **kw
    )
    assert out is not None
    return out


async def run_ok(env: Env3, uid: int, settings: dict[str, Any], **kw: Any) -> dict[str, Any]:
    out = await run_tile(env, uid, settings, **kw)
    assert out.status == "ok", out
    assert out.data is not None
    return out.data


def channel_total(data: dict[str, Any]) -> int:
    return sum(int(r[-1]) for r in data["result"]["rows"])


async def save_report(
    env: Env3,
    name: str,
    definition: dict[str, Any],
    *,
    owner: int = A,
    visibility: str = "private",
    groups: tuple[int, ...] = (),
) -> int:
    rights = ReportRights(view=True, create=True, share=True, admin=False)
    body = SavedReportIn(
        name=name,
        definition=ReportDefinition.model_validate(definition),
        visibility=visibility,  # type: ignore[arg-type]
        group_ids=list(groups),
    )
    saved = await env.run(create_saved_report, owner, rights, body, registry=get_registry())
    return int(saved.id)


async def set_access(env: Env3, access: str) -> None:
    await env.run(
        save_section_values,
        REPORTING_SECTION,
        {"access": access},
        secret_key=SETTINGS.secret_key,
    )


# --- pure ----------------------------------------------------------------------------------


def test_drillable_dimensions_are_ticket_sources_only() -> None:
    tickets = ReportDefinition.model_validate(
        {**CHANNEL_DEF, "dimensions": [{"key": "channel"}, {"key": "queue"}]}
    )
    assert drillable_dimensions(tickets) == ["channel", "queue"]
    events = ReportDefinition.model_validate(
        {
            "source": "ticket_events",
            "dimensions": [{"key": "channel"}],
            "measures": [{"key": "events"}],
        }
    )
    assert drillable_dimensions(events) == []
    assert drillable_dimensions(None) == []


def test_effective_time_range_dashboard_wins_over_the_widget() -> None:
    def ctx(filters: dict[str, Any]) -> ProviderContext:
        return ProviderContext(user_id=1, session=None, now=utc_now(), filters=filters)  # type: ignore[arg-type]

    assert effective_time_range(ctx({"time_range": "7d"}), "30d").count == 7  # type: ignore[union-attr]
    assert effective_time_range(ctx({"time_range": None}), "30d").count == 30  # type: ignore[union-attr]
    assert effective_time_range(ctx({}), None) is None


# --- core.report ---------------------------------------------------------------------------


async def test_builtin_report_widget_returns_result_and_definition(env: Env3) -> None:
    data = await run_ok(env, A, {"report_key": "builtin.channel_mix"})
    assert data["report"] == {
        "key": "builtin.channel_mix",
        "kind": "builtin",
        "title_key": "reports.builtin.channel_mix.title",
    }
    assert data["deferred"] is False and data["display"] == "auto"
    assert data["definition"]["source"] == "tickets"
    assert data["definition"]["visualization"]["type"] == "donut"
    assert data["result"]["rows"] == [["email", 2]]  # Q2's ticket is not A's
    assert data["ignored_filters"] == []
    assert data["drillable_dimensions"] == ["channel"]


async def test_report_widget_runs_as_viewer(env: Env3) -> None:
    rid = await save_report(
        env, "dash3 shared", CHANNEL_DEF, visibility="groups", groups=(GROUP_1,)
    )
    # B is not in GROUP_1, A (owner and member) is
    forbidden = await run_tile(env, B, {"report_key": str(rid)})
    assert forbidden.status == "forbidden" and forbidden.error == NOT_SHARED
    data = await run_ok(env, A, {"report_key": str(rid)})
    assert data["report"] == {"key": str(rid), "kind": "saved", "name": "dash3 shared"}
    assert channel_total(data) == 2
    # the scope is the viewer's: B sees their own queue through a report A owns
    await env.run(_share_with_everyone, rid)
    other = await run_ok(env, B, {"report_key": str(rid)})
    assert channel_total(other) == 1


async def _share_with_everyone(session: Any, rid: int) -> None:
    await session.execute(
        text("UPDATE tiqora_report SET visibility = 'all' WHERE id = :i"), {"i": rid}
    )


async def test_report_widget_404_410_403_become_tiles(env: Env3) -> None:
    unknown = await run_tile(env, A, {"report_key": "999999"})
    assert unknown.status == "forbidden" and unknown.error == NOT_SHARED
    unknown_builtin = await run_tile(env, A, {"report_key": "builtin.no_such_report"})
    assert unknown_builtin.status == "forbidden" and unknown_builtin.error == NOT_SHARED

    rid = await save_report(env, "dash3 gone", CHANNEL_DEF)
    await env.run(
        delete_saved_report, rid, A, ReportRights(view=True, create=True, share=False, admin=False)
    )
    gone = await run_tile(env, A, {"report_key": str(rid)})
    assert gone.status == "error" and gone.error == "Der Bericht „dash3 gone“ wurde gelöscht."

    await set_access(env, "stats_group")
    denied = await run_tile(env, A, {"report_key": "builtin.channel_mix"})
    assert denied.status == "forbidden"
    assert denied.error == "Für Berichte fehlt Ihnen die Berechtigung."
    admin = await run_ok(env, ADMIN, {"report_key": "builtin.channel_mix"})
    assert admin["deferred"] is False  # the Znuny admin passes the stats-group rule


async def test_report_widget_bad_parameter_is_an_error_tile_with_the_reason(env: Env3) -> None:
    out = await run_tile(env, A, {"report_key": "builtin.channel_mix", "parameters": {"nope": 1}})
    assert out.status == "error"
    assert out.error.startswith("Der Bericht kann nicht ausgeführt werden: ")


async def test_report_widget_deferred_never_enqueues(
    env: Env3, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def huge(*args: Any, **kwargs: Any) -> int:
        return 10**9

    async def no_enqueue(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a provider must not enqueue (it would commit)")

    monkeypatch.setattr(run_service, "estimate_rows", huge)
    monkeypatch.setattr(run_service, "enqueue_report_ref", no_enqueue)
    data = await run_ok(env, A, {"report_key": "builtin.channel_mix"})
    assert data["deferred"] is True and data["reason"] == "estimate"
    assert "result" not in data and "kiosk" not in data
    assert data["definition"]["source"] == "tickets" and data["row_cap"] == 10
    jobs = env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")
    assert jobs[0][0] == 0


async def test_report_widget_kiosk_flag_never_defers_to_job(
    env: Env3, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def huge(*args: Any, **kwargs: Any) -> int:
        return 10**9

    monkeypatch.setattr(run_service, "estimate_rows", huge)
    data = await run_ok(env, A, {"report_key": "builtin.channel_mix"}, kiosk=True)
    assert data["deferred"] is True and data["kiosk"] is True
    assert "definition" not in data and "row_cap" not in data, "a wallboard renders no job"
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 0


async def test_report_widget_common_filter_only_narrows(env: Env3) -> None:
    def tile(uid: int, *queues: int) -> Any:
        return run_ok(env, uid, {"report_key": "builtin.channel_mix"}, filters=_queues(*queues))

    assert channel_total(await tile(A)) == 2
    assert channel_total(await tile(A, Q1)) == 2  # the Q1 slice
    assert channel_total(await tile(A, Q1, Q2)) == 2
    # a queue A cannot read yields zero rows, never B's ticket
    assert channel_total(await tile(A, Q2)) == 0
    assert channel_total(await tile(B, Q1)) == 0
    assert channel_total(await tile(B, Q2)) == 1
    pinned = await run_ok(
        env, A, {"report_key": "builtin.channel_mix"}, pinned=True, filters=_queues(Q2)
    )
    assert channel_total(pinned) == 2  # a pinned widget ignores the dashboard filter


def _queues(*ids: int) -> DashboardFilters:
    return DashboardFilters(queue_ids=list(ids))


async def test_dashboard_time_range_overrides_report_range_unless_pinned(env: Env3) -> None:
    own = {"report_key": "builtin.channel_mix", "time_range": "30d"}
    assert channel_total(await run_ok(env, A, own)) == 2
    narrow = DashboardFilters(time_range="7d")
    assert channel_total(await run_ok(env, A, own, filters=narrow)) == 1  # dashboard wins
    assert channel_total(await run_ok(env, A, own, filters=narrow, pinned=True)) == 2
    # no widget range, no dashboard range: the report's own 30 days
    assert channel_total(await run_ok(env, A, {"report_key": "builtin.channel_mix"})) == 2
    seven = {"report_key": "builtin.channel_mix", "time_range": "7d"}
    assert channel_total(await run_ok(env, A, seven)) == 1


async def test_rows_capped_and_truncated_flag(env: Env3) -> None:
    rid = await save_report(env, "dash3 by day", DAY_DEF)
    full = await run_ok(env, A, {"report_key": str(rid), "max_rows": 50})
    # the report window is 30 days, empty days included
    assert len(full["result"]["rows"]) == 30 and full["result"]["truncated"] is False
    capped = await run_ok(env, A, {"report_key": str(rid), "max_rows": 5})
    assert len(capped["result"]["rows"]) == 5 and capped["result"]["truncated"] is True
    assert capped["result"]["totals"] == full["result"]["totals"]  # totals are not cut
    wide = {**DAY_DEF, "visualization": {"type": "heatmap"}}
    rid2 = await save_report(env, "dash3 wide", wide)
    uncapped = await run_ok(env, A, {"report_key": str(rid2), "max_rows": 5})
    assert len(uncapped["result"]["rows"]) == 30  # pivot / heatmap results keep every cell


async def test_second_run_is_served_from_the_cache(env: Env3) -> None:
    async with env.factory() as s:  # results are only cached once the report data is ready
        (await load_state(s)).backfill_status = "done"
        await s.commit()
    cache = MemoryCache()
    first = await run_ok(env, A, {"report_key": "builtin.channel_mix"}, report_cache_backend=cache)
    second = await run_ok(env, A, {"report_key": "builtin.channel_mix"}, report_cache_backend=cache)
    assert first["result"]["cached"] is False and second["result"]["cached"] is True
    assert second["result"]["rows"] == first["result"]["rows"]


async def test_inline_definition_runs_without_a_report_key(env: Env3) -> None:
    async with env.factory() as session:
        ctx = ProviderContext(user_id=A, session=session, now=utc_now(), app_settings=SETTINGS)
        data = await run_report_widget(
            ctx,
            report_key=None,
            definition=ReportDefinition.model_validate(CHANNEL_DEF),
            time_range=None,
            parameters={},
            dimension_choices={},
            max_rows=10,
            display="table",
        )
        await session.rollback()
    assert data["report"] == {"key": None, "kind": "inline", "name": None}
    assert data["display"] == "table" and channel_total(data) == 2


# --- save validation -----------------------------------------------------------------------


def _put_body(d: Any, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": d.name, "icon": None, "version": d.version, "widgets": widgets}


def _report_widget(settings: dict[str, Any], n: int = 1) -> dict[str, Any]:
    return {
        "id": f"dash3wid{n:05d}",
        "type_key": "core.report",
        "type_version": 1,
        "title_override": None,
        "settings": settings,
        "layout": {"lg": {"x": 0, "y": 0, "w": 6, "h": 5}},
        "pinned": False,
    }


async def test_save_rejects_invisible_report_for_saver(env: Env3) -> None:
    rid = await save_report(env, "dash3 private", CHANNEL_DEF)  # A's private report
    d = await env.create_personal(owner=B)
    body = _put_body(d, [_report_widget({"report_key": str(rid)})])
    r = await env.as_user(B).put(f"/api/v1/dashboards/{d.id}", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"message": NOT_SHARED, "path": "widgets[0].settings.report_key"}
    ok = await env.as_user(A).put(
        f"/api/v1/dashboards/{(da := await env.create_personal(owner=A)).id}",
        json=_put_body(da, [_report_widget({"report_key": str(rid)}, 2)]),
    )
    assert ok.status_code == 200, ok.text
    builtin = await env.as_user(B).put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(d, [_report_widget({"report_key": "builtin.channel_mix"})]),
    )
    assert builtin.status_code == 200, builtin.text


async def test_save_rejects_deleted_report_and_too_many_parameters(env: Env3) -> None:
    rid = await save_report(env, "dash3 doomed", CHANNEL_DEF)
    await env.run(
        delete_saved_report, rid, A, ReportRights(view=True, create=True, share=False, admin=False)
    )
    d = await env.create_personal(owner=A)
    gone = await env.as_user(A).put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(d, [_report_widget({"report_key": str(rid)})]),
    )
    assert gone.status_code == 422
    assert gone.json()["detail"] == {
        "message": "Der Bericht „dash3 doomed“ wurde gelöscht.",
        "path": "widgets[0].settings.report_key",
    }
    params = {f"p{i}": 1 for i in range(11)}
    many = await env.as_user(A).put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(
            d, [_report_widget({"report_key": "builtin.channel_mix", "parameters": params})]
        ),
    )
    assert many.status_code == 422
    assert many.json()["detail"]["path"] == "widgets[0].settings.parameters"


# --- statement limits ----------------------------------------------------------------------


async def test_report_provider_leaves_the_session_statement_limit_alone(env: Env3) -> None:
    """MariaDB: the engine's per-statement reset must not leave a changed limit behind."""
    if not env.sync_url.startswith("mysql"):
        pytest.skip("max_statement_time is a MariaDB session variable")

    async def probe(ctx: ProviderContext, settings: ReportWidgetSettings) -> dict[str, Any]:
        sql = text("SELECT @@max_statement_time")
        before = float((await ctx.session.execute(sql)).scalar_one())
        await run_report_widget(
            ctx,
            report_key=settings.report_key,
            definition=None,
            time_range=None,
            parameters={},
            dimension_choices={},
            max_rows=5,
            display="auto",
        )
        after = float((await ctx.session.execute(sql)).scalar_one())
        return {"before": before, "after": after}

    reg = Registry()
    reg.add(
        DASHBOARD_PROVIDERS,
        DashboardProvider(
            key="core.report",
            version=1,
            settings_model=ReportWidgetSettings,
            run=probe,
            timeout_seconds=8.0,
            self_limited=True,
        ),
    )
    reg.freeze()
    out = await run_widget_fn(
        env.factory,
        reg,
        A,
        W("dash3wid", "core.report", {"report_key": "builtin.channel_mix"}),
        app_settings=SETTINGS,
    )
    assert out is not None and out.status == "ok", out
    assert out.data == {"before": 0.0, "after": 0.0}


# --- SLA gauge, backlog aging, heatmap -----------------------------------------------------


def test_sla_gauge_definition_validates() -> None:
    validate_definition(SLA_GAUGE_DEFINITION, get_registry())
    assert [m.key for m in SLA_GAUGE_DEFINITION.measures] == ["sla_met_rate", "tickets"]
    assert SLA_GAUGE_DEFINITION.dimensions == []


def test_all_report_providers_limit_themselves() -> None:
    assert report_provider().self_limited
    by_key = {p.key: p for p in core_providers()}
    for key in ("core.report", "core.sla-gauge", "core.backlog-aging", "core.heatmap"):
        assert by_key[key].self_limited and by_key[key].timeout_seconds == 8.0


def test_gauge_figures_normalise_ratio_to_percent() -> None:
    def data(rate: Any, tickets: int) -> dict[str, Any]:
        cols = [{"key": "sla_met_rate"}, {"key": "tickets"}]
        return {"result": {"columns": cols, "rows": [[rate, tickets]]}}

    assert gauge_figures(data(0.8764, 120)) == (87.6, 120)
    assert gauge_figures(data(None, 4)) == (None, 4)
    assert gauge_figures({"result": {"columns": [], "rows": []}}) == (None, 0)
    assert gauge_figures({"deferred": True}) == (None, 0)


async def run_type(
    env: Env3, uid: int, type_key: str, settings: dict[str, Any], **kw: Any
) -> dict[str, Any]:
    kw.setdefault("app_settings", SETTINGS)
    pinned = bool(kw.pop("pinned", False))
    out = await run_widget_fn(
        env.factory, registry(), uid, W("dash3wid", type_key, settings, pinned=pinned), **kw
    )
    assert out is not None and out.status == "ok", out
    assert out.data is not None
    return out.data


def _set_sla(env: Env3, breached: dict[int, bool]) -> None:
    for tid, hit in breached.items():
        env.sql(
            "UPDATE tiqora_report_ticket_fact SET solution_target_minutes = 60,"
            " solution_breached = :b WHERE ticket_id = :t",
            b=hit,
            t=tid,
        )


async def test_sla_gauge_rate_in_percent_and_target(env: Env3) -> None:
    empty = await run_type(env, A, "core.sla-gauge", {})
    # no ticket has an SLA target yet: no rate, but the tickets of the window are counted
    assert empty["rate"] is None and empty["tickets"] == 2 and empty["target"] == 90
    assert empty["deferred"] is False and empty["report"]["kind"] == "inline"

    _set_sla(env, {T_RECENT_Q1: True, T_OLD_Q1: False, T_RECENT_Q2: False})
    data = await run_type(env, A, "core.sla-gauge", {"target_percent": 75})
    # A sees the two Q1 tickets, one of them breached: 50 %, not 0.5
    assert data["rate"] == 50.0 and data["target"] == 75
    assert data["definition"]["source"] == "tickets"
    assert data["result"]["columns"][0]["key"] == "sla_met_rate"
    # B sees only the Q2 ticket, which met its target
    assert (await run_type(env, B, "core.sla-gauge", {}))["rate"] == 100.0
    # the 7-day dashboard range keeps only the recent Q1 ticket, which breached
    narrow = DashboardFilters(time_range="7d")
    week = await run_type(env, A, "core.sla-gauge", {}, filters=narrow)
    assert week["rate"] == 0.0
    # a pinned gauge keeps its own 30 days; a queue filter narrows
    pinned = await run_type(env, A, "core.sla-gauge", {}, filters=narrow, pinned=True)
    assert pinned["rate"] == 50.0
    q2 = await run_type(env, A, "core.sla-gauge", {}, filters=_queues(Q2))
    assert q2["rate"] is None and q2["tickets"] == 0  # A cannot read Q2
    assert data["tickets"] == 2


async def test_backlog_aging_rows_capped(env: Env3) -> None:
    data = await run_type(env, A, "core.backlog-aging", {})
    assert data["report"]["key"] == "builtin.backlog_aging"
    assert data["definition"]["source"] == "open_tickets"
    keys = [c["key"] for c in data["result"]["columns"]]
    assert keys[0] == "queue" and keys[1:] == [
        "age_0_1d", "age_1_3d", "age_3_7d", "age_7_30d", "age_30d_plus",
    ]  # fmt: skip
    rows = data["result"]["rows"]
    assert len(rows) == 1 and sum(rows[0][1:]) == 2  # A reads Q1: two open tickets
    assert rows[0][keys.index("age_7_30d")] == 1  # the 20-day-old one
    # the dashboard time range does not apply to a snapshot of what is open now
    week = await run_type(
        env, A, "core.backlog-aging", {}, filters=DashboardFilters(time_range="7d")
    )
    assert week["result"]["rows"] == rows
    # with read access to both queues, max_rows 1 cuts the list and says so
    env.sql(
        "INSERT INTO group_user (user_id, group_id, permission_key,"
        " create_time, create_by, change_time, change_by)"
        " VALUES (:u, :g, 'ro', :t, 1, :t, 1)",
        u=A,
        g=GROUP_2,
        t=datetime(2026, 10, 1),
    )
    both = await run_type(env, A, "core.backlog-aging", {"max_rows": 8})
    assert len(both["result"]["rows"]) == 2 and both["result"]["truncated"] is False
    one = await run_type(env, A, "core.backlog-aging", {"max_rows": 1})
    assert len(one["result"]["rows"]) == 1 and one["result"]["truncated"] is True
    assert one["result"]["totals"] == both["result"]["totals"]


def heatmap_total(data: dict[str, Any]) -> int:
    return sum(int(r[-1]) for r in data["result"]["rows"])


async def test_heatmap_time_range_setting(env: Env3) -> None:
    data = await run_type(env, A, "core.heatmap", {})
    assert data["report"]["key"] == "builtin.created_heatmap"
    assert data["definition"]["visualization"]["type"] == "heatmap"
    assert [c["key"] for c in data["result"]["columns"]] == [
        "created_weekday", "created_hour", "tickets",
    ]  # fmt: skip
    assert (
        heatmap_total(data) == 2
        and heatmap_total(await run_type(env, A, "core.heatmap", {"time_range": "30d"})) == 2
    )
    # the dashboard range wins over the widget's own, unless the widget is pinned
    narrow = DashboardFilters(time_range="7d")
    assert heatmap_total(await run_type(env, A, "core.heatmap", {}, filters=narrow)) == 1
    pinned = await run_type(
        env, A, "core.heatmap", {"time_range": "30d"}, filters=narrow, pinned=True
    )
    assert heatmap_total(pinned) == 2
    assert heatmap_total(await run_type(env, B, "core.heatmap", {})) == 1


# --- background job ------------------------------------------------------------------------

_boards = iter(range(100, 10_000))


class Board(NamedTuple):
    id: int
    wid: str


async def _board(env: Env3, type_key: str, settings: dict[str, Any], **filters: Any) -> Board:
    d = await env.create_personal(owner=A)
    widget = {**_report_widget(settings, next(_boards)), "type_key": type_key}
    r = await env.as_user(A).put(f"/api/v1/dashboards/{d.id}", json=_put_body(d, [widget]))
    assert r.status_code == 200, r.text
    if filters:
        await env.patch_filters(A, d.id, filters, version=r.json()["version"])
    return Board(d.id, widget["id"])


def _job_url(d: Board) -> str:
    return f"/api/v1/dashboards/{d.id}/widgets/{d.wid}/report-job"


async def _run_jobs(env: Env3) -> None:
    runner = JobRunner(session_factory=env.factory, settings=SETTINGS, registry=get_registry())
    await runner.run_once()
    await runner.wait_idle()


@pytest.mark.parametrize(
    ("type_key", "settings", "filters"),
    [
        ("core.report", {"report_key": "builtin.channel_mix"}, {}),
        (
            "core.report",
            {"report_key": "builtin.channel_mix"},
            {"queue_ids": [Q1], "time_range": "7d"},
        ),
        ("core.sla-gauge", {}, {"queue_ids": [Q1]}),
        ("core.heatmap", {"time_range": "30d"}, {}),
    ],
)
async def test_report_job_result_is_served_from_cache(
    env: Env3,
    monkeypatch: pytest.MonkeyPatch,
    type_key: str,
    settings: dict[str, Any],
    filters: dict[str, Any],
) -> None:
    async with env.factory() as s:  # results are only cached once the report data is ready
        (await load_state(s)).backfill_status = "done"
        await s.commit()
    cache = MemoryCache()
    monkeypatch.setattr(report_jobs, "cache_backend_from_settings", lambda _s: cache)
    d = await _board(env, type_key, settings, **filters)
    board_filters = DashboardFilters(**filters)

    real_estimate = run_service.estimate_rows

    async def huge(*args: Any, **kwargs: Any) -> int:
        return 10**9

    monkeypatch.setattr(run_service, "estimate_rows", huge)  # a large report: deferred
    deferred = await run_type(
        env, A, type_key, settings, filters=board_filters, report_cache_backend=cache
    )
    assert deferred["deferred"] is True and "result" not in deferred
    # the browser renders the job's own result with this definition (cache off: never served)
    assert deferred["definition"]["source"] and deferred["row_cap"] >= 1

    resp = await env.as_user(A).post(_job_url(d))
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    assert accepted["status"] == "queued" and accepted["job_id"] > 0
    await _run_jobs(env)
    row = env.sql("SELECT status FROM tiqora_job WHERE id = :i", i=accepted["job_id"])
    assert row[0][0] == "succeeded", row

    monkeypatch.setattr(run_service, "estimate_rows", real_estimate)
    served = await run_type(
        env, A, type_key, settings, filters=board_filters, report_cache_backend=cache
    )
    assert served["deferred"] is False and served["result"]["cached"] is True
    assert served["definition"] == deferred["definition"]
    fresh = await run_type(env, A, type_key, settings, filters=board_filters)
    assert fresh["result"]["cached"] is False
    assert served["result"]["rows"] == fresh["result"]["rows"]
    assert served["result"]["totals"] == fresh["result"]["totals"]


async def test_report_job_rejects_non_report_widgets_and_respects_job_cap(env: Env3) -> None:
    d = await _board(env, "core.ai-status", {})
    r = await env.as_user(A).post(_job_url(d))
    assert r.status_code == 422
    assert r.json()["detail"]["message"] == "Dieses Widget zeigt keinen Bericht."
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 0

    missing = await env.as_user(A).post(f"/api/v1/dashboards/{d.id}/widgets/nope/report-job")
    assert missing.status_code == 404

    rep = await _board(env, "core.report", {"report_key": "builtin.channel_mix"})
    for n in range(3):
        resp = await env.as_user(A).post(_job_url(rep))
        assert resp.status_code == 202, (n, resp.text)
    over = await env.as_user(A).post(_job_url(rep))
    assert over.status_code == 429
    assert over.json()["detail"] == {
        "message": "Es laufen bereits mehrere Berichte im Hintergrund."
        " Bitte warten Sie, bis einer fertig ist."
    }
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 3


async def test_report_job_needs_report_access_and_a_valid_report(env: Env3) -> None:
    rep = await _board(env, "core.report", {"report_key": "builtin.channel_mix"})
    await set_access(env, "stats_group")
    denied = await env.as_user(A).post(_job_url(rep))
    assert denied.status_code == 403
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 0
    await set_access(env, "all_agents")
    # a saved report deleted after the widget was saved: 410 from the seam, no job
    rid = await save_report(env, "dash3 job gone", CHANNEL_DEF)
    gone = await _board(env, "core.report", {"report_key": str(rid)})
    await env.run(
        delete_saved_report, rid, A, ReportRights(view=True, create=True, share=False, admin=False)
    )
    r = await env.as_user(A).post(_job_url(gone))
    assert r.status_code == 410, r.text
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 0


async def test_report_job_invisible_dashboard_404(env: Env3) -> None:
    d = await _board(env, "core.report", {"report_key": "builtin.channel_mix"})
    r = await env.as_user(B).post(_job_url(d))  # a personal dashboard of A
    assert r.status_code == 404
    assert env.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.report_run'")[0][0] == 0
