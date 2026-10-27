"""Approval templates (A5 ``templates``). Own rows 970650-970749."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._approvals_helpers import (
    AG1,
    AG3,
    ROLE,
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tiqora.approvals import lists, templates
from tiqora.approvals.contract import ApprovalDefinition, ApprovalError
from tiqora.jobs.service import utc_now

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


def _definition(*entries: dict[str, str], **step: object) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate(
        {"steps": [{"name": "Step", "approvers": list(entries), **step}]}
    )


USER = _definition({"kind": "user", "ref": str(AG3)})


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_crud_round_trip(url_fixture: str, request: pytest.FixtureRequest) -> None:
    definition = ApprovalDefinition.model_validate(
        {
            "steps": [
                {"name": "Prüfung", "approvers": [{"kind": "user", "ref": str(AG3)}]},
                {
                    "name": "Freigabe",
                    "stage": 2,
                    "rule": "quorum",
                    "quorum": 1,
                    "approvers": [{"kind": "resolver", "ref": "ticket.owner"}],
                    "due_hours": 24,
                    "on_timeout": "escalate",
                    "escalation": [{"kind": "group", "ref": "970201"}],
                },
            ]
        }
    )
    async with approvals_session(request, url_fixture) as session:
        row = await templates.create_template(
            session,
            key="appr97.rt",
            name="Round trip",
            description="d",
            definition=definition,
            subject_types=["ticket"],
            actor_id=AG1,
        )
        assert row.definition == definition
        assert (row.key, row.origin, row.valid, row.subject_types) == (
            "appr97.rt",
            "admin",
            True,
            ("ticket",),
        )
        assert await templates.get_template(session, row.id) == row
        assert await templates.get_template_by_key(session, "appr97.rt") == row
        new_def = _definition({"kind": "user", "ref": str(AG1)})
        changed = await templates.update_template(
            session, row.id, actor_id=AG1, name="Neu", definition=new_def, valid=False
        )
        assert (changed.name, changed.valid, changed.definition) == ("Neu", False, new_def)
        assert changed.subject_types == ("ticket",) and changed.description == "d"
        assert [t.key for t in await templates.list_templates(session)] == []
        assert [t.key for t in await templates.list_templates(session, include_invalid=True)] == [
            "appr97.rt"
        ]
        await templates.delete_template(session, row.id)
        for call in (
            templates.get_template(session, row.id),
            templates.get_template_by_key(session, "appr97.rt"),
        ):
            with pytest.raises(ApprovalError) as err:
                await call
            assert (err.value.code, err.value.http_status) == ("template_not_found", 404)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_key_unique_and_immutable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        first = await templates.create_template(
            session, key="appr97.k1", name="A", definition=USER, actor_id=AG1
        )
        with pytest.raises(ApprovalError) as err:
            await templates.create_template(
                session, key="appr97.k1", name="B", definition=USER, actor_id=AG1
            )
        assert (err.value.code, err.value.http_status) == ("template_key_taken", 409)
        with pytest.raises(TypeError):
            await templates.update_template(  # type: ignore[call-arg]
                session, first.id, actor_id=AG1, key="appr97.k2"
            )
        assert (await templates.get_template(session, first.id)).key == "appr97.k1"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_subject_type_filter(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    change = make_fake_subject_type("appr97.change")
    with installed_registry(change):
        async with approvals_session(request, url_fixture) as session:
            await templates.create_template(
                session, key="appr97.all", name="a", definition=USER, actor_id=AG1
            )
            await templates.create_template(
                session,
                key="appr97.only",
                name="b",
                definition=USER,
                subject_types=["appr97.change"],
                actor_id=AG1,
            )
            await templates.create_template(
                session,
                key="appr97.tick",
                name="c",
                definition=USER,
                subject_types=["ticket"],
                actor_id=AG1,
            )

            async def keys(subject: str | None) -> list[str]:
                return [
                    t.key for t in await templates.list_templates(session, subject_type=subject)
                ]

            assert await keys(None) == ["appr97.all", "appr97.only", "appr97.tick"]
            assert await keys("appr97.change") == ["appr97.all", "appr97.only"]
            assert await keys("ticket") == ["appr97.all", "appr97.tick"]
            with pytest.raises(ApprovalError) as err:
                await templates.create_template(
                    session,
                    key="appr97.bad",
                    name="d",
                    definition=USER,
                    subject_types=["nope.type"],
                    actor_id=AG1,
                )
            assert (err.value.code, err.value.http_status) == ("unknown_subject_type", 422)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ensure_template_inserts_once_never_overwrites(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        first = await templates.ensure_template(
            session,
            key="appr97.ens",
            name="Erster",
            definition=USER,
            origin="appr97.consumer",
            subject_types=["ticket"],
        )
        other = _definition({"kind": "user", "ref": str(AG1)})
        second = await templates.ensure_template(
            session, key="appr97.ens", name="Zweiter", definition=other, origin="appr97.other"
        )
        assert second == first
        assert (first.name, first.origin, first.definition) == ("Erster", "appr97.consumer", USER)
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_approval_template WHERE template_key = :k"),
                {"k": "appr97.ens"},
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_validate_refs_reports_missing_and_unknown(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    plain = make_fake_subject_type("appr97.plain")
    with installed_registry(plain):
        async with approvals_session(request, url_fixture) as session:
            alive = await lists.create_list(session, name="appr97 alive", actor_id=AG1)
            definition = _definition(
                {"kind": "user", "ref": str(AG3)},
                {"kind": "user", "ref": "970199"},
                {"kind": "group", "ref": "970299"},
                {"kind": "group", "ref": "970201"},
                {"kind": "role", "ref": "970398"},
                {"kind": "role", "ref": str(ROLE)},
                {"kind": "list", "ref": "970397"},
                {"kind": "list", "ref": str(alive.id)},
                {"kind": "user", "ref": "abc"},
            )
            check = await templates.validate_definition_refs(session, definition)
            assert check.warnings == [
                "missing_user:970199",
                "missing_group:970299",
                "missing_role:970398",
                "missing_list:970397",
            ]
            assert check.errors == ["invalid_ref:user:abc"]

            check = await templates.validate_definition_refs(
                session, _definition({"kind": "resolver", "ref": "itsm.foo"})
            )
            assert (check.errors, check.warnings) == (["unknown_resolver:itsm.foo"], [])

            known = await templates.validate_definition_refs(
                session, _definition({"kind": "resolver", "ref": "ticket.owner"})
            )
            assert (known.errors, known.warnings) == ([], [])

            # an escalation entry is checked like an approver entry
            esc = _definition(
                {"kind": "user", "ref": str(AG3)},
                on_timeout="escalate",
                escalation=[{"kind": "user", "ref": "970198"}],
            )
            check = await templates.validate_definition_refs(session, esc)
            assert check.warnings == ["missing_user:970198"]

            customer = _definition({"kind": "customer_user", "ref": "appr97-k1"})
            resolver = _definition({"kind": "resolver", "ref": "ticket.customer_user"})
            for definition_ in (customer, resolver):
                denied = await templates.validate_definition_refs(
                    session, definition_, subject_types=["appr97.plain"]
                )
                assert denied.errors == ["customer_entry_not_allowed"]
                allowed = await templates.validate_definition_refs(
                    session, definition_, subject_types=["ticket"]
                )
                assert allowed.errors == []
            ghost = await templates.validate_definition_refs(
                session,
                _definition({"kind": "customer_user", "ref": "appr97-ghost"}),
                subject_types=["ticket"],
            )
            assert (ghost.errors, ghost.warnings) == ([], ["missing_customer:appr97-ghost"])

            with pytest.raises(ApprovalError) as err:
                await templates.create_template(
                    session,
                    key="appr97.cust",
                    name="x",
                    definition=customer,
                    subject_types=["appr97.plain"],
                    actor_id=AG1,
                )
            assert (err.value.code, err.value.http_status) == ("invalid_definition", 422)
            assert "customer_entry_not_allowed" in (err.value.detail or "")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delete_template_keeps_running_requests(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        template = await templates.create_template(
            session, key="appr97.run", name="Run", definition=USER, actor_id=AG1
        )
        await session.execute(
            text(
                "INSERT INTO tiqora_approval (ticket_id, subject_type, approval_key, title,"
                " template_id, status, requester, created_by, created_at)"
                " VALUES (:t, 'ticket', 'default', 'appr97 laufend', :tpl, 'pending',"
                " 'agent:970101', 970101, :now)"
            ),
            {"t": T1, "tpl": template.id, "now": utc_now()},
        )
        await templates.delete_template(session, template.id)
        row = (
            await session.execute(
                text(
                    "SELECT template_id, status, title FROM tiqora_approval"
                    " WHERE title = 'appr97 laufend'"
                )
            )
        ).one()
        assert tuple(row) == (None, "pending", "appr97 laufend")
