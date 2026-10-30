"""Fixtures of the change module tests: an in-memory approval port instead of the engine.

Real engine switch (Task 17): with ``TIQORA_TEST_REAL_APPROVALS=1`` the ``port`` fixtures of the
transition, CAB and calendar-mirror tests (Tasks 12, 13, 16; built with :func:`approval_port_for`)
install :class:`RecordingEnginePort` - the real ``EngineApprovalPort`` that records its calls in the
fake's lists - instead of :class:`FakeApprovalPort`, and :func:`settle` decides through the engine
(votes, cancel) instead of the fake's ``decide``::

    TIQORA_TEST_REAL_APPROVALS=1 uv run python -m pytest -q -k "transition or cab" \
        tests/test_itsm_change_transitions.py tests/test_itsm_change_cab.py \
        tests/test_itsm_change_calendar_mirror.py

Tests that reach into the fake's state (``snapshots``, ``reject_at_once``) call
:func:`require_fake` and are skipped under the switch. Without it they run against the fake (fast,
engine-free); ``tests/test_itsm_change_approval_engine.py`` always runs against the engine.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._itsm_mode_helpers import make_factory, plain_url, store
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.approvals_consumer_kit import approve_all, reject_first
from tiqora.approvals import service as approval_service
from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalStatus,
    Principal,
)
from tiqora.config import Settings
from tiqora.itsm.change.approval_port import (
    APPROVAL_KEY,
    ApprovalSnapshot,
    EngineApprovalPort,
    set_approval_port_for_tests,
)
from tiqora.itsm.change.models import TiqoraItsmCab, TiqoraItsmCabMember, TiqoraItsmChange
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import apply_setup
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.settings_sections.store import save_section_values

_EPOCH = datetime(2026, 1, 1, 8, 0, 0)


@dataclass(frozen=True, slots=True)
class FakeRequest:
    ticket_id: int
    round: int
    title: str
    description: str
    definition: ApprovalDefinition
    actor: Actor
    requester: Principal
    allow_self_approval: bool


@dataclass
class FakeApprovalPort:
    """Deterministic ids (1, 2, ...); tests decide the outcome with ``decide``."""

    snapshots: dict[int, ApprovalSnapshot] = field(default_factory=dict)
    requests: list[FakeRequest] = field(default_factory=list)
    auto_decisions: list[tuple[int, int]] = field(default_factory=list)  # (ticket_id, round)
    cancels: list[tuple[int, str]] = field(default_factory=list)  # (approval_id, reason)
    #: when set, ``request`` answers decided at once with this reason (e.g. "no_approvers")
    reject_at_once: str | None = None
    _next_id: int = 1

    def _new(
        self, *, ticket_id: int, round: int, status: ApprovalStatus, reason: str | None
    ) -> ApprovalSnapshot:
        approval_id = self._next_id
        self._next_id += 1
        created = _EPOCH + timedelta(minutes=approval_id)
        snap = ApprovalSnapshot(
            approval_id=approval_id,
            ticket_id=ticket_id,
            approval_key=APPROVAL_KEY,
            status=status,
            reason=reason,
            current_stage=1 if status == "pending" else None,
            created_at=created,
            decided_at=None if status == "pending" else created,
            round=round,
        )
        self.snapshots[approval_id] = snap
        return snap

    async def request(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        round: int,
        title: str,
        description: str,
        definition: ApprovalDefinition,
        actor: Actor,
        requester: Principal,
        allow_self_approval: bool,
    ) -> ApprovalSnapshot:
        self.requests.append(
            FakeRequest(
                ticket_id,
                round,
                title,
                description,
                definition,
                actor,
                requester,
                allow_self_approval,
            )
        )
        if self.reject_at_once is not None:
            return self._new(
                ticket_id=ticket_id, round=round, status="rejected", reason=self.reject_at_once
            )
        return self._new(ticket_id=ticket_id, round=round, status="pending", reason=None)

    async def auto_decision(
        self, session: AsyncSession, *, ticket_id: int, round: int, title: str, actor: Actor
    ) -> ApprovalSnapshot:
        self.auto_decisions.append((ticket_id, round))
        return self._new(ticket_id=ticket_id, round=round, status="approved", reason="auto")

    async def cancel(
        self, session: AsyncSession, *, approval_id: int, actor: Actor, reason: str
    ) -> None:
        snap = self.snapshots.get(approval_id)
        self.cancels.append((approval_id, reason))
        if snap is not None and snap.status == "pending":
            self.snapshots[approval_id] = replace(
                snap,
                status="cancelled",
                reason=reason,
                current_stage=None,
                decided_at=snap.created_at,
            )

    async def snapshot(self, session: AsyncSession, *, approval_id: int) -> ApprovalSnapshot | None:
        return self.snapshots.get(approval_id)

    def decide(
        self, approval_id: int, status: ApprovalStatus, reason: str | None = None
    ) -> ApprovalSnapshot:
        """Test helper: the engine reached a final status (pending ones only)."""
        snap = self.snapshots[approval_id]
        if snap.status != "pending":
            return snap
        decided = replace(
            snap, status=status, reason=reason, current_stage=None, decided_at=snap.created_at
        )
        self.snapshots[approval_id] = decided
        return decided


@pytest.fixture
def use_fake_approval_port() -> Iterator[FakeApprovalPort]:
    port = FakeApprovalPort()
    set_approval_port_for_tests(port)
    try:
        yield port
    finally:
        set_approval_port_for_tests(None)


# --- the real-engine switch (see the module docstring) ----------------------------------------

REAL_APPROVALS: Final = os.environ.get("TIQORA_TEST_REAL_APPROVALS") == "1"


@dataclass
class RecordingEnginePort(EngineApprovalPort):
    """The real engine adapter, with the calls recorded the way ``FakeApprovalPort`` does."""

    requests: list[FakeRequest] = field(default_factory=list)
    auto_decisions: list[tuple[int, int]] = field(default_factory=list)
    cancels: list[tuple[int, str]] = field(default_factory=list)

    async def request(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        round: int,
        title: str,
        description: str,
        definition: ApprovalDefinition,
        actor: Actor,
        requester: Principal,
        allow_self_approval: bool,
    ) -> ApprovalSnapshot:
        self.requests.append(
            FakeRequest(
                ticket_id,
                round,
                title,
                description,
                definition,
                actor,
                requester,
                allow_self_approval,
            )
        )
        return await super().request(
            session,
            ticket_id=ticket_id,
            round=round,
            title=title,
            description=description,
            definition=definition,
            actor=actor,
            requester=requester,
            allow_self_approval=allow_self_approval,
        )

    async def auto_decision(
        self, session: AsyncSession, *, ticket_id: int, round: int, title: str, actor: Actor
    ) -> ApprovalSnapshot:
        self.auto_decisions.append((ticket_id, round))
        return await super().auto_decision(
            session, ticket_id=ticket_id, round=round, title=title, actor=actor
        )

    async def cancel(
        self, session: AsyncSession, *, approval_id: int, actor: Actor, reason: str
    ) -> None:
        self.cancels.append((approval_id, reason))
        await super().cancel(session, approval_id=approval_id, actor=actor, reason=reason)


@contextmanager
def approval_port_for() -> Iterator[Any]:
    """The test port of the port fixtures: the fake, or the recording engine adapter under
    ``TIQORA_TEST_REAL_APPROVALS=1``. Typed ``Any``: tests annotate it as the fake."""
    port: FakeApprovalPort | RecordingEnginePort = (
        RecordingEnginePort() if REAL_APPROVALS else FakeApprovalPort()
    )
    set_approval_port_for_tests(port)
    try:
        yield port
    finally:
        set_approval_port_for_tests(None)


def require_fake(port: object) -> FakeApprovalPort:
    """Skips the test under the real-engine switch: it inspects or steers the fake's state."""
    if not isinstance(port, FakeApprovalPort):
        pytest.skip("inspects FakeApprovalPort state (TIQORA_TEST_REAL_APPROVALS=1)")
    return port


