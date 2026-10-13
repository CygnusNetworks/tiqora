"""Incident-state propagation (D8, F17), service incident states (C5) and the full-recompute job.

A CI whose *own* incident state has functionality ``incident`` puts every CI that depends on it
(transitively, along link types whose ``propagation`` points that way) into the catalog's
``warning`` state; the derived state lives in ``tiqora_cmdb_ci.cur_incident_state_id``.

Edge semantics (cross ruling R1): for a link ``S -type-> T``, propagation ``source`` means the
source end gets the warning when the target is in incident (edge T -> S), ``target`` the other
way round, ``both`` both. ``u -> v`` below always reads "an incident at ``u`` affects ``v``".

Per save (``recompute_from``) the work is bounded (F17): the *scope* is the seeds plus everything
they affect (forward BFS); whether a scope member is warned depends only on its incident
ancestors, which a capped reverse BFS from the scope finds. Unrelated incident clusters are never
touched. If any cap (``propagation_node_cap``, depth 50) is hit nothing is written and the
``core.cmdb_propagation`` job is enqueued instead (``deferred=True``). The job is idempotent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.cmdb import links as links_mod
from tiqora.cmdb import service as service_mod
from tiqora.cmdb.catalog import CatalogMisconfigured
from tiqora.cmdb.links import LinkService, LinkTypeService
from tiqora.cmdb.seed import INCIDENT_STATE
from tiqora.cmdb.settings import CmdbSettings, load_cmdb_settings
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi, TiqoraItsmLink
from tiqora.itsm.ci_provider import CatalogValue, ServiceIncidentState
from tiqora.itsm.service_state import counts_for_service, fold_service_states, service_closure
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job

PROPAGATION_JOB_KIND: Final = "core.cmdb_propagation"
MAX_DEPTH: Final = 50
_CHUNK: Final = 1000


@dataclass(frozen=True, slots=True)
class PropagationResult:
    updated: int
    deferred: bool


class PropagationPayload(BaseModel):
    """Empty on purpose: the job always recomputes every CI."""


class _Capped(Exception):
    """A traversal cap was hit; the synchronous pass must defer to the job."""


def _chunks(items: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


# --- states ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _States:
    incident_ids: frozenset[int]  # every incident-state item with functionality ``incident``
    warning_id: int


async def _states(session: AsyncSession) -> _States:
    rows = (
        await session.execute(
            select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.functionality, TiqoraCmdbCatalog.valid)
            .where(TiqoraCmdbCatalog.catalog_class == INCIDENT_STATE)
            .order_by(TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.id)
        )
    ).all()
    incident = frozenset(int(i) for i, f, _ in rows if f == "incident")
    warning = next((int(i) for i, f, v in rows if f == "warning" and bool(v)), None)
    if warning is None:
        raise CatalogMisconfigured("no valid incident state with functionality 'warning'")
    return _States(incident, warning)


# --- graph ----------------------------------------------------------------------------------


class _Graph:
    """Lazily loaded propagation edges between CIs (``u`` affects ``v``)."""

    def __init__(self, session: AsyncSession, props: dict[str, str]) -> None:
        self._links = LinkService(session)
        self._props = props
        self._loaded: set[int] = set()
        self.affects: dict[int, set[int]] = {}
        self.affected_by: dict[int, set[int]] = {}

    async def expand(self, nodes: Iterable[int]) -> None:
        new = sorted(set(nodes) - self._loaded)
        if not new:
            return
        self._loaded.update(new)
        for src, tgt, name in await self._links.ci_neighbours(new, propagating_only=True):
            prop = self._props.get(name)
            if prop in ("source", "both"):
                self._edge(tgt, src)
            if prop in ("target", "both"):
                self._edge(src, tgt)

    def _edge(self, u: int, v: int) -> None:
        self.affects.setdefault(u, set()).add(v)
        self.affected_by.setdefault(v, set()).add(u)

    async def bfs(
        self,
        start: Iterable[int],
        *,
        reverse: bool = False,
        node_cap: int | None,
        max_depth: int | None,
    ) -> set[int]:
        """Nodes reachable from ``start`` (including it); raises :class:`_Capped` on a cap."""
        visited = set(start)
        if node_cap is not None and len(visited) > node_cap:
            raise _Capped
        frontier = set(visited)
        depth = 0
        adjacency = self.affected_by if reverse else self.affects
        while frontier:
            await self.expand(frontier)
            nxt: set[int] = set()
            for n in frontier:
                nxt |= adjacency.get(n, set()) - visited
            if not nxt:
                break
            depth += 1
            if max_depth is not None and depth > max_depth:
                raise _Capped
            visited |= nxt
            if node_cap is not None and len(visited) > node_cap:
                raise _Capped
            frontier = nxt
        return visited

    def warned_within(self, universe: set[int], incident: Iterable[int]) -> set[int]:
        """Nodes of ``universe`` reachable over >= 1 edge from an incident node.

        ``universe`` must be closed under predecessors (every ancestor present and expanded),
        then every path from an incident node to a member lies inside it.
        """
        warned: set[int] = set()
        frontier = {i for i in incident if i in universe}
        seen = set(frontier)
        while frontier:
            nxt: set[int] = set()
            for n in frontier:
                for v in self.affects.get(n, ()):
                    if v in universe:
                        warned.add(v)
                        if v not in seen:
                            seen.add(v)
                            nxt.add(v)
            frontier = nxt
        return warned


async def _ci_rows(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[int, int]]:
    """CI id -> (own incident state id, current derived incident state id)."""
    out: dict[int, tuple[int, int]] = {}
    for chunk in _chunks(sorted(ids)):
        rows = await session.execute(
            select(
                TiqoraCmdbCi.id, TiqoraCmdbCi.incident_state_id, TiqoraCmdbCi.cur_incident_state_id
            ).where(TiqoraCmdbCi.id.in_(list(chunk)))
        )
        for i, own, cur in rows.all():
            out[int(i)] = (int(own), int(cur))
    return out


def _target_state(ci_id: int, own: int, states: _States, warned: set[int]) -> int:
    if own in states.incident_ids:
        return own
    return states.warning_id if ci_id in warned else own


async def _write(session: AsyncSession, changes: dict[int, list[int]]) -> int:
    """``new state id -> CI ids``; returns the number of rows written."""
    total = 0
    for state_id, ids in changes.items():
        for chunk in _chunks(sorted(ids)):
            await session.execute(
                update(TiqoraCmdbCi)
                .where(TiqoraCmdbCi.id.in_(list(chunk)))
                .values(cur_incident_state_id=state_id)
                .execution_options(synchronize_session=False)
            )
            total += len(chunk)
    return total


# --- per-save recompute ---------------------------------------------------------------------


async def enqueue_full_recompute(session: AsyncSession, *, user_id: int | None = None) -> int:
    """Queue the ``core.cmdb_propagation`` job (dedupe key ``all``); returns the job id.

    Flushes, never commits. Called on a cap hit, by ``POST /admin/cmdb/propagation/recompute``
    and (Task 30) when itsm1's link-type editor changes ``propagation`` / ``pointed`` / ``valid``.
    """
    job = await enqueue_job(
        session,
        kind=PROPAGATION_JOB_KIND,
        payload=PropagationPayload(),
        created_by=user_id,
        dedupe_key="all",
    )
    return int(job.id)


async def recompute_from(
    session: AsyncSession,
    seed_ci_ids: Sequence[int],
    *,
    settings: CmdbSettings | None = None,
) -> PropagationResult:
    """Recompute the derived incident state of the seeds and everything they affect."""
    seeds = {int(i) for i in seed_ci_ids}
    if not seeds:
        return PropagationResult(0, False)
    cfg = settings if settings is not None else await load_cmdb_settings(session)
    cap = int(cfg.propagation_node_cap)
    props = await LinkTypeService(session).propagating()
    graph = _Graph(session, props)
    states = await _states(session)
    try:
        scope = await graph.bfs(seeds, node_cap=cap, max_depth=MAX_DEPTH)
        universe = await graph.bfs(scope, reverse=True, node_cap=cap, max_depth=MAX_DEPTH)
    except _Capped:
        await enqueue_full_recompute(session)
        return PropagationResult(0, True)
    rows = await _ci_rows(session, sorted(universe))
    incident = [i for i, (own, _) in rows.items() if own in states.incident_ids]
    warned = graph.warned_within(universe, incident)
    changes: dict[int, list[int]] = {}
    for ci_id in scope:
        if ci_id not in rows:
            continue
        own, cur = rows[ci_id]
        want = _target_state(ci_id, own, states, warned)
        if want != cur:
            changes.setdefault(want, []).append(ci_id)
    return PropagationResult(await _write(session, changes), False)


# --- full recompute (job) -------------------------------------------------------------------


async def recompute_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: CmdbSettings,
    progress: Callable[[int, int | None], Awaitable[None]] | None = None,
) -> int:
    """Recompute every CI in batches of 1000, one commit per batch; returns rows changed.

    No node cap: this runs as a job. Termination is guaranteed by the visited set (cycles).
    ``settings`` is accepted for symmetry with :func:`recompute_from`.
    """
    _ = settings
    async with session_factory() as session:
        states = await _states(session)
        props = await LinkTypeService(session).propagating()
        graph = _Graph(session, props)
        incident = [
            int(i)
            for i in (
                await session.execute(
                    select(TiqoraCmdbCi.id).where(
                        TiqoraCmdbCi.incident_state_id.in_(sorted(states.incident_ids))
                    )
                )
            )
            .scalars()
            .all()
        ]
        reach = await graph.bfs(incident, node_cap=None, max_depth=None)
        warned = graph.warned_within(reach, incident)
        total = int((await session.execute(select(func.count(TiqoraCmdbCi.id)))).scalar_one())
    done = 0
    updated = 0
    last = 0
    if progress is not None:
        await progress(0, total)
    while True:
        async with session_factory() as session:
            batch = (
                await session.execute(
                    select(
                        TiqoraCmdbCi.id,
                        TiqoraCmdbCi.incident_state_id,
                        TiqoraCmdbCi.cur_incident_state_id,
                    )
                    .where(TiqoraCmdbCi.id > last)
                    .order_by(TiqoraCmdbCi.id)
                    .limit(_CHUNK)
                )
            ).all()
            if not batch:
                break
            changes: dict[int, list[int]] = {}
            for ci_id, own, cur in batch:
                want = _target_state(int(ci_id), int(own), states, warned)
                if want != int(cur):
                    changes.setdefault(want, []).append(int(ci_id))
            updated += await _write(session, changes)
            await session.commit()
            last = int(batch[-1][0])
            done += len(batch)
        if progress is not None:
            await progress(done, max(total, done))
    return updated


async def run_propagation_job(ctx: JobContext, payload: PropagationPayload) -> dict[str, int]:
    _ = payload
    async with ctx.session_factory() as session:
        settings = await load_cmdb_settings(session)
    updated = await recompute_all(ctx.session_factory, settings=settings, progress=_progress(ctx))
    return {"updated": updated}


def _progress(ctx: JobContext) -> Callable[[int, int | None], Awaitable[None]]:
    async def report(done: int, total: int | None) -> None:
        await ctx.progress(done, total, "Vorfallstatus wird neu berechnet …")

    return report


CMDB_PROPAGATION_JOB = JobHandler(
    kind=PROPAGATION_JOB_KIND,
    payload_model=PropagationPayload,
    run=run_propagation_job,
    title_key="jobs.kinds.core.cmdb_propagation",
    timeout_seconds=3600,
    requeue_if_interrupted=True,  # idempotent, so a requeue after a crash is safe
    max_attempts=3,
)


# --- service incident states (C5) -----------------------------------------------------------


async def service_incident_states(
    session: AsyncSession, service_ids: Sequence[int]
) -> dict[int, ServiceIncidentState]:
    """Service incident state from the Tiqora CMDB, with the ITSM core's rule (R12).

    :mod:`tiqora.itsm.service_state` folds it the same way as Mode A (behaviour survey,
    question 2): propagating link types in their direction, productive CIs only, worst
    functionality wins, a non-operational valid child service (``Parent::Child``) raises an
    operational parent to ``warning``; no linked CI and no such child gives ``state=None``.
    Separate statements on ``service`` and the ``tiqora_*`` tables (no JOIN between them).
    """
    wanted = sorted({int(i) for i in service_ids})
    out = {i: ServiceIncidentState(i, None, 0, 0) for i in wanted}
    if not wanted:
        return out
    services: dict[int, tuple[str, bool]] = {
        int(sid): (str(name), int(valid) == 1)
        for sid, name, valid in (
            await session.execute(text("SELECT id, name, valid_id FROM service"))
        ).all()
    }
    closure, children_of = service_closure(services, wanted)
    if not closure:
        return out
    types = {t.name: t for t in await LinkTypeService(session).list_types()}
    rows: list[tuple[int, int, str, bool]] = []  # (service, ci, link type, service is source)
    for chunk in _chunks(sorted(closure)):
        keys = [str(i) for i in chunk]
        for sk, skind, tk, name in (
            await session.execute(
                select(
                    TiqoraItsmLink.source_key,
                    TiqoraItsmLink.source_kind,
                    TiqoraItsmLink.target_key,
                    TiqoraItsmLink.link_type,
                ).where(
                    (
                        (TiqoraItsmLink.source_kind == "service")
                        & (TiqoraItsmLink.source_key.in_(keys))
                        & (TiqoraItsmLink.target_kind == "ci")
                    )
                    | (
                        (TiqoraItsmLink.target_kind == "service")
                        & (TiqoraItsmLink.target_key.in_(keys))
                        & (TiqoraItsmLink.source_kind == "ci")
                    ),
                )
            )
        ).all():
            service_is_source = skind == "service"
            svc, ci = (sk, tk) if service_is_source else (tk, sk)
            if str(svc).isdigit() and str(ci).isdigit():
                rows.append((int(svc), int(ci), str(name), service_is_source))
    ci_state = await _ci_states(session, sorted({ci for _, ci, _, _ in rows}))
    functionality = await _functionalities(session, {i for pair in ci_state.values() for i in pair})
    linked: dict[int, bool] = {}
    counted: dict[int, dict[int, str | None]] = {}
    for svc, ci, name, service_is_source in rows:
        if ci not in ci_state:
            continue  # a link to a CI that no longer exists
        linked[svc] = True
        ltype = types.get(name)
        depl, cur = ci_state[ci]
        if (
            ltype is not None
            and counts_for_service(str(ltype.propagation), bool(ltype.pointed), service_is_source)
            and functionality.get(depl) == "productive"
        ):
            counted.setdefault(svc, {})[ci] = functionality.get(cur)
    folded = fold_service_states(
        services,
        children_of,
        closure,
        {svc: list(by_ci.values()) for svc, by_ci in counted.items()},
        linked,
    )
    canonical = await _canonical_incident_values(session)
    for sid in wanted:
        fold = folded.get(sid)
        if fold is None:
            continue
        shown = fold.shown
        out[sid] = ServiceIncidentState(
            sid,
            None if shown is None else canonical.get(shown),
            fold.cis_incident,
            fold.cis_warning,
        )
    return out


async def _ci_states(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[int, int]]:
    """CI id -> (deployment state id, current derived incident state id)."""
    out: dict[int, tuple[int, int]] = {}
    for chunk in _chunks(sorted(ids)):
        rows = await session.execute(
            select(
                TiqoraCmdbCi.id,
                TiqoraCmdbCi.deployment_state_id,
                TiqoraCmdbCi.cur_incident_state_id,
            ).where(TiqoraCmdbCi.id.in_(list(chunk)))
        )
        for i, depl, cur in rows.all():
            out[int(i)] = (int(depl), int(cur))
    return out


async def _functionalities(session: AsyncSession, ids: Iterable[int]) -> dict[int, str | None]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = await session.execute(
        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.functionality).where(
            TiqoraCmdbCatalog.id.in_(wanted)
        )
    )
    return {int(i): f for i, f in rows.all()}


async def _canonical_incident_values(session: AsyncSession) -> dict[str, CatalogValue]:
    """Per functionality the incident state a service shows: the valid one whose key is the
    functionality (seeds ``operational``/``warning``/``incident``), else the first by position."""
    rows = (
        await session.execute(
            select(
                TiqoraCmdbCatalog.item_key,
                TiqoraCmdbCatalog.name,
                TiqoraCmdbCatalog.functionality,
            )
            .where(
                TiqoraCmdbCatalog.catalog_class == INCIDENT_STATE,
                TiqoraCmdbCatalog.valid.is_(True),
            )
            .order_by(TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.id)
        )
    ).all()
    out: dict[str, CatalogValue] = {}
    for key, name, func_ in rows:
        if func_ is None:
            continue
        current = out.get(func_)
        if current is None or (str(key) == func_ and current.key != func_):
            out[func_] = CatalogValue(str(key), str(name), func_)
    return out


async def _hook(session: AsyncSession, ci_ids: list[int]) -> None:
    await recompute_from(session, ci_ids)


#: wired at import of this module (``tiqora/cmdb/__init__.py`` imports it)
service_mod._recompute_hook = _hook
links_mod._recompute_hook = _hook

__all__ = [
    "CMDB_PROPAGATION_JOB",
    "PROPAGATION_JOB_KIND",
    "PropagationPayload",
    "PropagationResult",
    "enqueue_full_recompute",
    "recompute_all",
    "recompute_from",
    "run_propagation_job",
    "service_incident_states",
]
