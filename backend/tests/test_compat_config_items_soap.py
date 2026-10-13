"""SOAP transport of the ``ConfigItem*`` operations (Task 24; both dialects).

``POST /znuny-compat/soap/<webservice>`` with a ``ConfigItemGet`` envelope must answer the same
data as the REST route (one handler, two codecs). Users live in the plan's band 56530-56549.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import GROUP_RW, CmdbWorld, make_ci, make_class, set_mode
from tiqora.api.app import create_app
from tiqora.api.deps import get_db, get_session_store
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PW = "itim56-soap-secret"
SOAP_ID, RO_ID = 56540, 56541
SOAP_USER, RO_USER = "itim56.soap.rw", "itim56.soap.none"
WHEN = datetime(2026, 10, 1, 8, 0, 0)

ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
)


@pytest.fixture(scope="module", autouse=True)
def soap_agents(cmdb_db: CmdbWorld) -> Iterator[None]:
    cleanup = (
        f"DELETE FROM group_user WHERE user_id IN ({SOAP_ID}, {RO_ID})",
        f"DELETE FROM users WHERE id IN ({SOAP_ID}, {RO_ID})",
    )
    engine = create_engine(cmdb_db.sync_url)
    try:
        with engine.begin() as conn:
            for stmt in cleanup:
                conn.execute(text(stmt))
            for uid, login in ((SOAP_ID, SOAP_USER), (RO_ID, RO_USER)):
                conn.execute(
                    text(
                        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :l, :pw, 'Soap', 'Agent', 1, :t, 1, :t, 1)"
                    ),
                    {"id": uid, "l": login, "pw": hash_password(PW), "t": WHEN},
                )
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": SOAP_ID, "g": GROUP_RW, "t": WHEN},
            )
    finally:
        engine.dispose()
    try:
        yield
    finally:
        engine = create_engine(cmdb_db.sync_url)
        try:
            with engine.begin() as conn:
                for stmt in cleanup:
                    conn.execute(text(stmt))
        finally:
            engine.dispose()


class _FakeRedis:
    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        pass

    async def get(self, key: str) -> str | None:
        return None

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _client(world: CmdbWorld) -> AsyncClient:
    factory = world.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _envelope(operation: str, login: str, password: str, **fields: Any) -> bytes:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    body = (
        f"<{operation}><UserLogin>{login}</UserLogin><Password>{password}</Password>"
        f"{inner}</{operation}>"
    )
    return ENVELOPE.format(body=body).encode()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _leaf_texts(node: ET.Element) -> dict[str, list[str]]:
    """Every element with text anywhere below ``node``: local name -> texts, in order."""
    out: dict[str, list[str]] = {}
    for el in node.iter():
        if el is not node and el.text and el.text.strip():
            out.setdefault(_local(el.tag), []).append(el.text.strip())
    return out


def _rest_leaves(value: Any, out: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(inner, dict | list):
                _rest_leaves(inner, out)
            elif inner not in (None, ""):
                out.setdefault(key, []).append(str(inner))
    elif isinstance(value, list):
        for inner in value:
            _rest_leaves(inner, out)
    return out


async def _seed(world: CmdbWorld) -> int:
    set_mode(world, "tiqora")
    async with world.factory() as s:
        class_id = await make_class(s)
        return await make_ci(s, class_id, "itim56-soap-ci", attributes={"Vendor": [{"v": "Acme"}]})


async def test_soap_config_item_get_answers_the_data_of_the_rest_route(
    cmdb_db: CmdbWorld,
) -> None:
    ci_id = await _seed(cmdb_db)
    async with _client(cmdb_db) as client:
        rest = await client.get(
            f"/znuny-compat/ConfigItem/{ci_id}",
            params={"UserLogin": SOAP_USER, "Password": PW},
        )
        assert rest.status_code == 200, rest.text
        soap = await client.post(
            "/znuny-compat/soap/itim56",
            content=_envelope("ConfigItemGet", SOAP_USER, PW, ConfigItemID=ci_id),
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
    assert soap.status_code == 200, soap.text
    root = ET.fromstring(soap.content)
    body = next(el for el in root.iter() if _local(el.tag) == "ConfigItemGetResponse")
    soap_leaves = _leaf_texts(body)
    rest_json = rest.json()
    rest_leaves = _rest_leaves(rest_json)
    assert rest_json["ConfigItem"][0]["Name"] == "itim56-soap-ci"
    assert soap_leaves["ConfigItemID"] == [str(ci_id)] == rest_leaves["ConfigItemID"]
    assert soap_leaves["Name"] == ["itim56-soap-ci"]
    assert soap_leaves == rest_leaves  # every field, same values in the same order


async def test_soap_config_item_delete_and_access_denied_fault(cmdb_db: CmdbWorld) -> None:
    ci_id = await _seed(cmdb_db)
    headers = {"Content-Type": "text/xml; charset=utf-8"}
    async with _client(cmdb_db) as client:
        denied = await client.post(
            "/znuny-compat/soap/itim56",
            content=_envelope("ConfigItemDelete", RO_USER, PW, ConfigItemID=ci_id),
            headers=headers,
        )
        assert (
            "ConfigItemDelete.AccessDenied: ConfigItemDelete: Can not delete configuration item!"
            in denied.text
        )
        assert denied.status_code >= 400
        assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 1
        ok = await client.post(
            "/znuny-compat/soap/itim56",
            content=_envelope("ConfigItemDelete", SOAP_USER, PW, ConfigItemID=ci_id),
            headers=headers,
        )
    assert ok.status_code == 200, ok.text
    root = ET.fromstring(ok.content)
    body = next(el for el in root.iter() if _local(el.tag) == "ConfigItemDeleteResponse")
    assert _leaf_texts(body) == {"ConfigItemID": [str(ci_id)]}
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0