async def settle(
    session: AsyncSession, port: object, approval_id: int, status: str, reason: str | None
) -> None:
    """The approval reaches *status* (pending sets only): the fake's ``decide``, or the engine
    through votes of its approvers / a cancel. Reasons the engine sets itself (``timeout``,
    ``no_approvers``) cannot be produced by votes; the engine set is then left as it is."""
    if isinstance(port, FakeApprovalPort):
        if approval_id in port.snapshots:
            port.decide(approval_id, status, reason)  # type: ignore[arg-type]
        return
    if reason in ("timeout", "no_approvers"):
        return
    info = await approval_service.get_info(session, approval_id)
    if info.status != "pending":
        return
    if status == "approved":
        await approve_all(session, approval_id)
    elif status == "rejected":
        while (await approval_service.get_info(session, approval_id)).status == "pending":
            await reject_first(session, approval_id)
    else:
        await approval_service.cancel_approval(
            session, approval_id, actor=Actor.system(), check_permission=False
        )


# --- Tiqora-side change helpers --------------------------------------------------------------


async def make_change(
    session: AsyncSession,
    ticket_id: int,
    *,
    change_type: str = "normal",
    phase: str = "draft",
    user_id: int = 1,
    **fields: Any,
) -> TiqoraItsmChange:
    """A ``tiqora_itsm_change`` row for an existing ticket (flushed, not committed).

    Bypasses the service on purpose: tests of later phases start from a given phase without
    walking the phase machine.
    """
    now = datetime(2026, 10, 1, 8, 0, 0)
    values: dict[str, Any] = {
        "approval_round": 0,
        "created_at": now,
        "created_by": user_id,
        "updated_at": now,
        "updated_by": user_id,
        **fields,
    }
    row = TiqoraItsmChange(ticket_id=ticket_id, change_type=change_type, phase=phase, **values)
    session.add(row)
    await session.flush()
    return row


