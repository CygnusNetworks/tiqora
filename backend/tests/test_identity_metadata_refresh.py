"""Daily SAML metadata refresh (wave 1, Task 12). Provider slugs use the ``idpmd-`` prefix."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import make_provider
from tests._saml_idp import IDP_ENTITY_ID, IDP_SLO_URL, IDP_SSO_URL, make_test_cert
from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_IDENTITY_METADATA_REFRESH_ENABLED, set_setting
from tiqora.identity import metadata_refresh as job
from tiqora.identity.saml_metadata import SamlMetadataError
from tiqora.identity.store import ProviderRecord, get_provider
from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-metadata"
SETTINGS = Settings(environment="test", secret_key=KEY)
SLUG = "idpmd-"
PUBLIC = "93.184.216.34"
_URLS: set[str] = set()

_TEMPLATE = """<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{entity}">
 <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
  <md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
   <ds:X509Data><ds:X509Certificate>{cert}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>
  </md:KeyDescriptor>
  <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
    Location="{slo}"/>
  <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
    Location="{sso}"/>
 </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


@pytest.fixture(scope="module")
def idp_xml(tmp_path_factory: pytest.TempPathFactory) -> str:
    tmp = tmp_path_factory.mktemp("mdr")
    pem = make_test_cert("idp.example.org", tmp / "k.pem", tmp / "c.pem")
    cert = "".join(line for line in pem.splitlines() if "CERTIFICATE" not in line)
    return _TEMPLATE.format(entity=IDP_ENTITY_ID, cert=cert, sso=IDP_SSO_URL, slo=IDP_SLO_URL)


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_identity_provider WHERE slug LIKE :p"), {"p": f"{SLUG}%"}
            )
            conn.execute(
                delete(TiqoraSettings).where(
                    TiqoraSettings.key == KEY_IDENTITY_METADATA_REFRESH_ENABLED
                )
            )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", lambda _h, _p: [PUBLIC])


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(job, "get_session_factory", lambda: maker)
    async with maker() as s:
        yield s
    await engine.dispose()
    _clean(url)


async def _saml(session: AsyncSession, slug: str, **cfg: Any) -> ProviderRecord:
    record = await make_provider(
        session, slug=f"{SLUG}{slug}", kind="saml", enabled=False, config=cfg, settings=SETTINGS
    )
    await session.commit()
    return record


async def _reload(session: AsyncSession, record: ProviderRecord) -> ProviderRecord:
    await session.rollback()
    session.expire_all()
    fresh = await get_provider(session, record.id, secret_key=KEY)
    assert fresh is not None
    return fresh


def _transport(routes: dict[str, Callable[[], httpx.Response]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[request.headers["host"]]()

    return httpx.MockTransport(handler)


async def test_tick_refreshes_failed_keeps_old_and_skips_pasted(
    env: AsyncSession, idp_xml: str
) -> None:
    ok = await _saml(env, "ok", idp_metadata_url="https://ok.example.org/md")
    broken = await _saml(env, "broken", idp_metadata_url="https://broken.example.org/md")
    pasted = await _saml(
        env,
        "pasted",
        idp_metadata_url="https://pasted.example.org/md",
        idp_metadata_xml=idp_xml,
    )
    await env.execute(
        text("UPDATE tiqora_identity_provider SET metadata_xml = :x WHERE id = :i"),
        {"x": "<old/>", "i": broken.id},
    )
    await env.commit()

    transport = _transport(
        {
            "ok.example.org": lambda: httpx.Response(200, text=idp_xml),
            "broken.example.org": lambda: httpx.Response(500),
        }
    )
    out = await job.run_identity_metadata_refresh_tick(settings=SETTINGS, transport=transport)
    assert out == {"refreshed": 1, "failed": 1, "skipped": 1}

    ok_after = await _reload(env, ok)
    assert ok_after.metadata_xml == idp_xml
    assert isinstance(ok_after.metadata_fetched_at, datetime)
    assert ok_after.metadata_error is None

    broken_after = await _reload(env, broken)
    assert broken_after.metadata_xml == "<old/>"  # last good copy survives
    assert broken_after.metadata_error == "Die Metadaten konnten nicht geladen werden (HTTP 500)."
    assert broken_after.metadata_fetched_at is None

    pasted_after = await _reload(env, pasted)
    assert pasted_after.metadata_xml is None and pasted_after.metadata_error is None


async def test_recovery_clears_error(env: AsyncSession, idp_xml: str) -> None:
    rec = await _saml(env, "recover", idp_metadata_url="https://flaky.example.org/md")
    state = {"status": 503}
    transport = _transport(
        {
            "flaky.example.org": lambda: httpx.Response(
                state["status"], text=idp_xml if state["status"] == 200 else ""
            )
        }
    )
    out = await job.run_identity_metadata_refresh_tick(settings=SETTINGS, transport=transport)
    assert out["failed"] == 1
    assert (await _reload(env, rec)).metadata_error is not None
    state["status"] = 200
    out = await job.run_identity_metadata_refresh_tick(settings=SETTINGS, transport=transport)
    assert out["refreshed"] == 1
    after = await _reload(env, rec)
    assert after.metadata_error is None and after.metadata_xml == idp_xml


async def test_invalid_metadata_keeps_old_and_does_not_disable(
    env: AsyncSession, idp_xml: str
) -> None:
    rec = await _saml(env, "bad", idp_metadata_url="https://bad.example.org/md")
    await env.execute(
        text("UPDATE tiqora_identity_provider SET metadata_xml = :x, enabled = :e WHERE id = :i"),
        {"x": idp_xml, "e": True, "i": rec.id},
    )
    await env.commit()
    transport = _transport({"bad.example.org": lambda: httpx.Response(200, text="<nope/>")})
    out = await job.run_identity_metadata_refresh_tick(settings=SETTINGS, transport=transport)
    assert out == {"refreshed": 0, "failed": 1, "skipped": 0}
    after = await _reload(env, rec)
    assert after.metadata_xml == idp_xml and after.enabled is True
    assert after.metadata_error and "Anmeldedienst" in after.metadata_error


async def test_disabled_setting_is_a_noop(env: AsyncSession, idp_xml: str) -> None:
    rec = await _saml(env, "off", idp_metadata_url="https://off.example.org/md")
    await set_setting(env, KEY_IDENTITY_METADATA_REFRESH_ENABLED, "0")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=idp_xml)

    out = await job.run_identity_metadata_refresh_tick(
        settings=SETTINGS, transport=httpx.MockTransport(handler)
    )
    assert out == {"refreshed": 0, "failed": 0, "skipped": 0}
    assert calls == []
    assert (await _reload(env, rec)).metadata_xml is None


async def test_refresh_provider_metadata_raises_and_leaves_row(env: AsyncSession) -> None:
    rec = await _saml(env, "raise", idp_metadata_url="https://gone.example.org/md")
    transport = _transport({"gone.example.org": lambda: httpx.Response(404)})
    with pytest.raises(SamlMetadataError, match=re.escape("HTTP 404")):
        await job.refresh_provider_metadata(env, rec, settings=SETTINGS, transport=transport)
    assert (await _reload(env, rec)).metadata_xml is None


def test_catalog_entry_is_daily_and_default_on() -> None:
    svc = DAEMON_SERVICES_BY_SLUG["identity_metadata"]
    assert svc.enabled_key == KEY_IDENTITY_METADATA_REFRESH_ENABLED
    assert svc.schedule_kind == "daily" and svc.default_enabled and svc.toggleable
