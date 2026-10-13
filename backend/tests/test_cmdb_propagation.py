"""CMDB incident propagation: cycles, caps, deferral to the job, service states (band 743xxx)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    SERVICE_ID,
    SERVICE_ID_2,
    USER_A,
    USER_ADMIN,
    CmdbWorld,
    _inc,
    client_for,
    make_ci,
    make_class,
    make_link_type,
    run_jobs_until_idle,
    set_mode,
)
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.propagation import (
    CMDB_PROPAGATION_JOB,
    enqueue_full_recompute,
    recompute_all,
    recompute_from,
    service_incident_states,
)
from tiqora.cmdb.service import CiService, system_access
from tiqora.cmdb.settings import CmdbSettings
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi, TiqoraItsmLink
from tiqora.extensions import get_registry
from tiqora.jobs.registry import JOB_HANDLERS

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _tiny_cap(cap: int) -> CmdbSettings:
    # below the validated floor (100): model_construct skips validation, test only
    return CmdbSettings.model_construct(
        **{**CmdbSettings().model_dump(), "propagation_node_cap": cap}
    )


async def _link(
    s: AsyncSession, a: int, b: int, link_type: str = "DependsOn", kind_b: str = "ci"
) -> None:
    await LinkService(s).add(
        system_access(USER_A),
        source=LinkEnd("ci", str(a)),
        target=LinkEnd(kind_b, str(b)),  # type: ignore[arg-type]
        link_type_key=link_type,
    )
    await s.commit()


async def _chain(
    s: AsyncSession,
    names: Sequence[str],
    link_type: str = "DependsOn",
    *,
    class_key: str = "cmdb4srv",
) -> list[int]:
    """``names[i]`` DependsOn ``names[i+1]``: an incident at the end propagates to the start."""
    cid = await make_class(s, key=class_key)
    return await _chain_in(s, cid, names, link_type)


async def _chain_in(
    s: AsyncSession, class_id: int, names: Sequence[str], link_type: str = "DependsOn"
) -> list[int]:
    ids = [await make_ci(s, class_id, n) for n in names]
    for a, b in zip(ids, ids[1:], strict=False):
        await _link(s, a, b, link_type)
    return ids


async def _set_own_incident(
    s: AsyncSession, ci_id: int, key: str, *, recompute: bool = True
) -> None:
    await CiService(s).update(
        system_access(USER_A),
        ci_id,
        expected_lock_version=None,
        incident_state_id=await _inc(s, key),
        recompute=recompute,
        enforce_permissions=False,
    )
    await s.commit()


async def _cur(s: AsyncSession, ids: Sequence[int]) -> list[str]:
    rows = (
        await s.execute(
            select(TiqoraCmdbCi.id, TiqoraCmdbCatalog.item_key)
            .select_from(TiqoraCmdbCi)
            .join(TiqoraCmdbCatalog, TiqoraCmdbCatalog.id == TiqoraCmdbCi.cur_incident_state_id)
            .where(TiqoraCmdbCi.id.in_(list(ids)))
        )
    ).all()
    by_id = {int(i): str(k) for i, k in rows}
    return [by_id[i] for i in ids]


async def _link_id(s: AsyncSession, a: int, b: int) -> int:
    return int(
        (
            await s.execute(
                select(TiqoraItsmLink.id).where(
                    TiqoraItsmLink.source_kind == "ci",
                    TiqoraItsmLink.source_key == str(a),
                    TiqoraItsmLink.target_key == str(b),
                )
            )
        ).scalar_one()
    )


async def _queued_job_kinds(s: AsyncSession) -> list[str]:
    rows = await s.execute(text("SELECT kind FROM tiqora_job WHERE status = 'queued' ORDER BY id"))
    return [str(k) for (k,) in rows.all()]


async def test_incident_marks_dependents_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b, c = await _chain(s, ["cmdb4-app", "cmdb4-db", "cmdb4-disk"])
        await _set_own_incident(s, c, "incident")
        assert await _cur(s, [a, b, c]) == ["warning", "warning", "incident"]


#: a directed test type that propagates only to the source end (the stock DependsOn is "both")
SRC_ONLY = "Cmdb4DependsSrc"


async def test_incident_does_not_propagate_to_dependencies(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await make_link_type(s, SRC_ONLY, "source")
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"], link_type=SRC_ONLY)
        await _set_own_incident(s, a, "incident")  # a depends on b, not the other way round
        assert await _cur(s, [a, b]) == ["incident", "operational"]


async def test_default_depends_on_warns_both_directions_transitively(cmdb_db: CmdbWorld) -> None:
    """Survey question 2, s2b/s2c: Znuny ships {DependsOn: Both}; the warning follows the link
    in either direction and over several hops."""
    async with cmdb_db.factory() as s:
        a, b, c = await _chain(s, ["cmdb4-a", "cmdb4-b", "cmdb4-c"])
        await _set_own_incident(s, a, "incident")  # the dependent end is in incident
        assert await _cur(s, [a, b, c]) == ["incident", "warning", "warning"]
        await _set_own_incident(s, a, "operational")
        assert await _cur(s, [a, b, c]) == ["operational"] * 3


async def test_cycle_terminates_and_marks_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b, c = await _chain(s, ["cmdb4-a", "cmdb4-b", "cmdb4-c"])
        await _link(s, c, a)
        await _set_own_incident(s, c, "incident")
        assert await _cur(s, [a, b, c]) == ["warning", "warning", "incident"]


async def test_clearing_incident_clears_cycle(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b, c = await _chain(s, ["cmdb4-a", "cmdb4-b", "cmdb4-c"])
        await _link(s, c, a)
        await _set_own_incident(s, c, "incident")
        assert await _cur(s, [a, b, c]) == ["warning", "warning", "incident"]
        await _set_own_incident(s, c, "operational")
        assert await _cur(s, [a, b, c]) == ["operational"] * 3


async def test_link_delete_clears_downstream_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"])
        await _set_own_incident(s, b, "incident")
        assert await _cur(s, [a, b]) == ["warning", "incident"]
        link_id = await _link_id(s, a, b)
        await LinkService(s).delete(system_access(USER_A), link_id)
        await s.commit()
        assert await _cur(s, [a, b]) == ["operational", "incident"]


async def test_link_add_to_incident_ci_marks_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b", incident="incident")
        assert await _cur(s, [a, b]) == ["operational", "incident"]
        await _link(s, a, b)
        assert await _cur(s, [a, b]) == ["warning", "incident"]


async def test_ci_delete_clears_dependent_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"])
        await _set_own_incident(s, b, "incident")
        assert await _cur(s, [a]) == ["warning"]
        await CiService(s).delete(system_access(USER_A), b, enforce_permissions=False)
        await s.commit()
        assert await _cur(s, [a]) == ["operational"]


async def test_non_propagating_type_does_not_propagate(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"], link_type="ConnectedTo")
        await _set_own_incident(s, b, "incident")
        assert await _cur(s, [a, b]) == ["operational", "incident"]


async def test_two_incidents_one_cleared_keeps_warning(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await make_link_type(s, SRC_ONLY, "source")
        cid = await make_class(s)
        a, b, c = [await make_ci(s, cid, f"cmdb4-{n}") for n in "abc"]
        await _link(s, a, b, SRC_ONLY)
        await _link(s, a, c, SRC_ONLY)
        await _set_own_incident(s, b, "incident")
        await _set_own_incident(s, c, "incident")
        assert await _cur(s, [a, b, c]) == ["warning", "incident", "incident"]
        await _set_own_incident(s, b, "operational")
        assert await _cur(s, [a, b, c]) == ["warning", "operational", "incident"]
        await _set_own_incident(s, c, "operational")
        assert await _cur(s, [a, b, c]) == ["operational"] * 3


async def test_cap_defers_to_job_without_partial_update(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ids = await _chain(s, [f"cmdb4-n{i}" for i in range(12)])
        await _set_own_incident(s, ids[-1], "incident", recompute=False)
        res = await recompute_from(s, [ids[-1]], settings=_tiny_cap(5))
        await s.commit()
        assert res.deferred
        assert res.updated == 0
        assert await _cur(s, ids[:-1]) == ["operational"] * 11
        assert await _queued_job_kinds(s) == ["core.cmdb_propagation"]
    await run_jobs_until_idle(cmdb_db)
    async with cmdb_db.factory() as s:
        assert await _cur(s, ids[:-1]) == ["warning"] * 11
        assert await _cur(s, ids[-1:]) == ["incident"]
        status = (await s.execute(text("SELECT status FROM tiqora_job"))).scalar_one()
        assert status == "succeeded"


async def test_unrelated_incident_clusters_do_not_defer(cmdb_db: CmdbWorld) -> None:
    """F17: bounded per-save work. Three large unrelated incident clusters, small edited chain."""
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        clusters = [
            await _chain_in(s, cid, [f"cmdb4-c{k}n{i}" for i in range(8)]) for k in range(3)
        ]
        for ids in clusters:
            await _set_own_incident(s, ids[-1], "incident")
        small = await _chain_in(s, cid, ["cmdb4-x", "cmdb4-y", "cmdb4-z"])
        await _set_own_incident(s, small[-1], "incident", recompute=False)
        res = await recompute_from(s, [small[-1]], settings=_tiny_cap(5))
        await s.commit()
        assert not res.deferred
        assert res.updated == 2
        assert await _cur(s, small) == ["warning", "warning", "incident"]
        assert await _queued_job_kinds(s) == []
        for ids in clusters:
            assert await _cur(s, ids) == ["warning"] * 7 + ["incident"]


async def test_cap_counts_incident_ancestors_of_scope(cmdb_db: CmdbWorld) -> None:
    """A big dependency tree *below* the edited CI is the reverse BFS: it is capped, too."""
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        top = await make_ci(s, cid, "cmdb4-top")
        deps = [await make_ci(s, cid, f"cmdb4-dep{i}") for i in range(8)]
        for d in deps:
            await _link(s, top, d)
        await _set_own_incident(s, deps[0], "incident", recompute=False)
        res = await recompute_from(s, [top], settings=_tiny_cap(5))
        await s.commit()
        assert res.deferred
        assert await _cur(s, [top]) == ["operational"]


async def test_recompute_all_job_fixes_stale_states(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ids = await _chain(s, [f"cmdb4-n{i}" for i in range(4)])
        await _set_own_incident(s, ids[-1], "incident", recompute=False)
        assert await _cur(s, ids) == ["operational"] * 3 + ["incident"]
    seen: list[tuple[int, int | None]] = []

    async def progress(done: int, total: int | None) -> None:
        seen.append((done, total))

    updated = await recompute_all(cmdb_db.factory, settings=CmdbSettings(), progress=progress)
    assert updated == 3
    assert seen[0] == (0, 4)
    assert seen[-1] == (4, 4)
    async with cmdb_db.factory() as s:
        assert await _cur(s, ids) == ["warning"] * 3 + ["incident"]
    # idempotent: a second run changes nothing
    assert await recompute_all(cmdb_db.factory, settings=CmdbSettings()) == 0


async def test_service_incident_states(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        x = await make_ci(s, cid, "cmdb4-x")
        # the service is the source: it depends on CI x (DependsOn propagates to the source)
        await _link(s, x, SERVICE_ID, "DependsOn", kind_b="service")  # CI -> service: wrong way
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("service", str(SERVICE_ID)),
            target=LinkEnd("ci", str(x)),
            link_type_key="DependsOn",
        )
        await s.commit()
        before = await service_incident_states(s, [SERVICE_ID, SERVICE_ID_2])
        assert before[SERVICE_ID].state is not None
        assert before[SERVICE_ID].state.functionality == "operational"
        assert before[SERVICE_ID_2].state is None
        await _set_own_incident(s, x, "incident")
        after = await service_incident_states(s, [SERVICE_ID, SERVICE_ID_2])
        assert after[SERVICE_ID].state is not None
        assert after[SERVICE_ID].state.functionality == "incident"
        assert after[SERVICE_ID].cis_incident == 1
        assert after[SERVICE_ID].cis_warning == 0
        assert after[SERVICE_ID_2].state is None
        assert after[SERVICE_ID_2].cis_incident == 0


async def test_service_state_is_warning_when_ci_only_warned(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"])
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("service", str(SERVICE_ID)),
            target=LinkEnd("ci", str(a)),
            link_type_key="DependsOn",
        )
        await s.commit()
        await _set_own_incident(s, b, "incident")  # a is warned, the service is not linked to b
        got = (await service_incident_states(s, [SERVICE_ID]))[SERVICE_ID]
        assert got.state is not None
        assert got.state.functionality == "warning"
        assert (got.cis_incident, got.cis_warning) == (0, 1)


async def test_service_state_ignores_non_propagating_link(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        x = await make_ci(s, cid, "cmdb4-x", incident="incident")
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("ci", str(x)),
            target=LinkEnd("service", str(SERVICE_ID)),
            link_type_key="ConnectedTo",
        )
        await s.commit()
        got = (await service_incident_states(s, [SERVICE_ID]))[SERVICE_ID]
        # survey s5 (RelevantTo does not count): a linked CI on a non-propagating type leaves the
        # service operational -- it has CIs, so it has a state, as in Mode A
        assert got.state is not None and got.state.functionality == "operational"
        assert (got.cis_incident, got.cis_warning) == (0, 0)


async def _service_link(s: AsyncSession, service_id: int, ci_id: int) -> None:
    await LinkService(s).add(
        system_access(USER_A),
        source=LinkEnd("service", str(service_id)),
        target=LinkEnd("ci", str(ci_id)),
        link_type_key="DependsOn",
    )
    await s.commit()


async def _functionality(s: AsyncSession, service_id: int) -> str | None:
    got = (await service_incident_states(s, [service_id]))[service_id]
    return None if got.state is None else got.state.functionality


async def test_service_state_counts_only_productive_cis(cmdb_db: CmdbWorld) -> None:
    """Survey s3: an incident CI in a non-productive deployment state (Planned) is ignored."""
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        planned = await make_ci(s, cid, "cmdb4-planned", deployment="planned", incident="incident")
        await _service_link(s, SERVICE_ID, planned)
        assert await _functionality(s, SERVICE_ID) == "operational"
        live = await make_ci(s, cid, "cmdb4-live", deployment="repair", incident="incident")
        await _service_link(s, SERVICE_ID, live)  # "repair" is productive too
        got = (await service_incident_states(s, [SERVICE_ID]))[SERVICE_ID]
        assert got.state is not None and got.state.functionality == "incident"
        assert got.state.key == "incident" and got.cis_incident == 1


async def test_service_state_child_service_raises_parent_to_warning(cmdb_db: CmdbWorld) -> None:
    """Survey s6: a valid ``Parent::Child`` in incident makes an operational parent warning (never
    incident); a parent that is worse on its own keeps its state."""
    child_id = 740402
    cmdb_db.sql(
        "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time, change_by)"
        " VALUES (:i, :n, 1, :t, 1, :t, 1)",
        i=child_id,
        n="cmdb4-service::child",
        t="2026-10-01 08:00:00",
    )
    try:
        async with cmdb_db.factory() as s:
            cid = await make_class(s)
            bad = await make_ci(s, cid, "cmdb4-bad", incident="incident")
            await _service_link(s, child_id, bad)
            states = await service_incident_states(s, [SERVICE_ID, child_id])
            assert states[child_id].state is not None
            assert states[child_id].state.functionality == "incident"
            parent = states[SERVICE_ID]  # no CI of its own, but a child in incident
            assert parent.state is not None and parent.state.functionality == "warning"
            assert (parent.cis_incident, parent.cis_warning) == (0, 0)
        cmdb_db.sql("UPDATE service SET valid_id = 2 WHERE id = :i", i=child_id)
        async with cmdb_db.factory() as s:  # new snapshot (MariaDB: repeatable read)
            assert await _functionality(s, SERVICE_ID) is None  # an invalid child does not count
    finally:
        cmdb_db.sql("DELETE FROM tiqora_itsm_link WHERE source_key = :k", k=str(child_id))
        cmdb_db.sql("DELETE FROM service WHERE id = :i", i=child_id)


async def test_service_state_incident_beats_warning(cmdb_db: CmdbWorld) -> None:
    """Survey s4: one warned and one incident CI give incident; the counts name both."""
    async with cmdb_db.factory() as s:
        await make_link_type(s, SRC_ONLY, "source")
        cid = await make_class(s)
        warned, root = [await make_ci(s, cid, n) for n in ("cmdb4-warned", "cmdb4-root")]
        await _link(s, warned, root, SRC_ONLY)
        await _set_own_incident(s, root, "incident")
        hit = await make_ci(s, cid, "cmdb4-hit", incident="incident")
        await _service_link(s, SERVICE_ID, warned)
        assert await _functionality(s, SERVICE_ID) == "warning"
        await _service_link(s, SERVICE_ID, hit)
        got = (await service_incident_states(s, [SERVICE_ID]))[SERVICE_ID]
        assert got.state is not None and got.state.functionality == "incident"
        assert (got.cis_incident, got.cis_warning) == (1, 1)


async def test_link_type_propagation_change_enqueues_full_recompute(cmdb_db: CmdbWorld) -> None:
    """R2: the link-type editor is itsm1's; it calls ``enqueue_full_recompute`` (deduped)."""
    assert get_registry().get(JOB_HANDLERS, "core.cmdb_propagation") == CMDB_PROPAGATION_JOB
    async with cmdb_db.factory() as s:
        first = await enqueue_full_recompute(s, user_id=USER_ADMIN)
        second = await enqueue_full_recompute(s, user_id=USER_ADMIN)
        await s.commit()
        assert first == second
        assert await _queued_job_kinds(s) == ["core.cmdb_propagation"]