async def make_cab(
    session: AsyncSession,
    *,
    cab_id: int,
    rule: str = "any",
    members: Sequence[tuple[str, str]] = (),
    quorum: int | None = None,
    name: str | None = None,
    valid: bool = True,
) -> TiqoraItsmCab:
    """A CAB board with members as engine approver entries ``(kind, ref)`` (flushed, not
    committed). Seed band of Task 12: ids 947000-947099."""
    now = datetime(2026, 10, 1, 8, 0, 0)
    cab = TiqoraItsmCab(
        id=cab_id,
        name=name or f"chg94 CAB {cab_id}",
        rule=rule,
        quorum=quorum,
        valid=valid,
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    session.add(cab)
    await session.flush()
    for kind, ref in members:
        session.add(TiqoraItsmCabMember(cab_id=cab_id, member_kind=kind, member_ref=ref))
    await session.flush()
    return cab


# --- the service/API world -------------------------------------------------------------------

URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]
APP_SETTINGS = Settings(environment="test")
_T = datetime(2026, 10, 1)
#: seed band of Task 5 (946300-946399)
U_FULL, U_CREATE, U_RO, U_NONE, U_MANAGER = 946301, 946302, 946303, 946304, 946305
G_A, G_B, G_MANAGER = 946310, 946311, 946312
Q_A, Q_B = 946320, 946321
SERVICE, SERVICE_B = 946330, 946331
MANAGER_GROUP = "chg94-managers"
_COUNTER_TABLES = ("ticket_number_counter",)


@dataclass
class ChangeWorld:
    """One rolled-back session on a plain Znuny database in Mode B with the change setup applied.

    Users: ``U_FULL`` rw on queue ``Q_A``; ``U_CREATE`` create+ro only; ``U_RO`` ro only;
    ``U_NONE`` nothing; ``U_MANAGER`` rw in the change manager group. Queue ``Q_B`` is out of reach
    for everybody but admin (user 1). Nothing is committed, so the strict leak check finds nothing
    except the ticket-number counter, which commits in its own session and is removed afterwards.
    """

    session: AsyncSession
    url: str
    factory: async_sessionmaker[AsyncSession]


async def _seed_access(session: AsyncSession) -> None:
    for uid, login in (
        (U_FULL, "chg94-full"),
        (U_CREATE, "chg94-create"),
        (U_RO, "chg94-ro"),
        (U_NONE, "chg94-none"),
        (U_MANAGER, "chg94-manager"),
    ):
        await session.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, :l, 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
            ),
            {"i": uid, "l": login, "t": _T},
        )
    for gid, name in ((G_A, "chg94-a"), (G_B, "chg94-b"), (G_MANAGER, MANAGER_GROUP)):
        await session.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, "t": _T},
        )
    grants = (
        (U_FULL, G_A, ("ro", "rw")),
        (U_CREATE, G_A, ("ro", "create")),
        (U_RO, G_A, ("ro",)),
        (U_MANAGER, G_A, ("ro", "rw")),
        (U_MANAGER, G_MANAGER, ("rw",)),
    )
    for uid, gid, keys in grants:
        for key in keys:
            await session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": _T},
            )
    for qid, gid in ((Q_A, G_A), (Q_B, G_B)):
        await session.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": f"chg94-q{qid}", "g": gid, "t": _T},
        )
    for sid in (SERVICE, SERVICE_B):
        await session.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": sid, "n": f"chg94-service-{sid}", "t": _T},
        )


async def _seed_world(session: AsyncSession, *, setup: bool, manager_group: bool) -> None:
    await _seed_access(session)
    if setup:
        await apply_setup(session, ItsmSettings(), groups={"change"}, user_id=1)
    if manager_group:
        await save_section_values(
            session,
            ITSM_CHANGE_SECTION,
            {"manager_group": MANAGER_GROUP},
            secret_key=APP_SETTINGS.secret_key,
        )


@asynccontextmanager
async def change_world(
    request: pytest.FixtureRequest,
    url_fixture: str,
    *,
    mode: str | None = "tiqora",
    setup: bool = True,
    manager_group: bool = True,
    savepoints: bool = False,
) -> AsyncIterator[ChangeWorld]:
    """The world; with *savepoints* the session sits in an outer connection transaction
    (``join_transaction_mode="create_savepoint"``) and the seed is committed into it, so code
    that commits or rolls back itself (the approval sweep, the hook job) touches only its
    savepoint. Without it ``commit`` is a ``flush`` and a ``rollback`` would drop the seed."""
    url = plain_url(request, url_fixture)
    marks = snapshot_marks(url, _COUNTER_TABLES)
    store(url, "itsm.mode", mode)
    clear_detect_cache()
    engine, factory = make_factory(url)
    try:
        if savepoints:
            async with engine.connect() as conn:
                outer = await conn.begin()
                bound = async_sessionmaker(
                    conn,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    async with bound() as session:
                        await _seed_world(session, setup=setup, manager_group=manager_group)
                        await session.commit()
                        yield ChangeWorld(session, url, factory)
                finally:
                    await outer.rollback()
        else:
            async with factory() as session:
                try:
                    await _seed_world(session, setup=setup, manager_group=manager_group)
                    session.commit = session.flush  # type: ignore[method-assign]
                    yield ChangeWorld(session, url, factory)
                finally:
                    await session.rollback()
    finally:
        await engine.dispose()
        delete_above_marks(url, marks)
        clear_detect_cache()
