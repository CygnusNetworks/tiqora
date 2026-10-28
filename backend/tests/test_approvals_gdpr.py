"""GDPR erasure of approval records (Task 18) on MariaDB and PostgreSQL.

The erased customer ``appr97-kunde2-<tag>@example.org`` owns ticket ``T_E`` (erased) and is a
customer approver and requester on ticket ``T1`` (971981, another customer's ticket; no ticket
row needed, the approval tables have no FK to ``ticket``; one test makes it a real ticket of a
second customer who is erased later). The approval set of ``T_E`` is removed in every mode; on
``T1`` every column naming the customer is rewritten, the vote comment and the customer's own
request texts blanked, the customer's link token deleted; a template naming the customer loses
the entry. Rollback restores all of it, and skips rows that were deleted since.
Seed band 971980-971999.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.test_gdpr_erasure import (
    NOW,
    _factory,
    _insert_company,
    _insert_customer,
    _insert_ticket,
    _is_mysql,
    _seed_tiqora_tables,
    _tiqora_tables,  # noqa: F401  (autouse: creates the tiqora_* tables)
)
from tiqora.approvals import templates
from tiqora.approvals.contract import ApprovalDefinition, ApprovalError
from tiqora.approvals.models import APPROVAL_TICKET_TABLES
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.gdpr.erasure import build_erasure_preview_for_ids, rollback_job, run_erasure

pytestmark = pytest.mark.db

_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
_VARIANTS = ["anonymize", "delete", "delete_tickets"]

T1 = 971981  # the other customer's ticket (not erased)
# request on the erased ticket: approval, step, approver, vote, token, event
A_E, S_E, AP_E, V_E, TK_E, EV_E = 971982, 971983, 971984, 971985, 971986, 971987
# request on T1: the customer approved it; an agent approver stays untouched
A_O, S_O, AP_O, V_O, TK_O, EV_O = 971988, 971989, 971990, 971991, 971992, 971993
AP_AGENT, TK_AGENT = 971994, 971995
TPL = 971996
_COMMENT = "Passt so, Gruss kunde2"
#: every tiqora_approval* table, straight from the ORM metadata (not a hand-picked list)
_ALL_APPROVAL_TABLES = sorted(
    t for t in TiqoraBase.metadata.tables if t.startswith("tiqora_approval")
)
_MARK_TABLES = (
    "tiqora_gdpr_backup",
    "tiqora_gdpr_job",
    "tiqora_gdpr_audit",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
    "ticket_history",
    "article",
    "ticket",
    "customer_user",
)


@dataclass(frozen=True)
class _Seed:
    login: str
    customer_user_id: int
    erased_ticket: int
    other_ticket: int = T1
    owner_customer_user_id: int | None = None  # the other ticket's customer, if seeded

    @property
    def tickets(self) -> list[int]:
        return [self.erased_ticket, self.other_ticket]


def _tag(url_fixture: str, variant: str) -> str:
    return f"{url_fixture[:2]}{variant.replace('_', '')}"


def _run_kwargs(variant: str) -> dict[str, Any]:
    return {
        "mode": "anonymize" if variant == "anonymize" else "delete",
        "delete_tickets": variant == "delete_tickets",
    }


async def _insert_set(
    session: AsyncSession,
    *,
    ids: tuple[int, int, int, int, int, int],
    ticket_id: int,
    requester: str,
    customer: str | None,
    spec_ref: str,
    title: str = "appr97 Zugang",
    description: str = "appr97 Beschreibung",
    template_id: int | None = None,
) -> None:
    """One request with one step, one approver, its vote, its link token and one audit row.

    ``customer`` set: the approver is that customer (``source_ref`` spelled ``spec_ref``), the
    vote is theirs with a comment and the audit row names them as actor.
    """
    approval_id, step_id, approver_id, vote_id, token_id, event_id = ids
    principal = f"customer:{customer}" if customer else "agent:1"
    spec = {
        "name": "Fachbereich",
        "stage": 1,
        "rule": "any",
        "quorum": None,
        "approvers": [{"kind": "customer_user", "ref": spec_ref}, {"kind": "user", "ref": "1"}],
        "due_hours": None,
        "on_timeout": "notify",
        "escalation": [],
        "on_no_approver": "reject",
        "allow_delegation": True,
        "allow_email": True,
    }
    params = {"t": ticket_id, "n": NOW}
    await session.execute(
        text(
            "INSERT INTO tiqora_approval (id, ticket_id, subject_type, approval_key, title,"
            " description, template_id, status, current_stage, requester, created_by,"
            " created_at)"
            " VALUES (:id, :t, 'ticket', 'default', :ti, :de, :tpl, 'pending', 1, :r, 1, :n)"
        ),
        {
            **params,
            "id": approval_id,
            "r": requester,
            "ti": title,
            "de": description,
            "tpl": template_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_step (id, approval_id, ticket_id, position, stage, name,"
            " rule, status, spec, activated_at)"
            " VALUES (:id, :a, :t, 1, 1, 'Fachbereich', 'any', 'active', :spec, :n)"
        ),
        {**params, "id": step_id, "a": approval_id, "spec": json.dumps(spec)},
    )
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_approver (id, approval_id, step_id, ticket_id, principal,"
            " user_id, customer_login, source_kind, source_ref, status, added_at)"
            " VALUES (:id, :a, :s, :t, :p, :u, :c, :k, :ref, 'approved', :n)"
        ),
        {
            **params,
            "id": approver_id,
            "a": approval_id,
            "s": step_id,
            "p": principal,
            "u": None if customer else 1,
            "c": customer,
            "k": "customer_user" if customer else "user",
            "ref": spec_ref if customer else "1",
        },
    )
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_vote (id, approval_id, step_id, approver_id, ticket_id,"
            " decision, comment, decided_by, channel, decided_at)"
            " VALUES (:id, :a, :s, :ap, :t, 'approved', :c, :by, :ch, :n)"
        ),
        {
            **params,
            "id": vote_id,
            "a": approval_id,
            "s": step_id,
            "ap": approver_id,
            "c": _COMMENT if customer else "ok",
            "by": principal,
            "ch": "portal" if customer else "ui",
        },
    )
    await _insert_token(session, token_id, approval_id, approver_id, ticket_id)
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_event (id, approval_id, ticket_id, at, action, actor,"
            " step_id, approver_id, channel, detail)"
            " VALUES (:id, :a, :t, :n, 'voted', :actor, :s, :ap, :ch, :d)"
        ),
        {
            **params,
            "id": event_id,
            "a": approval_id,
            "s": step_id,
            "ap": approver_id,
            "actor": principal,
            "ch": "portal" if customer else "ui",
            "d": json.dumps({"decision": "approved", "on_behalf": False}),
        },
    )


async def _insert_token(
    session: AsyncSession, token_id: int, approval_id: int, approver_id: int, ticket_id: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_token (id, approval_id, approver_id, ticket_id,"
            " token_hash, created_at, expires_at)"
            " VALUES (:id, :a, :ap, :t, :h, :n, :n)"
        ),
        {
            "id": token_id,
            "a": approval_id,
            "ap": approver_id,
            "t": ticket_id,
            "h": f"{token_id:064d}",
            "n": NOW,
        },
    )


def _template_definition(login: str) -> str:
    step = {
        "rule": "any",
        "quorum": None,
        "due_hours": None,
        "on_timeout": "notify",
        "escalation": [],
        "on_no_approver": "reject",
        "allow_delegation": True,
        "allow_email": True,
    }
    return json.dumps(
        {
            "steps": [
                {
                    **step,
                    "name": "Fachbereich",
                    "stage": 1,
                    "approvers": [
                        {"kind": "customer_user", "ref": login.upper()},
                        {"kind": "user", "ref": "1"},
                    ],
                    "due_hours": 24,
                    "on_timeout": "escalate",
                    "escalation": [{"kind": "customer_user", "ref": login}],
                },
                {
                    **step,
                    "name": "Kunde",
                    "stage": 2,
                    "approvers": [{"kind": "customer_user", "ref": login}],
                },
            ]
        }
    )


async def _seed(
    factory: async_sessionmaker[AsyncSession], url: str, tag: str, *, owned_other: bool = False
) -> _Seed:
    login = f"appr97-kunde2-{tag}@example.org"
    company = f"APPR97-GDPR-{tag.upper()}"
    async with factory() as session:
        await _seed_tiqora_tables(session, mysql=_is_mysql(url))
        await _insert_company(session, customer_id=company, name=f"appr97 Firma {tag}")
        cuid = await _insert_customer(session, login=login, customer_id=company)
        erased = await _insert_ticket(
            session, customer_user_id=login, customer_id=company, tn=f"2026AP{tag}{cuid}"
        )
        other, owner_id = T1, None
        if owned_other:
            owner = f"appr97-owner-{tag}@example.org"
            owner_company = f"APPR97-GDPR-O{tag.upper()}"
            await _insert_company(session, customer_id=owner_company, name=f"appr97 O {tag}")
            owner_id = await _insert_customer(session, login=owner, customer_id=owner_company)
            other = await _insert_ticket(
                session,
                customer_user_id=owner,
                customer_id=owner_company,
                tn=f"2026AO{tag}{owner_id}",
            )
        await _insert_template(session, tag, login)
        # the erased customer's own ticket: an agent-only request from a template (deleted whole)
        await _insert_set(
            session,
            ids=(A_E, S_E, AP_E, V_E, TK_E, EV_E),
            ticket_id=erased,
            requester="agent:1",
            customer=None,
            spec_ref="1",
            template_id=TPL,
        )
        # another ticket: the customer requested (title and description name them) and approved,
        # spelled in another case in the entry (the stored principal is the canonical login)
        await _insert_set(
            session,
            ids=(A_O, S_O, AP_O, V_O, TK_O, EV_O),
            ticket_id=other,
            requester=f"customer:{login}",
            customer=login,
            spec_ref=login.upper(),
            title=f"Zugang fuer {login}",
            description=f"Bitte {login.upper()} freischalten",
        )
        await session.execute(
            text(
                "INSERT INTO tiqora_approval_approver (id, approval_id, step_id, ticket_id,"
                " principal, user_id, source_kind, source_ref, status, added_at)"
                " VALUES (:id, :a, :s, :t, 'agent:1', 1, 'user', '1', 'pending', :n)"
            ),
            {"id": AP_AGENT, "a": A_O, "s": S_O, "t": other, "n": NOW},
        )
        await _insert_token(session, TK_AGENT, A_O, AP_AGENT, other)
        await session.commit()
    return _Seed(
        login=login,
        customer_user_id=cuid,
        erased_ticket=erased,
        other_ticket=other,
        owner_customer_user_id=owner_id,
    )


async def _insert_template(session: AsyncSession, tag: str, login: str) -> None:
    await session.execute(
        text(
            "INSERT INTO tiqora_approval_template (id, template_key, name, subject_types,"
            " definition, origin, valid, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :k, 'appr97 Vorlage', '[]', :d, 'admin', :v, :n, 1, :n, 1)"
        ),
        {
            "id": TPL,
            "k": f"appr97-gdpr-{tag}",
            "d": _template_definition(login),
            "v": True,
            "n": NOW,
        },
    )


async def _rows(
    factory: async_sessionmaker[AsyncSession], ticket_ids: list[int]
) -> dict[str, list[dict[str, Any]]]:
    """Every row of every ``tiqora_approval*`` table this test owns, all columns."""
    out: dict[str, list[dict[str, Any]]] = {}
    async with factory() as session:
        for table in _ALL_APPROVAL_TABLES:
            columns = TiqoraBase.metadata.tables[table].columns
            if "ticket_id" in columns:
                stmt = text(f"SELECT * FROM {table} WHERE ticket_id IN :ids").bindparams(  # noqa: S608
                    bindparam("ids", expanding=True)
                )
                params: dict[str, Any] = {"ids": ticket_ids}
            elif "id" in columns:
                stmt = text(f"SELECT * FROM {table} WHERE id BETWEEN 971980 AND 971999")  # noqa: S608
                params = {}
            else:  # list members: keyed by list id
                stmt = text(f"SELECT * FROM {table} WHERE list_id BETWEEN 971980 AND 971999")  # noqa: S608
                params = {}
            result = await session.execute(stmt, params)
            out[table] = sorted(
                (dict(r) for r in result.mappings()), key=lambda r: json.dumps(r, default=str)
            )
    return out


def _ids(rows: dict[str, list[dict[str, Any]]], table: str) -> set[int]:
    return {int(r["id"]) for r in rows[table]}


async def _cleanup(factory: async_sessionmaker[AsyncSession], ticket_ids: list[int]) -> None:
    async with factory() as session:
        for table in APPROVAL_TICKET_TABLES:
            await session.execute(
                text(f"DELETE FROM {table} WHERE ticket_id IN :ids").bindparams(  # noqa: S608
                    bindparam("ids", expanding=True)
                ),
                {"ids": ticket_ids},
            )
        await session.execute(
            text("DELETE FROM tiqora_approval_template WHERE id = :i"), {"i": TPL}
        )
        await session.commit()


@asynccontextmanager
async def _world(
    url_fixture: str, request: pytest.FixtureRequest, variant: str, *, owned_other: bool = False
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], _Seed]]:
    url = str(request.getfixturevalue(url_fixture))
    marks = snapshot_marks(url, _MARK_TABLES)
    engine, factory = await _factory(url)
    tag = _tag(url_fixture, variant)
    seed: _Seed | None = None
    try:
        seed = await _seed(factory, url, tag, owned_other=owned_other)
        yield factory, seed
    finally:
        tickets = seed.tickets if seed is not None else [T1]
        await _cleanup(factory, tickets)
        await engine.dispose()
        delete_above_marks(
            url,
            marks,
            first=[
                (
                    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id > :m",
                    {"m": marks["ticket"]},
                )
            ],
        )
        sync_engine = create_engine(url)
        with sync_engine.begin() as conn:
            for pattern in ("appr97-kunde2-%", "appr97-owner-%"):
                conn.execute(
                    text("DELETE FROM customer_user_customer WHERE user_id LIKE :p"),
                    {"p": pattern},
                )
            conn.execute(
                text("DELETE FROM customer_company WHERE customer_id LIKE :p"),
                {"p": "APPR97-GDPR-%"},
            )
        sync_engine.dispose()


async def _erase(
    factory: async_sessionmaker[AsyncSession], seed: _Seed, variant: str
) -> tuple[int, dict[str, int]]:
    result = await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[seed.customer_user_id],
        seed=18,
        force_parallel=True,
        actor="test",
        **_run_kwargs(variant),
    )
    return result.job_id, result.counts


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_ticket_erasure_removes_approvals(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Anonymise: the whole approval set of the erased ticket goes (backed up), T1's stays."""
    async with _world(url_fixture, request, "anonymize") as (factory, seed):
        _, counts = await _erase(factory, seed, "anonymize")

        assert {t: counts[t] for t in APPROVAL_TICKET_TABLES} == {
            "tiqora_approval_token": 2,  # the erased ticket's token + the customer's token on T1
            "tiqora_approval_vote": 1,
            "tiqora_approval_approver": 1,
            "tiqora_approval_event": 1,
            "tiqora_approval_step": 1,
            "tiqora_approval": 1,
        }
        rows = await _rows(factory, seed.tickets)
        assert _ids(rows, "tiqora_approval") == {A_O}
        assert _ids(rows, "tiqora_approval_step") == {S_O}
        assert _ids(rows, "tiqora_approval_approver") == {AP_O, AP_AGENT}
        assert _ids(rows, "tiqora_approval_vote") == {V_O}
        assert _ids(rows, "tiqora_approval_token") == {TK_AGENT}
        assert _ids(rows, "tiqora_approval_event") == {EV_O}


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_ticket_hard_delete_removes_approvals(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """delete_tickets: the set goes through ``_TICKET_DELETE_ORDER`` before the ticket row."""
    async with _world(url_fixture, request, "delete_tickets") as (factory, seed):
        _, counts = await _erase(factory, seed, "delete_tickets")

        assert counts["tickets_deleted"] == 1
        assert {t: counts[t] for t in APPROVAL_TICKET_TABLES} == {
            "tiqora_approval_token": 2,
            "tiqora_approval_vote": 1,
            "tiqora_approval_approver": 1,
            "tiqora_approval_event": 1,
            "tiqora_approval_step": 1,
            "tiqora_approval": 1,
        }
        rows = await _rows(factory, seed.tickets)
        erased_rows = {
            table: [r for r in rows[table] if int(r["ticket_id"]) == seed.erased_ticket]
            for table in APPROVAL_TICKET_TABLES
        }
        assert erased_rows == {table: [] for table in APPROVAL_TICKET_TABLES}
        assert _ids(rows, "tiqora_approval") == {A_O}
        assert _ids(rows, "tiqora_approval_token") == {TK_AGENT}


@pytest.mark.parametrize("url_fixture", _URLS)
@pytest.mark.parametrize("variant", _VARIANTS)
async def test_rollback_restores_approvals(
    url_fixture: str, variant: str, request: pytest.FixtureRequest
) -> None:
    """Every approval row (deleted set, rewritten rows, deleted tokens, template) comes back
    byte-identical, in all three erasure variants."""
    async with _world(url_fixture, request, variant) as (factory, seed):
        before = await _rows(factory, seed.tickets)
        assert len(before["tiqora_approval"]) == 2
        job_id, _ = await _erase(factory, seed, variant)
        assert await _rows(factory, seed.tickets) != before

        await rollback_job(factory, Settings(), job_id, force_parallel=True, actor="test")

        assert await _rows(factory, seed.tickets) == before


@pytest.mark.parametrize("url_fixture", _URLS)
@pytest.mark.parametrize("variant", _VARIANTS)
async def test_customer_erasure_rewrites_other_tickets(
    url_fixture: str, variant: str, request: pytest.FixtureRequest
) -> None:
    """kunde2 approved on T1 (not erased): afterwards no ``kunde2`` in any column of any
    tiqora_approval* row, the vote comment is ``[anonymisiert]``, the customer's link token is
    gone; rollback brings the login and the comment back."""
    async with _world(url_fixture, request, variant) as (factory, seed):
        job_id, counts = await _erase(factory, seed, variant)
        # requester, step spec, approver, vote, event on T1 + the template
        assert counts["tiqora_approval_customer_refs"] == 6

        rows = await _rows(factory, seed.tickets)
        leaks = [
            (table, column, value)
            for table, table_rows in rows.items()
            for row in table_rows
            for column, value in row.items()
            if "kunde2" in str(value).lower()
        ]
        assert leaks == []

        approver = next(r for r in rows["tiqora_approval_approver"] if r["id"] == AP_O)
        new_login = str(approver["customer_login"])
        assert new_login and new_login != seed.login
        if variant == "anonymize":
            async with factory() as session:
                stored = (
                    await session.execute(
                        text("SELECT login FROM customer_user WHERE id = :i"),
                        {"i": seed.customer_user_id},
                    )
                ).scalar_one()
            assert new_login == stored
        elif variant == "delete":
            async with factory() as session:
                token = (
                    await session.execute(
                        text("SELECT customer_user_id FROM ticket WHERE id = :i"),
                        {"i": seed.erased_ticket},
                    )
                ).scalar_one()
            assert new_login == token  # the same stable token the ticket got
        new_principal = f"customer:{new_login}"
        assert approver["principal"] == new_principal
        assert approver["source_ref"] == new_login
        agent_row = next(r for r in rows["tiqora_approval_approver"] if r["id"] == AP_AGENT)
        assert (agent_row["principal"], agent_row["source_ref"]) == ("agent:1", "1")
        (vote,) = rows["tiqora_approval_vote"]
        assert (vote["decided_by"], vote["comment"]) == (new_principal, "[anonymisiert]")
        (request_row,) = rows["tiqora_approval"]
        assert (request_row["requester"], request_row["title"], request_row["description"]) == (
            new_principal,
            "[anonymisiert]",
            "[anonymisiert]",
        )
        (event,) = rows["tiqora_approval_event"]
        assert event["actor"] == new_principal
        (step,) = rows["tiqora_approval_step"]
        assert json.loads(step["spec"])["approvers"] == [
            {"kind": "customer_user", "ref": new_login},
            {"kind": "user", "ref": "1"},
        ]
        assert _ids(rows, "tiqora_approval_token") == {TK_AGENT}
        (template,) = rows["tiqora_approval_template"]
        definition = ApprovalDefinition.model_validate_json(template["definition"])
        assert [[(e.kind, e.ref) for e in s.approvers] for s in definition.steps] == [
            [("user", "1")],  # entry dropped
            [("customer_user", "[anonymisiert]")],  # last entry: placeholder keeps it valid
        ]
        # the emptied escalation is not padded; the step stops escalating
        assert [(s.escalation, s.on_timeout) for s in definition.steps] == [
            ([], "notify"),
            ([], "notify"),
        ]
        assert template["change_time"] != NOW

        await rollback_job(factory, Settings(), job_id, force_parallel=True, actor="test")

        rows = await _rows(factory, seed.tickets)
        approver = next(r for r in rows["tiqora_approval_approver"] if r["id"] == AP_O)
        assert (approver["customer_login"], approver["principal"], approver["source_ref"]) == (
            seed.login,
            f"customer:{seed.login}",
            seed.login.upper(),
        )
        vote = next(r for r in rows["tiqora_approval_vote"] if r["id"] == V_O)
        assert (vote["decided_by"], vote["comment"]) == (f"customer:{seed.login}", _COMMENT)
        request_row = next(r for r in rows["tiqora_approval"] if r["id"] == A_O)
        assert (request_row["title"], request_row["description"]) == (
            f"Zugang fuer {seed.login}",
            f"Bitte {seed.login.upper()} freischalten",
        )
        assert _ids(rows, "tiqora_approval_token") == {TK_E, TK_O, TK_AGENT}
        (template,) = rows["tiqora_approval_template"]
        assert (template["definition"], template["change_time"]) == (
            _template_definition(seed.login),
            NOW,
        )


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_preview_counts_approvals(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """The preview counts what run_erasure then removes and rewrites, under the same keys."""
    async with _world(url_fixture, request, "preview") as (factory, seed):
        async with factory() as session:
            preview = await build_erasure_preview_for_ids(
                session, [seed.customer_user_id], "anonymize"
            )
            hard = await build_erasure_preview_for_ids(
                session, [seed.customer_user_id], "delete", delete_tickets=True
            )
        expected = {
            "tiqora_approval_token": 2,
            "tiqora_approval_vote": 1,
            "tiqora_approval_approver": 1,
            "tiqora_approval_event": 1,
            "tiqora_approval_step": 1,
            "tiqora_approval": 1,
            "tiqora_approval_customer_refs": 6,
        }
        assert {k: preview.counts[k] for k in expected} == expected
        assert {k: hard.counts[k] for k in expected} == expected
        assert set(APPROVAL_TICKET_TABLES) <= set(hard.tables_deleted)

        _, counts = await _erase(factory, seed, "anonymize")
        assert {k: counts[k] for k in expected} == expected


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_stale_template_save_after_erasure_is_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """An editor opened before the erasure saves the old definition with the change_time it
    loaded: 409 ``template_changed``, the erased login does not come back (B-M8)."""
    async with _world(url_fixture, request, "anonymize") as (factory, seed):
        async with factory() as session:
            loaded = (await templates.get_template(session, TPL)).change_time
        assert loaded == NOW
        await _erase(factory, seed, "anonymize")

        async with factory() as session:
            with pytest.raises(ApprovalError) as err:
                await templates.update_template(
                    session,
                    TPL,
                    actor_id=1,
                    definition=json.loads(_template_definition(seed.login)),
                    expected_change_time=loaded,
                )
            await session.rollback()
        assert (err.value.code, err.value.http_status) == ("template_changed", 409)

        (template,) = (await _rows(factory, seed.tickets))["tiqora_approval_template"]
        assert seed.login.lower() not in str(template["definition"]).lower()
        assert template["change_time"] != NOW


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_rollback_after_template_deleted(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The admin deletes the template the erasure warned about; the rollback still restores
    everything else (the template's own backup is skipped, the request comes back without it)."""
    async with _world(url_fixture, request, "tpldel") as (factory, seed):
        before = await _rows(factory, seed.tickets)
        job_id, _ = await _erase(factory, seed, "anonymize")
        async with factory() as session:
            await session.execute(
                text("DELETE FROM tiqora_approval_template WHERE id = :i"), {"i": TPL}
            )
            await session.commit()

        result = await rollback_job(factory, Settings(), job_id, force_parallel=True, actor="test")

        assert result["skipped_rows"] == 1  # the template's backup, counted (Task 18)
        after = await _rows(factory, seed.tickets)
        assert after["tiqora_approval_template"] == []
        expected_requests = [
            {**r, "template_id": None} if r["id"] == A_E else r for r in before["tiqora_approval"]
        ]
        assert sorted(after["tiqora_approval"], key=lambda r: r["id"]) == sorted(
            expected_requests, key=lambda r: r["id"]
        )
        for table in APPROVAL_TICKET_TABLES:
            if table != "tiqora_approval":
                assert after[table] == before[table], table


@pytest.mark.parametrize("url_fixture", _URLS)
async def test_rollback_after_later_erasure_of_other_ticket(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A later erasure of the other ticket's customer deletes that approval set; rolling back the
    first job skips the rows that are gone and restores the rest."""
    async with _world(url_fixture, request, "later", owned_other=True) as (factory, seed):
        assert seed.owner_customer_user_id is not None
        before = await _rows(factory, seed.tickets)
        first_job, _ = await _erase(factory, seed, "anonymize")
        second = await run_erasure(
            factory,
            Settings(),
            customer_user_ids=[seed.owner_customer_user_id],
            mode="anonymize",
            seed=19,
            force_parallel=True,
            actor="test",
        )
        assert second.counts["tiqora_approval"] == 1  # the other ticket's set

        result = await rollback_job(
            factory, Settings(), first_job, force_parallel=True, actor="test"
        )

        assert result["skipped_rows"] > 0  # the other ticket's rewritten rows are gone
        after = await _rows(factory, seed.tickets)
        for table in APPROVAL_TICKET_TABLES:
            assert after[table] == [
                r for r in before[table] if int(r["ticket_id"]) == seed.erased_ticket
            ], table
        assert after["tiqora_approval_template"] == before["tiqora_approval_template"]
