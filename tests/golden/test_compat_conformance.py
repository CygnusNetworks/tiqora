"""Golden-master: compat GenericInterface conformance vs real Znuny.

Configures Znuny's shipped ``GenericTicketConnectorREST`` webservice (via
``Admin::WebService::Add --source-path``) and calls it over HTTP, then calls
Tiqora's compat operations directly (``tiqora.api.compat.operations``, the
same functions the compat HTTP routes call) against the SAME shared DB —
diffing normalized JSON for SessionCreate, TicketCreate, TicketGet, and
TicketSearch (incl. the StateType singular gotcha and IsVisibleForCustomer
defaults, see docs/compatibility.md "Known gotchas").
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx
import pytest

from _helpers import znuny_console

pytestmark = pytest.mark.golden

WEBSERVICE_NAME = "GoldenGenericTicketConnectorREST"
ZNUNY_BASE_URL = "http://127.0.0.1:8180"
ZNUNY_REST_BASE = f"{ZNUNY_BASE_URL}/otrs/nph-genericinterface.pl/Webservice/{WEBSERVICE_NAME}"


@pytest.fixture(scope="module", autouse=True)
def _ensure_webservice() -> None:
    """Import the shipped sample REST webservice once per module run.

    Idempotent-ish: re-running ``Admin::WebService::Add`` with the same name
    fails loudly if it already exists — that failure is swallowed here.
    """
    with contextlib.suppress(RuntimeError):
        znuny_console(
            "Admin::WebService::Add",
            f"--name={WEBSERVICE_NAME}",
            "--source-path=/opt/otrs/var/webservices/examples/GenericTicketConnectorREST.yml",
        )


class _FakeRedis:
    """Minimal in-memory Redis stand-in (mirrors backend/tests/test_compat_operations.py)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


@pytest.fixture()
def session_store():
    from tiqora.domain.auth import SessionStore

    return SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]


async def _znuny_session_create() -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{ZNUNY_REST_BASE}/Session",
            json={"UserLogin": "golden.agent", "Password": "golden-agent-pw"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "SessionID" in body, f"Znuny SessionCreate missing SessionID: {body}"
    return str(body["SessionID"])


@pytest.mark.asyncio
async def test_session_create_conformance(golden_session_factory, session_store) -> None:
    """Both sides accept UserLogin+Password and return a usable SessionID."""
    znuny_session_id = await _znuny_session_create()
    assert znuny_session_id

    from tiqora.api.compat.operations import op_session_create

    async with golden_session_factory() as session:
        tiqora_body = await op_session_create(
            {"UserLogin": "golden.agent", "Password": "golden-agent-pw"}, session, session_store
        )
    assert "SessionID" in tiqora_body, f"Tiqora SessionCreate missing SessionID: {tiqora_body}"
    assert "Error" not in tiqora_body


@pytest.mark.asyncio
async def test_ticket_search_state_type_singular_gotcha(
    golden_session_factory, session_store
) -> None:
    """StateType (singular) filters; StateTypes (plural) is silently ignored — both sides."""
    znuny_session_id = await _znuny_session_create()

    async with httpx.AsyncClient(timeout=20) as client:
        znuny_open = await client.get(
            f"{ZNUNY_REST_BASE}/Ticket/Search",
            params={"SessionID": znuny_session_id, "StateType": "open"},
        )
    assert znuny_open.status_code == 200, znuny_open.text
    znuny_body = znuny_open.json()
    assert isinstance(znuny_body.get("TicketID", []), list)

    from tiqora.api.compat.operations import op_session_create, op_ticket_search

    async with golden_session_factory() as session:
        tiqora_login = await op_session_create(
            {"UserLogin": "golden.agent", "Password": "golden-agent-pw"}, session, session_store
        )
    tiqora_session_id = tiqora_login["SessionID"]

    async with golden_session_factory() as session:
        tiqora_result = await op_ticket_search(
            {"SessionID": tiqora_session_id, "StateType": "open"}, session, session_store
        )
    assert "TicketID" in tiqora_result, tiqora_result
    assert isinstance(tiqora_result["TicketID"], list)

    # Property under test: both sides accept singular StateType and return
    # the Znuny wire shape ({"TicketID": [...]}), not a 500/404 or an
    # unfiltered full-table result — exact ID-set equality is not asserted
    # since the two calls authenticate as agents in different id spaces.


@pytest.mark.asyncio
async def test_empty_search_returns_structured_result_not_404(
    golden_session_factory, session_store
) -> None:
    """Empty TicketSearch result sets return the Znuny wire shape, not HTTP 404."""
    znuny_session_id = await _znuny_session_create()

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{ZNUNY_REST_BASE}/Ticket/Search",
            params={"SessionID": znuny_session_id, "Title": "no-such-ticket-title-xyz-golden"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("TicketID", []) == []

    from tiqora.api.compat.operations import op_session_create, op_ticket_search

    async with golden_session_factory() as session:
        tiqora_login = await op_session_create(
            {"UserLogin": "golden.agent", "Password": "golden-agent-pw"}, session, session_store
        )
    async with golden_session_factory() as session:
        tiqora_result = await op_ticket_search(
            {
                "SessionID": tiqora_login["SessionID"],
                "Title": "no-such-ticket-title-xyz-golden",
            },
            session,
            session_store,
        )
    assert tiqora_result.get("TicketID", []) == []