async def _propagation_jobs(s: AsyncSession) -> list[tuple[str, str | None]]:
    rows = await s.execute(
        text(
            "SELECT kind, dedupe_key FROM tiqora_job WHERE kind = 'core.cmdb_propagation'"
            " AND status = 'queued'"
        )
    )
    return [(str(k), d) for k, d in rows.all()]


async def test_itsm_link_type_patch_queues_cmdb_recompute(cmdb_db: CmdbWorld) -> None:
    """Task 30 / R2: itsm1's link-type PATCH queues the full recompute in Mode B when
    propagation, pointed or valid changes; other fields (labels) do not."""
    base = "/api/v1/admin/itsm/link-types"
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(base, json={"name": "Cmdb4Feeds", "pointed": True, "propagation": "none"})
        assert r.status_code == 201, r.text
        type_id = r.json()["id"]
        r = await c.patch(f"{base}/{type_id}", json={"source_label": "speist"})
        assert r.status_code == 200, r.text
        async with cmdb_db.factory() as s:
            assert await _propagation_jobs(s) == []
        r = await c.patch(f"{base}/{type_id}", json={"propagation": "source"})
        assert r.status_code == 200, r.text
        async with cmdb_db.factory() as s:
            assert await _propagation_jobs(s) == [("core.cmdb_propagation", "all")]
        # deduplicated: a second relevant change joins the queued job
        for body in ({"pointed": False}, {"valid": False}):
            r = await c.patch(f"{base}/{type_id}", json=body)
            assert r.status_code == 200, r.text
        async with cmdb_db.factory() as s:
            assert await _propagation_jobs(s) == [("core.cmdb_propagation", "all")]


