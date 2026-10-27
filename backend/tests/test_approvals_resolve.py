"""Approver resolution (A6 ``resolve``). Own rows 970750-970849."""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    AG6,
    FIRMA,
    GROUP_CAB,
    ROLE,
    T1,
    T2,
    approvals_session,
    installed_registry,
)
from tests.test_itsm_no_cross_join import znuny_tables
from tiqora.approvals import lists
from tiqora.approvals.contract import (
    ApproverEntry,
    ApproverResolver,
    Principal,
    ResolveContext,
)
from tiqora.approvals.resolve import (
    Resolution,
    customer_in_ticket_company,
    resolve_entries,
    ticket_customer_id,
)

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
K1, K2, FREMD = "appr97-k1", "appr97-k2", "appr97-fremd"


def _ctx(ticket_id: int = T1) -> ResolveContext:
    return ResolveContext(
        ticket_id=ticket_id, subject_type="ticket", subject_ref=None, approval_id=0
    )


async def _resolve(
    session: AsyncSession,
    *entries: tuple[str, str],
    excluded: frozenset[str] = frozenset(),
    allow_customers: bool = True,
    ticket_id: int = T1,
    **kwargs: Any,
) -> Resolution:
    return await resolve_entries(
        session,
        [ApproverEntry(kind=k, ref=r) for k, r in entries],  # type: ignore[arg-type]
        ctx=_ctx(ticket_id),
        excluded_principals=excluded,
        allow_customers=allow_customers,
        **kwargs,
    )


def _ids(res: Resolution) -> list[str]:
    return [a.principal.key for a in res.approvers]


