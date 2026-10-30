"""Change service: create / adopt / get / update, DF mirror, events, listeners (both dialects).

Seed band 946300-946399 (users 946301-5, groups 946310-2, queues 946320-1, services 946330-1,
tickets 946340-49). One rolled-back session carries the world (``tests._change_fixtures``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    Q_A,
    Q_B,
    SERVICE,
    SERVICE_B,
    U_CREATE,
    U_FULL,
    U_MANAGER,
    U_NONE,
    U_RO,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
    make_change,
)
from tests._itsm_fake_provider import OPERATIONAL, FakeCiProvider
from tiqora.domain.dynamic_field_values import get_df_values, load_dynamic_field
from tiqora.domain.ticket_write_service import InvalidInput, update_dynamic_field
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.change import service as change_service
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.impact import ITSM_CHANGE_IMPACT_JOB
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeService,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.phases import allowed_targets
from tiqora.itsm.change.service import (
    CHANGE_LISTENERS,
    PHASE_FIELD,
    TYPE_FIELD,
    ChangeCreateIn,
    ChangeEvent,
    ChangeListener,
    ChangeUpdateIn,
    adopt_ticket,
    create_change,
    get_change,
    list_events,
    mirror_dynamic_fields,
    update_change,
)
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T_PLAIN, T_OTHER, T_PROCESS = 946340, 946341, 946342
FUTURE = datetime(2026, 11, 1, 8, 0, 0)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        yield world


def _registry(
    request: pytest.FixtureRequest,
    *,
    provider: FakeCiProvider | None = None,
    listeners: Sequence[ChangeListener] = (),
) -> None:
    registry = Registry()
    registry.add(JOB_HANDLERS, ITSM_CHANGE_IMPACT_JOB)  # create/update queue an impact job
    if provider is not None:
        registry.add(CI_PROVIDERS, CiProviderFactory(mode="tiqora", create=lambda _sc: provider))
    for listener in listeners:
        registry.add(CHANGE_LISTENERS, listener)
    registry.freeze()
    set_registry_for_tests(registry)
    request.addfinalizer(lambda: set_registry_for_tests(None))


async def _create(
    w: ChangeWorld, user_id: int = U_FULL, **fields: object
) -> change_service.ChangeView:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
    )
    view, job = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=user_id,
        session_factory=w.factory,
    )
    assert job is None
    return view


async def _events(w: ChangeWorld, ticket_id: int) -> list[TiqoraItsmChangeEvent]:
    rows = await w.session.execute(
        select(TiqoraItsmChangeEvent)
        .where(TiqoraItsmChangeEvent.ticket_id == ticket_id)
        .order_by(TiqoraItsmChangeEvent.id)
    )
    return list(rows.scalars())


async def _history_count(
    w: ChangeWorld, ticket_id: int, kind: str = "TicketDynamicFieldUpdate"
) -> int:
    return int(
        (
            await w.session.execute(
                text(
                    "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                    " ON ht.id = h.history_type_id WHERE ht.name = :k AND h.ticket_id = :t"
                ),
                {"k": kind, "t": ticket_id},
            )
        ).scalar_one()
    )


async def _plain_ticket(
    w: ChangeWorld, ticket_id: int, *, type_name: str | None, queue_id: int = Q_A
) -> None:
    type_id = 1
    if type_name is not None:
        type_id = int(
            (
                await w.session.execute(
                    text("SELECT id FROM ticket_type WHERE name = :n"), {"n": type_name}
                )
            ).scalar_one()
        )
    state_id = int(
        (
            await w.session.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
        ).scalar_one()
    )
    await w.session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :tn, 'Altes Ticket', :q, 1, :ty, 1, 1, 3, :st, 0, 0, 0, 0,"
            " 0, 0, 0, :now, 1, :now, 1)"
        ),
        {
            "id": ticket_id,
            "tn": f"20261001{ticket_id}",
            "q": queue_id,
            "ty": type_id,
            "st": state_id,
            "now": FUTURE,
        },
    )


async def test_create_change_writes_ticket_record_dfs_event(w: ChangeWorld) -> None:
    view = await _create(w, body="Zeile 1\n<b>fett</b>", reason="Austausch\nam Samstag")
    tid = view.record.ticket_id

    assert (view.ticket.type_name, view.ticket.state) == ("Change", "new")
    assert (view.record.phase, view.record.change_type) == ("draft", "normal")
    assert view.record.reason == "Austausch\nam Samstag"  # plain text stays plain
    assert view.can_write is True and view.can_manage is False and view.drift == ()
    assert view.allowed_targets == allowed_targets(
        "draft",
        "normal",
        emergency_post_approval=False,
        standard_requires_review=False,
        auto_approve_low_risk=False,
    )

    owner = (
        await w.session.execute(text("SELECT user_id FROM ticket WHERE id = :t"), {"t": tid})
    ).scalar_one()
    assert owner == U_FULL
    articles = (
        await w.session.execute(
            text(
                "SELECT a.is_visible_for_customer, m.a_body FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id WHERE a.ticket_id = :t"
            ),
            {"t": tid},
        )
    ).all()
    assert len(articles) == 1 and not articles[0][0]
    assert articles[0][1] == "Zeile 1<br>&lt;b&gt;fett&lt;/b&gt;"  # escaped, never raw HTML

    for name, expected in ((TYPE_FIELD, "normal"), (PHASE_FIELD, "draft")):
        field = await load_dynamic_field(w.session, name)
        assert field is not None
        assert await get_df_values(w.session, field, tid) == [expected]
    assert await _history_count(w, tid) == 2

    events = await _events(w, tid)
    assert [(e.event, e.from_phase, e.to_phase, e.created_by) for e in events] == [
        ("created", None, "draft", U_FULL),
        ("phase", None, "draft", U_FULL),
    ]


async def test_create_links_cis_and_services(
    w: ChangeWorld, request: pytest.FixtureRequest
) -> None:
    provider = FakeCiProvider(source="tiqora")
    ref = provider.add_ci(7, "srv-db-01.example.org", incident=OPERATIONAL)
    _registry(request, provider=provider)

    view = await _create(
        w, service_id=SERVICE, service_ids=[SERVICE_B, SERVICE], ci_refs=[str(ref)]
    )
    tid = view.record.ticket_id
    assert view.service_ids == (SERVICE, SERVICE_B)
    assert view.ticket.service_id == SERVICE
    extra = (
        (
            await w.session.execute(
                select(TiqoraItsmChangeService.service_id).where(
                    TiqoraItsmChangeService.ticket_id == tid
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(extra) == [SERVICE_B]  # the ticket's own service is not stored twice
    assert (tid, 7, "RelevantTo") in provider._links

    with pytest.raises(ChangeError) as unknown_service:
        await _create(w, service_id=946399)
    assert (unknown_service.value.error_code, unknown_service.value.status_code) == (
        "service_not_found",
        422,
    )
    for bad, code in (
        ("znuny:7", "ci_ref_invalid"),
        ("nonsense", "ci_ref_invalid"),
        ("tiqora:99", "ci_not_found"),
    ):
        with pytest.raises(ChangeError) as err:
            await _create(w, ci_refs=[bad])
        assert (err.value.error_code, err.value.status_code) == (code, 422)


async def test_create_without_cmdb_provider_refuses_ci_refs(
    w: ChangeWorld, request: pytest.FixtureRequest
) -> None:
    _registry(request)
    with pytest.raises(ChangeError) as err:
        await _create(w, ci_refs=["tiqora:7"])
    assert (err.value.error_code, err.value.status_code) == ("cmdb_unavailable", 409)


async def test_create_needs_queue_create(w: ChangeWorld) -> None:
    for user in (U_RO, U_NONE):
        with pytest.raises(ChangeError) as err:
            await _create(w, user)
        assert (err.value.error_code, err.value.status_code) == ("forbidden", 403)
    # a queue nobody holds, and no queue at all
    with pytest.raises(ChangeError) as other:
        await _create(w, U_FULL, queue_id=Q_B)
    assert other.value.status_code == 403
    with pytest.raises(ChangeError) as missing:
        await _create(w, U_FULL, queue_id=None)
    assert (missing.value.error_code, missing.value.status_code) == ("queue_required", 422)
    # create + ro is enough (no rw)
    view = await _create(w, U_CREATE)
    assert view.can_write is False and view.allowed_targets == ()
    assert view.record.created_by == U_CREATE


@pytest.mark.parametrize(
    ("mode", "setup", "code", "status"),
    [
        ("off", True, "change_unavailable", 404),
        ("znuny_readthrough", True, "change_mode_a", 409),
        ("tiqora", False, "change_setup_missing", 409),
    ],
)
async def test_create_refused_in_mode_a_and_off(
    request: pytest.FixtureRequest, mode: str, setup: bool, code: str, status: int
) -> None:
    for url_fixture in URL_FIXTURES:
        async with change_world(request, url_fixture, mode=mode, setup=setup) as w:
            with pytest.raises(ChangeError) as err:
                await _create(w)
            assert (err.value.error_code, err.value.status_code) == (code, status)
            with pytest.raises(ChangeError) as read:
                await get_change(w.session, APP_SETTINGS, ticket_id=T_PLAIN, user_id=U_FULL)
            assert read.value.error_code == code


async def test_standard_change_needs_a_standard_template(w: ChangeWorld) -> None:
    with pytest.raises(ValidationError):  # no template at all
        ChangeCreateIn.model_validate({"title": "x", "change_type": "standard", "queue_id": Q_A})
    now = datetime(2026, 10, 1)
    template = TiqoraItsmChangeTemplate(
        name="chg94 normal",
        change_type="normal",
        is_standard=False,
        valid=True,
        defaults="{}",
        tasks="[]",
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    standard = TiqoraItsmChangeTemplate(
        name="chg94 standard",
        change_type="standard",
        is_standard=True,
        valid=True,
        defaults="{}",
        tasks="[]",
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    w.session.add_all([template, standard])
    await w.session.flush()
    with pytest.raises(ChangeError) as plain:  # a template that is not a standard template
        await _create(w, change_type="standard", template_id=template.id)
    assert plain.value.error_code == "standard_needs_template"
    with pytest.raises(ChangeError) as mismatch:
        await _create(w, change_type="normal", template_id=standard.id)
    assert mismatch.value.error_code == "template_type_mismatch"
    with pytest.raises(ChangeError) as gone:
        await _create(w, change_type="standard", template_id=946399)
    assert gone.value.error_code == "template_not_found"
    view = await _create(w, change_type="standard", template_id=standard.id)
    assert (view.record.change_type, view.record.template_id) == ("standard", standard.id)


async def test_adopt_existing_change_ticket(w: ChangeWorld) -> None:
    await _plain_ticket(w, T_PLAIN, type_name="Change")
    with pytest.raises(ChangeError) as ro:
        await adopt_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=T_PLAIN,
            change_type="normal",
            user_id=U_RO,
        )
    assert ro.value.status_code == 403
    with pytest.raises(ChangeError) as none:
        await adopt_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=T_PLAIN,
            change_type="normal",
            user_id=U_NONE,
        )
    assert none.value.status_code == 404

    view = await adopt_ticket(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=T_PLAIN,
        change_type="emergency",
        user_id=U_FULL,
    )
    assert (view.record.phase, view.record.change_type) == ("draft", "emergency")
    assert view.drift == () and view.service_ids == ()
    for name, expected in ((TYPE_FIELD, "emergency"), (PHASE_FIELD, "draft")):
        field = await load_dynamic_field(w.session, name)
        assert field is not None
        assert await get_df_values(w.session, field, T_PLAIN) == [expected]
    assert [(e.event, e.from_phase, e.to_phase) for e in await _events(w, T_PLAIN)] == [
        ("adopted", None, "draft"),
        ("phase", None, "draft"),
    ]


async def test_adopt_refuses_process_ticket_other_type_duplicate(w: ChangeWorld) -> None:
    await _plain_ticket(w, T_OTHER, type_name=None)
    await _plain_ticket(w, T_PROCESS, type_name="Change")
    await update_dynamic_field(
        w.session,
        ticket_id=T_PROCESS,
        field_name="ProcessManagementProcessID",
        values=["Process-chg94"],
        user_id=1,
    )
    await update_dynamic_field(
        w.session,
        ticket_id=T_PROCESS,
        field_name="ProcessManagementActivityID",
        values=["Activity-chg94"],
        user_id=1,
    )

    async def adopt(ticket_id: int) -> ChangeError:
        with pytest.raises(ChangeError) as err:
            await adopt_ticket(
                w.session,
                APP_SETTINGS,
                SysConfig(w.session),
                ticket_id=ticket_id,
                change_type="normal",
                user_id=U_FULL,
            )
        return err.value

    assert (await adopt(T_OTHER)).error_code == "not_a_change_ticket"
    assert (await adopt(T_PROCESS)).error_code == "process_ticket"
    created = await _create(w)
    duplicate = await adopt(created.record.ticket_id)
    assert (duplicate.error_code, duplicate.status_code) == ("change_exists", 409)
    assert (await adopt(946399)).status_code == 404
    # nothing was written for the refused tickets
    rows = await w.session.execute(
        select(func.count())
        .select_from(TiqoraItsmChange)
        .where(TiqoraItsmChange.ticket_id.in_([T_OTHER, T_PROCESS]))
    )
    assert rows.scalar_one() == 0


async def test_adopt_refuses_closed_ticket(w: ChangeWorld) -> None:
    await _plain_ticket(w, T_PLAIN, type_name="Change")
    closed = (
        await w.session.execute(
            text("SELECT id FROM ticket_state WHERE name = 'closed successful'")
        )
    ).scalar_one()
    await w.session.execute(
        text("UPDATE ticket SET ticket_state_id = :s WHERE id = :t"), {"s": closed, "t": T_PLAIN}
    )
    with pytest.raises(ChangeError) as err:
        await adopt_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=T_PLAIN,
            change_type="normal",
            user_id=U_FULL,
        )
    assert err.value.error_code == "ticket_closed"


async def _patch(
    w: ChangeWorld, ticket_id: int, user_id: int = U_FULL, **fields: object
) -> change_service.ChangeView:
    return await update_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=ticket_id,
        data=ChangeUpdateIn.model_validate(fields),
        user_id=user_id,
    )


async def test_patch_updates_fields_not_phase(w: ChangeWorld) -> None:
    created = await _create(w)
    tid = created.record.ticket_id
    with pytest.raises(ValidationError):  # the phase is not a field
        ChangeUpdateIn.model_validate({"phase": "closed"})

    view = await _patch(
        w,
        tid,
        reason="  Neuer Grund ",
        implementation_plan="Plan A",
        backout_plan="Plan B",
        test_plan="Test",
        planned_start=FUTURE,
        planned_end=datetime(2026, 11, 1, 10, 0, 0),
        portal_visible=True,
    )
    rec = view.record
    assert (rec.reason, rec.implementation_plan, rec.backout_plan, rec.test_plan) == (
        "Neuer Grund",
        "Plan A",
        "Plan B",
        "Test",
    )
    assert (rec.planned_start, rec.planned_end) == (FUTURE, datetime(2026, 11, 1, 10, 0, 0))
    assert rec.portal_visible is True and rec.phase == "draft" and rec.updated_by == U_FULL

    events = [e for e in await _events(w, tid) if e.event == "updated"]
    assert len(events) == 1 and events[0].created_by == U_FULL
    assert sorted(json.loads(events[0].detail or "{}")["fields"]) == [
        "backout_plan",
        "implementation_plan",
        "portal_visible",
        "reason",
        "test_plan",
        "window",
    ]

    # blank clears a text, nothing changed writes no event
    assert (await _patch(w, tid, reason="   ")).record.reason is None
    before = len(await _events(w, tid))
    await _patch(w, tid, implementation_plan="Plan A", portal_visible=True)
    assert len(await _events(w, tid)) == before
    assert [e.event for e in await _events(w, tid)].count(
        "phase"
    ) == 1  # PATCH never moves the phase


async def test_patch_window_services_and_cab(w: ChangeWorld) -> None:
    tid = (await _create(w, service_id=SERVICE)).record.ticket_id
    with pytest.raises(ValidationError):  # both given, end before start
        ChangeUpdateIn.model_validate(
            {"planned_start": FUTURE, "planned_end": datetime(2026, 11, 1, 7, 0, 0)}
        )
    with pytest.raises(ValidationError):
        ChangeUpdateIn.model_validate(
            {"planned_start": FUTURE.isoformat(), "planned_end": FUTURE.isoformat()}
        )
    with pytest.raises(ValidationError):
        ChangeUpdateIn.model_validate({"clear_window": True, "planned_start": FUTURE.isoformat()})

    view = await _patch(w, tid, planned_start=FUTURE, planned_end=datetime(2026, 11, 1, 9, 0, 0))
    assert view.record.planned_end == datetime(2026, 11, 1, 9, 0, 0)
    # only the end moves: checked against the stored start
    with pytest.raises(ChangeError) as inverted:
        await _patch(w, tid, planned_end=datetime(2026, 11, 1, 7, 0, 0))
    assert (inverted.value.error_code, inverted.value.status_code) == ("window_invalid", 422)
    cleared = await _patch(w, tid, clear_window=True)
    assert (cleared.record.planned_start, cleared.record.planned_end) == (None, None)

    # extra services are replaced, the ticket's own service stays and is not stored twice
    both = await _patch(w, tid, service_ids=[SERVICE, SERVICE_B])
    assert both.service_ids == (SERVICE, SERVICE_B)
    assert (await _patch(w, tid, service_ids=[])).service_ids == (SERVICE,)
    with pytest.raises(ChangeError) as unknown:
        await _patch(w, tid, service_ids=[946399])
    assert unknown.value.error_code == "service_not_found"

    now = datetime(2026, 10, 1)
    cab = TiqoraItsmCab(
        name="chg94-cab",
        rule="any",
        is_emergency=False,
        valid=True,
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    w.session.add(cab)
    await w.session.flush()
    assert (await _patch(w, tid, cab_id=cab.id)).record.cab_id == cab.id
    with pytest.raises(ChangeError) as no_cab:
        await _patch(w, tid, cab_id=946399)
    assert no_cab.value.error_code == "cab_not_found"
    assert (await _patch(w, tid, cab_id=None)).record.cab_id is None  # explicit null clears
    assert (await _patch(w, tid, reason="x")).record.cab_id is None  # omitted keeps


async def test_patch_needs_rw_and_refuses_final_phase(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    with pytest.raises(ChangeError) as ro:
        await _patch(w, tid, U_RO, reason="x")
    assert ro.value.status_code == 403
    with pytest.raises(ChangeError) as none:
        await _patch(w, tid, U_NONE, reason="x")
    assert none.value.status_code == 404
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = "closed"
    await w.session.flush()
    with pytest.raises(ChangeError) as final:
        await _patch(w, tid, reason="x")
    assert (final.value.error_code, final.value.status_code) == ("change_final", 409)


async def test_change_type_only_in_draft(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    view = await _patch(w, tid, change_type="emergency")
    assert view.record.change_type == "emergency"
    field = await load_dynamic_field(w.session, TYPE_FIELD)
    assert field is not None and await get_df_values(w.session, field, tid) == ["emergency"]
    with pytest.raises(ChangeError) as std:  # standard needs a standard template
        await _patch(w, tid, change_type="standard")
    assert std.value.error_code == "standard_needs_template"

    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = "assessment"
    await w.session.flush()
    with pytest.raises(ChangeError) as locked:
        await _patch(w, tid, change_type="normal")
    assert (locked.value.error_code, locked.value.status_code) == ("change_type_locked", 409)
    # the same type is not a change
    assert (await _patch(w, tid, change_type="emergency")).record.change_type == "emergency"


async def test_get_404_without_ro(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    for user in (U_NONE,):
        with pytest.raises(ChangeError) as err:
            await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=user)
        assert (err.value.error_code, err.value.status_code) == ("ticket_not_found", 404)
    with pytest.raises(ChangeError) as events:
        await list_events(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_NONE)
    assert events.value.status_code == 404
    ro = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_RO)
    assert (ro.can_write, ro.can_manage, ro.allowed_targets) == (False, False, ())
    # readable ticket without a change record
    await _plain_ticket(w, T_PLAIN, type_name="Change")
    with pytest.raises(ChangeError) as plain:
        await get_change(w.session, APP_SETTINGS, ticket_id=T_PLAIN, user_id=U_FULL)
    assert (plain.value.error_code, plain.value.status_code) == ("change_not_found", 404)
    # a ticket in a queue the caller cannot read looks like a missing one
    await _plain_ticket(w, T_OTHER, type_name="Change", queue_id=Q_B)
    with pytest.raises(ChangeError) as hidden:
        await get_change(w.session, APP_SETTINGS, ticket_id=T_OTHER, user_id=U_FULL)
    assert hidden.value.error_code == "ticket_not_found"


async def test_dirty_df_shows_drift(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    await update_dynamic_field(
        w.session, ticket_id=tid, field_name=PHASE_FIELD, values=["closed"], user_id=1
    )
    view = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert view.drift == ("phase_field",)
    await update_dynamic_field(
        w.session, ticket_id=tid, field_name=TYPE_FIELD, values=["emergency"], user_id=1
    )
    assert (await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)).drift == (
        "type_field",
        "phase_field",
    )

    # closing the ticket behind Tiqora's back while the phase is not final
    closed = (
        await w.session.execute(
            text("SELECT id FROM ticket_state WHERE name = 'closed successful'")
        )
    ).scalar_one()
    await w.session.execute(
        text("UPDATE ticket SET ticket_state_id = :s WHERE id = :t"), {"s": closed, "t": tid}
    )
    drift = (await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)).drift
    assert drift == ("type_field", "phase_field", "ticket_closed")


async def test_process_ticket_only_offers_cancel_to_managers(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    await update_dynamic_field(
        w.session,
        ticket_id=tid,
        field_name="ProcessManagementProcessID",
        values=["Process-chg94"],
        user_id=1,
    )
    await update_dynamic_field(
        w.session,
        ticket_id=tid,
        field_name="ProcessManagementActivityID",
        values=["Activity-chg94"],
        user_id=1,
    )
    plain = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert (plain.process_ticket, plain.allowed_targets) == (True, ())
    manager = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_MANAGER)
    assert (manager.can_manage, manager.allowed_targets) == (True, ("cancelled",))


async def test_listener_called_in_same_transaction(
    w: ChangeWorld, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[tuple[ChangeEvent, int]] = []

    async def good(session, event):  # type: ignore[no-untyped-def]
        # the new row is visible: the listener runs in the caller's (uncommitted) transaction
        count = (
            await session.execute(
                select(func.count())
                .select_from(TiqoraItsmChange)
                .where(TiqoraItsmChange.ticket_id == event.ticket_id)
            )
        ).scalar_one()
        seen.append((event, int(count)))

    async def broken(session, event):  # type: ignore[no-untyped-def]
        raise RuntimeError("listener bug")

    async def bad_sql(session, event):  # type: ignore[no-untyped-def]
        await session.execute(text("SELECT * FROM chg94_no_such_table"))

    phase_only_calls: list[str] = []

    async def phase_only(session, event):  # type: ignore[no-untyped-def]
        phase_only_calls.append(event.event)

    _registry(
        request,
        listeners=[
            ChangeListener("test.good", frozenset({"created", "updated"}), good),
            ChangeListener("test.broken", frozenset({"created"}), broken),
            ChangeListener("test.bad_sql", frozenset({"created", "updated"}), bad_sql),
            ChangeListener("test.phase", frozenset({"phase"}), phase_only),
        ],
    )
    with caplog.at_level(logging.ERROR, logger="tiqora.itsm.change.service"):
        view = await _create(w)  # broken and bad_sql fail; the create still goes through
    tid = view.record.ticket_id
    assert [(e.event, e.from_phase, e.to_phase, e.user_id, c) for e, c in seen] == [
        ("created", None, "draft", U_FULL, 1)
    ]
    assert phase_only_calls == []
    assert {r.getMessage() for r in caplog.records} >= {"change_listener_failed"}
    assert await w.session.get(TiqoraItsmChange, tid) is not None  # the transaction is still usable

    await _patch(w, tid, reason="neu")
    assert [e.event for e, _ in seen] == ["created", "updated"]


async def test_mirror_is_noop_when_unchanged(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    before = await _history_count(w, tid)
    assert before == 2
    assert (
        await mirror_dynamic_fields(
            w.session, ticket_id=tid, change_type="normal", phase="draft", user_id=U_FULL
        )
        is True
    )
    assert await _history_count(w, tid) == before
    await mirror_dynamic_fields(
        w.session, ticket_id=tid, change_type="normal", phase="submitted", user_id=U_FULL
    )
    assert await _history_count(w, tid) == before + 1  # only the phase changed


async def test_mirror_failure_is_logged_not_raised(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tid = (await _create(w)).record.ticket_id

    async def refuse(*args: object, **kwargs: object) -> None:
        raise InvalidInput("not allowed any more")

    monkeypatch.setattr(change_service, "update_dynamic_field", refuse)
    with caplog.at_level(logging.WARNING, logger="tiqora.itsm.change.service"):
        ok = await mirror_dynamic_fields(
            w.session, ticket_id=tid, change_type="normal", phase="approval", user_id=U_FULL
        )
    assert ok is False
    assert [r.getMessage() for r in caplog.records].count("change_df_mirror_failed") == 2
    # the DF still holds the old value: the page shows the drift
    view = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert view.drift == ()  # record still says draft, DF says draft


async def test_events_page_newest_first(w: ChangeWorld) -> None:
    tid = (await _create(w)).record.ticket_id
    await _patch(w, tid, reason="a")
    await _patch(w, tid, reason="b")
    page = await list_events(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_RO, limit=2)
    assert [e.event for e in page] == ["updated", "updated"]
    older = await list_events(
        w.session, APP_SETTINGS, ticket_id=tid, user_id=U_RO, before_id=page[-1].id
    )
    assert [e.event for e in older] == ["phase", "created"]


async def test_make_change_helper_builds_a_row(w: ChangeWorld) -> None:
    await _plain_ticket(w, T_PLAIN, type_name="Change")
    row = await make_change(
        w.session, T_PLAIN, phase="scheduled", change_type="emergency", planned_start=FUTURE
    )
    assert (row.phase, row.change_type, row.approval_round) == ("scheduled", "emergency", 0)
