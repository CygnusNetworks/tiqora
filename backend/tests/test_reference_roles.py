"""``GET /reference/roles``: valid roles for approver pickers, any agent, nobody else.

Both dialects. Own ids 971750-971759 (inside the approvals band); rows roll back with the session.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._approvals_helpers import AG3, approvals_session, as_client
from tiqora.jobs.service import utc_now

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
URL = "/api/v1/reference/roles"
LO, HI = 971750, 971759


async def _seed(session: object) -> None:
    for rid, name, valid in (
        (971752, "Zeta Rolle", 1),
        (971750, "Alpha Rolle", 1),
        (971751, "Alt", 2),
    ):
        await session.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:i, :n, :v, :t, 1, :t, 1)"
            ),
            {"i": rid, "n": name, "v": valid, "t": utc_now()},
        )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_agent_gets_valid_roles_sorted_by_name(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed(session)
        await session.commit()
        async with await as_client(session, agent=AG3) as client:
            got = await client.get(URL)
        assert got.status_code == 200, got.text
        rows = got.json()
        ours = [r for r in rows if LO <= r["id"] <= HI]
        assert ours == [
            {"id": 971750, "name": "Alpha Rolle"},
            {"id": 971752, "name": "Zeta Rolle"},
        ]
        names = [r["name"].casefold() for r in rows]  # the database collation ignores case
        assert names == sorted(names)
        assert all(set(r) == {"id", "name"} for r in rows)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_customer_and_anonymous_are_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed(session)
        await session.commit()
        async with await as_client(session) as anonymous:
            assert (await anonymous.get(URL)).status_code == 401
        async with await as_client(session, customer="appr97-k1") as customer:
            assert (await customer.get(URL)).status_code == 401