def _excluded(res: Resolution) -> list[tuple[str, str]]:
    return [(p.key, reason) for p, reason in res.excluded]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_user_entry(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("user", str(AG1)))
        assert _ids(res) == [f"agent:{AG1}"]
        only = res.approvers[0]
        assert (only.source_kind, only.source_ref, only.source_label) == ("user", str(AG1), None)
        assert res.excluded == [] and res.unresolved == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_user_excluded(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("user", str(AG6)), ("user", "970848"))
        assert res.approvers == []
        assert _excluded(res) == [(f"agent:{AG6}", "invalid"), ("agent:970848", "unknown")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_system_user_excluded(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("user", "1"))
        assert res.approvers == []
        assert _excluded(res) == [("agent:1", "system_user")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_group_entry_expands_rw_members(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("group", str(GROUP_CAB)))
        # AG4 holds rw directly and through the role: listed once; AG7 is ro-only: not an approver
        assert _ids(res) == [f"agent:{AG3}", f"agent:{AG4}", f"agent:{AG5}"]
        assert {(a.source_kind, a.source_ref, a.source_label) for a in res.approvers} == {
            ("group", str(GROUP_CAB), "appr97-cab")
        }
        assert res.excluded == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_role_entry(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("role", str(ROLE)))
        assert _ids(res) == [f"agent:{AG4}"]
        assert (res.approvers[0].source_kind, res.approvers[0].source_label) == (
            "role",
            "appr97-role",
        )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_entry(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        row = await lists.create_list(session, name="appr97 resolve", actor_id=AG1)
        # AG6 became invalid after it was added: simulate with a direct member row
        await session.execute(
            text(
                "INSERT INTO tiqora_approval_list_member (list_id, user_id)"
                " VALUES (:l, :a), (:l, :b)"
            ),
            {"l": row.id, "a": AG1, "b": AG6},
        )
        res = await _resolve(session, ("list", str(row.id)))
        assert _ids(res) == [f"agent:{AG1}"]
        assert (res.approvers[0].source_kind, res.approvers[0].source_label) == (
            "list",
            "appr97 resolve",
        )
        assert _excluded(res) == [(f"agent:{AG6}", "invalid")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_dedupe_first_source_wins(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("user", str(AG4)), ("group", str(GROUP_CAB)))
        assert _ids(res) == [f"agent:{AG4}", f"agent:{AG3}", f"agent:{AG5}"]
        assert res.approvers[0].source_kind == "user"
        assert {a.source_kind for a in res.approvers[1:]} == {"group"}
        override = await _resolve(session, ("user", str(AG4)), source_kind_override="escalation")
        assert override.approvers[0].source_kind == "escalation"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_self_excluded(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(
            session, ("group", str(GROUP_CAB)), excluded=frozenset({f"agent:{AG3}"})
        )
        assert _ids(res) == [f"agent:{AG4}", f"agent:{AG5}"]
        assert _excluded(res) == [(f"agent:{AG3}", "self")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_entry_inside_company(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        assert await ticket_customer_id(session, T1) == FIRMA
        assert await ticket_customer_id(session, T2) is None
        assert await ticket_customer_id(session, 970849) is None
        res = await _resolve(session, ("customer_user", K2))
        assert _ids(res) == [f"customer:{K2}"]
        assert res.approvers[0].source_kind == "customer_user"
        assert await customer_in_ticket_company(session, K2, T1) is True
        assert await customer_in_ticket_company(session, K2, T2) is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_outside_company_excluded(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(session, ("customer_user", FREMD), ("customer_user", "appr97-nope"))
        assert res.approvers == []
        assert _excluded(res) == [
            (f"customer:{FREMD}", "outside_company"),
            ("customer:appr97-nope", "unknown"),
        ]
        assert await customer_in_ticket_company(session, FREMD, T1) is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_via_customer_user_customer(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await session.execute(
            text(
                "INSERT INTO customer_user_customer (user_id, customer_id, create_time, create_by,"
                " change_time, change_by) VALUES (:u, :c, '2026-10-15 08:00:00', 1,"
                " '2026-10-15 08:00:00', 1)"
            ),
            {"u": FREMD, "c": FIRMA},
        )
        res = await _resolve(session, ("customer_user", FREMD))
        assert _ids(res) == [f"customer:{FREMD}"]
        assert await customer_in_ticket_company(session, FREMD, T1) is True


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customers_not_allowed(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(
            session, ("customer_user", K2), ("user", str(AG1)), allow_customers=False
        )
        assert _ids(res) == [f"agent:{AG1}"]
        assert _excluded(res) == [(f"customer:{K2}", "customers_not_allowed")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolver_entry_goes_through_filters(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async def _fake(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
        assert ctx.ticket_id == T1
        return [Principal.agent(AG6), Principal.agent(AG1), Principal.customer(FREMD)]

    fake = ApproverResolver(key="appr97.fake", title_key="appr97.fake", resolve=_fake)
    with installed_registry(fake) as registry:
        async with approvals_session(request, url_fixture) as session:
            res = await _resolve(session, ("resolver", "appr97.fake"), registry=registry)
            assert _ids(res) == [f"agent:{AG1}"]
            assert (res.approvers[0].source_kind, res.approvers[0].source_ref) == (
                "resolver",
                "appr97.fake",
            )
            assert _excluded(res) == [
                (f"agent:{AG6}", "invalid"),
                (f"customer:{FREMD}", "outside_company"),
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_unknown_resolver_and_missing_group_do_not_raise(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    with installed_registry() as registry:
        async with approvals_session(request, url_fixture) as session:
            res = await _resolve(
                session,
                ("resolver", "appr97.nope"),
                ("group", "970845"),
                ("group", "x"),
                ("role", "970846"),
                ("list", "970847"),
                ("user", str(AG1)),
                registry=registry,
            )
            assert _ids(res) == [f"agent:{AG1}"]
            assert [(e.kind, e.ref, reason) for e, reason in res.unresolved] == [
                ("resolver", "appr97.nope", "unknown"),
                ("group", "970845", "unknown"),
                ("group", "x", "unknown"),
                ("role", "970846", "unknown"),
                ("list", "970847", "unknown"),
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_cross_queries_join(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    znuny = znuny_tables()
    async with approvals_session(request, url_fixture) as session:
        row = await lists.create_list(session, name="appr97 spy", member_ids=[AG1], actor_id=AG1)
        seen: list[str] = []
        original = session.execute

        async def _spy(statement: Any, *args: Any, **kwargs: Any) -> Any:
            seen.append(str(statement))
            return await original(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", _spy)
        res = await _resolve(
            session,
            ("list", str(row.id)),
            ("group", str(GROUP_CAB)),
            ("role", str(ROLE)),
            ("customer_user", K2),
        )
        assert AG1 in {a.principal.user_id for a in res.approvers}
        touching = [s for s in seen if "tiqora_approval_list" in s]
        assert touching, "the list entry must have queried tiqora_approval_list*"
        for sql in touching:
            # drop column qualifiers ("tiqora_approval_list.valid"), keep table names
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"\.\w+", "", sql.lower())))
            assert words & znuny == set(), sql


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_customer_excluded(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await session.execute(
            text("UPDATE customer_user SET valid_id = 2 WHERE login = :l"), {"l": K2}
        )
        res = await _resolve(session, ("customer_user", K2))
        assert res.approvers == []
        assert _excluded(res) == [(f"customer:{K2}", "invalid")]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolver_failures_propagate(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async def _db_error(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
        await session.execute(text("SELECT 1 FROM appr97_no_such_table"))
        return []

    async def _not_principals(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
        return [AG1]  # type: ignore[list-item]

    broken = [
        ApproverResolver(key="appr97.dberr", title_key="appr97.dberr", resolve=_db_error),
        ApproverResolver(key="appr97.bad", title_key="appr97.bad", resolve=_not_principals),
    ]
    with installed_registry(*broken) as registry:
        async with approvals_session(request, url_fixture) as session:
            # an infrastructure error must not turn into "no approver" (on_no_approver=approve)
            with pytest.raises(SQLAlchemyError):
                await _resolve(session, ("resolver", "appr97.dberr"), registry=registry)
        async with approvals_session(request, url_fixture) as session:
            with pytest.raises(TypeError, match="expected Principal"):
                await _resolve(session, ("resolver", "appr97.bad"), registry=registry)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_login_case_is_canonical(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        res = await _resolve(
            session,
            ("customer_user", "APPR97-K2"),
            ("customer_user", K2),
            ("customer_user", FREMD.upper()),
        )
        # same customer twice (different case) collapses onto the stored login
        assert _ids(res) == [f"customer:{K2}"]
        assert _excluded(res) == [(f"customer:{FREMD}", "outside_company")]
        res = await _resolve(
            session, ("customer_user", "Appr97-K2"), excluded=frozenset({f"customer:{K2}"})
        )
        assert _excluded(res) == [(f"customer:{K2}", "self")]
        assert await customer_in_ticket_company(session, "APPR97-K2", T1) is True
        assert await customer_in_ticket_company(session, "APPR97-FREMD", T1) is False
        assert await customer_in_ticket_company(session, "appr97-nope", T1) is False