async def test_itsm_link_type_patch_valid_change_queues_recompute(cmdb_db: CmdbWorld) -> None:
    base = "/api/v1/admin/itsm/link-types"
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(base, json={"name": "Cmdb4Feeds", "propagation": "both"})
        assert r.status_code == 201, r.text
        r = await c.patch(f"{base}/{r.json()['id']}", json={"valid": False})
        assert r.status_code == 200, r.text
    async with cmdb_db.factory() as s:
        assert await _propagation_jobs(s) == [("core.cmdb_propagation", "all")]


async def test_itsm_link_type_patch_outside_mode_b_queues_nothing(cmdb_db: CmdbWorld) -> None:
    base = "/api/v1/admin/itsm/link-types"
    set_mode(cmdb_db, "znuny_readthrough")
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(base, json={"name": "Cmdb4Feeds", "propagation": "none"})
        assert r.status_code == 201, r.text
        r = await c.patch(f"{base}/{r.json()['id']}", json={"propagation": "both"})
        assert r.status_code == 200, r.text
    async with cmdb_db.factory() as s:
        assert await _propagation_jobs(s) == []


async def test_itsm_link_type_sync_without_changes_queues_nothing(cmdb_db: CmdbWorld) -> None:
    """The sync adds Znuny's own types (``Normal``, ``ParentChild``) as new rows: new rows carry
    no links, so no recompute; it removes only the rows it added here."""
    async with cmdb_db.factory() as s:
        before = set((await s.execute(text("SELECT name FROM tiqora_itsm_link_type"))).scalars())
    try:
        async with client_for(cmdb_db, USER_ADMIN) as c:
            r = await c.post("/api/v1/admin/itsm/link-types/sync")
            assert r.status_code == 200, r.text
        async with cmdb_db.factory() as s:
            assert await _propagation_jobs(s) == []
    finally:
        async with cmdb_db.factory() as s:
            names = set((await s.execute(text("SELECT name FROM tiqora_itsm_link_type"))).scalars())
            for name in sorted(names - before):
                await s.execute(
                    text("DELETE FROM tiqora_itsm_link_type WHERE name = :n"), {"n": name}
                )
            await s.commit()


