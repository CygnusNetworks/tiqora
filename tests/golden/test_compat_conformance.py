"""Golden-master: compat GenericInterface conformance vs real Znuny.

Configures Znuny's shipped ``GenericTicketConnectorREST`` webservice (via
``Admin::WebService::Add --source-path``) and calls it over HTTP. Tiqora serves the
very same webservice row (shared DB) through its real ASGI app, so TicketSearch is
called as ``POST /Ticket/Search`` on both sides exactly as the shipped YAML routes it
(SessionCreate keeps calling the compat operation directly). See docs/compatibility.md
"Known gotchas" for the StateType singular gotcha and the ``GET /Ticket/Search`` quirk.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest
import pytest_asyncio
from _helpers import tiqora_http_client, znuny_console

pytestmark = pytest.mark.golden

WEBSERVICE_NAME = "GoldenGenericTicketConnectorREST"
ZNUNY_BASE_URL = "http://127.0.0.1:8180"
ZNUNY_REST_BASE = f"{ZNUNY_BASE_URL}/otrs/nph-genericinterface.pl/Webservice/{WEBSERVICE_NAME}"
TIQORA_REST_BASE = f"/znuny-compat/Webservice/{WEBSERVICE_NAME}"


# Dockerfile.znuny normalizes this path for all peers (6.0–7.3 sample locations vary).
_WEBSERVICE_YML = "/opt/otrs/var/webservices/examples/GenericTicketConnectorREST.yml"


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
            f"--source-path={_WEBSERVICE_YML}",
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


@pytest_asyncio.fixture()
async def tiqora_client(golden_session_factory):
    async with tiqora_http_client(golden_session_factory) as client:
        yield client


async def _tiqora_session_id(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{TIQORA_REST_BASE}/Session",
        json={"UserLogin": "golden.agent", "Password": "golden-agent-pw"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "SessionID" in body and "Error" not in body, body
    return str(body["SessionID"])


async def _search_both(
    tiqora_client: httpx.AsyncClient, payload: dict[str, object]
) -> tuple[dict, dict]:
    """POST /Ticket/Search on Znuny and Tiqora; both must answer 200 without Error."""
    znuny_session_id = await _znuny_session_create()
    tiqora_session_id = await _tiqora_session_id(tiqora_client)
    async with httpx.AsyncClient(timeout=20) as client:
        znuny_resp = await client.post(
            f"{ZNUNY_REST_BASE}/Ticket/Search", json={"SessionID": znuny_session_id, **payload}
        )
    tiqora_resp = await tiqora_client.post(
        f"{TIQORA_REST_BASE}/Ticket/Search", json={"SessionID": tiqora_session_id, **payload}
    )
    assert znuny_resp.status_code == 200, znuny_resp.text
    assert tiqora_resp.status_code == 200, tiqora_resp.text
    znuny_body, tiqora_body = znuny_resp.json(), tiqora_resp.json()
    assert "Error" not in znuny_body, znuny_body
    assert "Error" not in tiqora_body, tiqora_body
    return znuny_body, tiqora_body


@pytest.mark.asyncio
async def test_ticket_search_state_type_singular(tiqora_client) -> None:
    """Singular ``StateType`` filters identically on both sides.

    (The plural ``StateTypes`` is a documented Tiqora extension that Znuny ignores, see
    docs/compatibility.md "StateType / StateTypes gotcha"; it is deliberately not compared.)
    """
    znuny_new, tiqora_new = await _search_both(tiqora_client, {"StateType": "new"})
    znuny_ids = sorted(map(str, znuny_new.get("TicketID", [])))
    assert znuny_ids, "seeded golden DB has no ticket of state type 'new'"
    assert znuny_ids == sorted(map(str, tiqora_new.get("TicketID", [])))

    znuny_closed, tiqora_closed = await _search_both(tiqora_client, {"StateType": "closed"})
    assert sorted(map(str, znuny_closed.get("TicketID", []))) == sorted(
        map(str, tiqora_closed.get("TicketID", []))
    )
    assert set(znuny_closed.get("TicketID", [])).isdisjoint(znuny_new.get("TicketID", []))


@pytest.mark.asyncio
async def test_empty_search_returns_structured_result_not_404(tiqora_client) -> None:
    """Empty TicketSearch result sets return the same (empty) wire shape on both sides."""
    znuny_body, tiqora_body = await _search_both(
        tiqora_client, {"Title": "no-such-ticket-title-xyz-golden"}
    )
    assert znuny_body.get("TicketID", []) == []
    assert tiqora_body.get("TicketID", []) == []
    assert znuny_body == tiqora_body


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "GET /Ticket/Search: Znuny answers TicketGet.WrongStructure "
        "(Operation/Ticket/TicketGet.pm, 'Invalid ticket ID parameter(s) found!'); Tiqora "
        "crashes with ValueError int('Search') in op_ticket_get (operations.py:1913), "
        "fix pending"
    ),
)
async def test_get_ticket_search_quirk_is_a_ticket_get_error(tiqora_client) -> None:
    """``GET /Ticket/Search`` matches the ``GET /Ticket/:TicketID`` route with TicketID
    ``Search``: both sides answer with a TicketGet error, not with a search result."""
    znuny_session_id = await _znuny_session_create()
    tiqora_session_id = await _tiqora_session_id(tiqora_client)
    async with httpx.AsyncClient(timeout=20) as client:
        znuny_resp = await client.get(
            f"{ZNUNY_REST_BASE}/Ticket/Search",
            params={"SessionID": znuny_session_id, "StateType": "open"},
        )
    tiqora_resp = await tiqora_client.get(
        f"{TIQORA_REST_BASE}/Ticket/Search",
        params={"SessionID": tiqora_session_id, "StateType": "open"},
    )
    znuny_body, tiqora_body = znuny_resp.json(), tiqora_resp.json()
    assert "Error" in znuny_body, znuny_body
    assert "Error" in tiqora_body, tiqora_body
    assert "TicketID" not in znuny_body and "TicketID" not in tiqora_body
    assert znuny_body["Error"]["ErrorCode"].startswith("TicketGet."), znuny_body
    assert tiqora_body["Error"]["ErrorCode"].startswith("TicketGet."), tiqora_body
