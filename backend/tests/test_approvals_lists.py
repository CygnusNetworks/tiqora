"""Approver lists (A5 ``lists``). Own rows 970650-970749 (lists/templates are autoincrement)."""

from __future__ import annotations

import pytest

from tests._approvals_helpers import AG1, AG2, AG3, AG4, AG6, approvals_session
from tiqora.approvals import lists, templates
from tiqora.approvals.contract import ApprovalDefinition, ApprovalError

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


def _definition(list_id: int) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate(
        {"steps": [{"name": "CAB", "approvers": [{"kind": "list", "ref": str(list_id)}]}]}
    )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_create_list_with_members(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        row = await lists.create_list(
            session,
            name="appr97 CAB",
            description="Board",
            member_ids=[AG4, AG3, AG3],
            actor_id=AG1,
        )
        assert (row.name, row.description, row.valid) == ("appr97 CAB", "Board", True)
        assert row.member_ids == (AG3, AG4)
        assert await lists.get_list(session, row.id) == row
        await lists.update_list(session, row.id, actor_id=AG1, member_ids=[AG2], description="B2")
        again = await lists.get_list(session, row.id)
        assert again.member_ids == (AG2,) and again.description == "B2"
        await lists.update_list(session, row.id, actor_id=AG1, valid=False)
        assert [r.id for r in await lists.list_lists(session)] == []
        assert [r.id for r in await lists.list_lists(session, include_invalid=True)] == [row.id]
        with pytest.raises(ApprovalError) as err:
            await lists.get_list(session, row.id + 100000)
        assert (err.value.code, err.value.http_status) == ("list_not_found", 404)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_members_must_be_valid_agents(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        for bad in ([AG6], [AG3, 970199]):
            with pytest.raises(ApprovalError) as err:
                await lists.create_list(session, name="appr97 bad", member_ids=bad, actor_id=AG1)
            assert (err.value.code, err.value.http_status) == ("invalid_member", 422)
        ok = await lists.create_list(session, name="appr97 ok", member_ids=[AG3], actor_id=AG1)
        with pytest.raises(ApprovalError) as err:
            await lists.update_list(session, ok.id, actor_id=AG1, member_ids=[AG6])
        assert err.value.code == "invalid_member"
        assert (await lists.get_list(session, ok.id)).member_ids == (AG3,)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_name_unique(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        first = await lists.create_list(session, name="appr97 dup", actor_id=AG1)
        other = await lists.create_list(session, name="appr97 other", actor_id=AG1)
        with pytest.raises(ApprovalError) as err:
            await lists.create_list(session, name="appr97 dup", actor_id=AG1)
        assert (err.value.code, err.value.http_status) == ("list_name_taken", 409)
        with pytest.raises(ApprovalError) as err:
            await lists.update_list(session, other.id, actor_id=AG1, name="appr97 dup")
        assert err.value.code == "list_name_taken"
        renamed = await lists.update_list(session, first.id, actor_id=AG1, name="appr97 dup")
        assert renamed.name == "appr97 dup"  # own name is no conflict


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delete_list_used_by_template_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        used = await lists.create_list(session, name="appr97 used", member_ids=[AG3], actor_id=AG1)
        free = await lists.create_list(session, name="appr97 free", member_ids=[AG3], actor_id=AG1)
        template = await templates.create_template(
            session, key="appr97.cab", name="CAB", definition=_definition(used.id), actor_id=AG1
        )
        with pytest.raises(ApprovalError) as err:
            await lists.delete_list(session, used.id)
        assert (err.value.code, err.value.http_status) == ("list_in_use", 409)
        assert (await lists.get_list(session, used.id)).member_ids == (AG3,)
        await lists.delete_list(session, free.id)
        with pytest.raises(ApprovalError):
            await lists.get_list(session, free.id)
        assert (await lists.list_member_ids(session, [free.id])) == {free.id: ()}
        await templates.delete_template(session, template.id)
        await lists.delete_list(session, used.id)
        assert [r.name for r in await lists.list_lists(session, include_invalid=True)] == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_member_ids_bulk(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        a = await lists.create_list(session, name="appr97 a", member_ids=[AG3, AG4], actor_id=AG1)
        b = await lists.create_list(session, name="appr97 b", actor_id=AG1)
        c = await lists.create_list(session, name="appr97 c", member_ids=[AG2], actor_id=AG1)
        got = await lists.list_member_ids(session, [a.id, b.id, c.id, 970199])
        assert got == {a.id: (AG3, AG4), b.id: (), c.id: (AG2,), 970199: ()}
        assert await lists.list_member_ids(session, []) == {}