async def test_itsm_link_type_recompute_job_applies_the_new_direction(cmdb_db: CmdbWorld) -> None:
    """End to end: a type that did not propagate starts to after the PATCH and the job."""
    base = "/api/v1/admin/itsm/link-types"
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(base, json={"name": "Cmdb4Feeds", "pointed": True, "propagation": "none"})
        assert r.status_code == 201, r.text
        type_id = r.json()["id"]
    async with cmdb_db.factory() as s:
        a, b = await _chain(s, ["cmdb4-a", "cmdb4-b"], link_type="Cmdb4Feeds")
        await _set_own_incident(s, b, "incident")
        assert await _cur(s, [a, b]) == ["operational", "incident"]
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.patch(f"{base}/{type_id}", json={"propagation": "source"})
        assert r.status_code == 200, r.text
    await run_jobs_until_idle(cmdb_db)
    async with cmdb_db.factory() as s:
        assert await _cur(s, [a, b]) == ["warning", "incident"]


async def test_recompute_route_enqueues_job(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post("/api/v1/admin/cmdb/propagation/recompute")
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        assert r.json()["status"] == "queued"
        again = await c.post("/api/v1/admin/cmdb/propagation/recompute")
        assert again.json()["job_id"] == job_id
        r = await c.get(f"/api/v1/jobs/{job_id}")
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "core.cmdb_propagation"
    async with client_for(cmdb_db, USER_A) as c:
        assert (await c.post("/api/v1/admin/cmdb/propagation/recompute")).status_code == 403
