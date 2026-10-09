"""IdP metadata parsing and fetching (pure; no xmlsec needed except to generate sample XML)."""

from __future__ import annotations

import re

import httpx
import pytest

from tests._saml_idp import IDP_ENTITY_ID, IDP_SLO_URL, IDP_SSO_URL, make_test_cert
from tiqora.identity.saml_metadata import (
    SamlMetadataError,
    fetch_idp_metadata,
    parse_idp_metadata,
)

PUBLIC = "93.184.216.34"


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
    """Hand-written IdP metadata: this module needs neither pysaml2 nor xmlsec1 to run."""
    tmp = tmp_path_factory.mktemp("md")
    pem = make_test_cert("idp.example.org", tmp / "k.pem", tmp / "c.pem")
    cert = "".join(line for line in pem.splitlines() if "CERTIFICATE" not in line)
    return _TEMPLATE.format(entity=IDP_ENTITY_ID, cert=cert, sso=IDP_SSO_URL, slo=IDP_SLO_URL)


def _inner(xml: str) -> str:
    match = re.search(r"<(\w+:)?EntityDescriptor\b.*</(\w+:)?EntityDescriptor>", xml, re.S)
    assert match is not None
    return match.group(0)


def test_parse_returns_entity_cert_and_sso(idp_xml: str) -> None:
    info = parse_idp_metadata(idp_xml, entity_id=None)
    assert info.entity_ids == (IDP_ENTITY_ID,)
    assert len(info.signing_certs) >= 1 and info.signing_certs[0].startswith("MII")
    assert info.sso_redirect_url == IDP_SSO_URL
    assert info.slo_redirect_url is not None


def test_aggregate_without_entity_id_rejected(idp_xml: str) -> None:
    one = _inner(idp_xml)
    two = one.replace(IDP_ENTITY_ID, "https://other.example.org/saml")
    agg = (
        '<md:EntitiesDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata">'
        + one
        + two
        + "</md:EntitiesDescriptor>"
    )
    with pytest.raises(SamlMetadataError, match="mehrere Anmeldedienste"):
        parse_idp_metadata(agg, entity_id=None)
    info = parse_idp_metadata(agg, entity_id="https://other.example.org/saml")
    assert set(info.entity_ids) == {IDP_ENTITY_ID, "https://other.example.org/saml"}


def test_doctype_rejected(idp_xml: str) -> None:
    evil = '<!DOCTYPE r [<!ENTITY x "y">]>' + idp_xml.split("?>", 1)[-1]
    with pytest.raises(SamlMetadataError, match="DOCTYPE"):
        parse_idp_metadata(evil, entity_id=None)


def test_expired_valid_until_rejected(idp_xml: str) -> None:
    expired = re.sub(
        r"<((?:\w+:)?EntityDescriptor) ",
        r'<\1 validUntil="2020-01-01T00:00:00Z" ',
        idp_xml,
        count=1,
    )
    assert expired != idp_xml
    with pytest.raises(SamlMetadataError, match="abgelaufen"):
        parse_idp_metadata(expired, entity_id=None)


def test_missing_signing_cert_rejected(idp_xml: str) -> None:
    stripped = re.sub(r"<(\w+:)?KeyDescriptor.*?</(\w+:)?KeyDescriptor>", "", idp_xml, flags=re.S)
    with pytest.raises(SamlMetadataError, match="Signaturzertifikat"):
        parse_idp_metadata(stripped, entity_id=None)


def _public_dns(monkeypatch: pytest.MonkeyPatch, ip: str = PUBLIC) -> None:
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", lambda _h, _p: [ip])


async def test_fetch_http_rejected() -> None:
    with pytest.raises(SamlMetadataError, match="https"):
        await fetch_idp_metadata("http://idp.example.org/md", allow_private_network=False)


async def test_fetch_ok_uses_pinned_ip(monkeypatch: pytest.MonkeyPatch, idp_xml: str) -> None:
    _public_dns(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=idp_xml)

    got = await fetch_idp_metadata(
        "https://idp.example.org/md",
        allow_private_network=False,
        transport=httpx.MockTransport(handler),
    )
    assert got == idp_xml
    assert seen[0].url.host == PUBLIC and seen[0].headers["host"] == "idp.example.org"
    # The 5 MB cap counts bytes on the wire: no compressed body is inflated.
    assert seen[0].headers["accept-encoding"] == "identity"


async def test_fetch_too_large_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, content=b"x" * (6 * 1024 * 1024))
    )
    with pytest.raises(SamlMetadataError, match="zu groß"):
        await fetch_idp_metadata(
            "https://idp.example.org/md", allow_private_network=False, transport=transport
        )


async def test_fetch_loopback_resolution_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch, "127.0.0.1")
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, text="<x/>"))
    with pytest.raises(SamlMetadataError, match="nicht erlaubt"):
        await fetch_idp_metadata(
            "https://idp.example.org/md", allow_private_network=True, transport=transport
        )


async def test_fetch_redirect_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    with pytest.raises(SamlMetadataError, match="HTTP 302"):
        await fetch_idp_metadata(
            "https://idp.example.org/md", allow_private_network=False, transport=transport
        )


async def test_fetch_total_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import tiqora.identity.saml_metadata as md

    _public_dns(monkeypatch)
    monkeypatch.setattr(md, "TOTAL_FETCH_TIMEOUT", 0.05)

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, text="<x/>")

    with pytest.raises(SamlMetadataError, match="rechtzeitig"):
        await fetch_idp_metadata(
            "https://idp.example.org/md",
            allow_private_network=False,
            transport=httpx.MockTransport(slow),
        )
