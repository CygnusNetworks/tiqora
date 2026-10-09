"""IdP metadata: safe parsing (no DTD/entities) and pinned HTTPS fetch."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as safe_et
import httpx

from tiqora.identity.store import ProviderRecord
from tiqora.security.outbound import OutboundURLError, pin_outbound_url

MAX_METADATA_BYTES = 5 * 1024 * 1024
TOTAL_FETCH_TIMEOUT = 15.0  # wall clock for connect + body, on top of httpx's per-phase timeout
_MD = "{urn:oasis:names:tc:SAML:2.0:metadata}"
_DS = "{http://www.w3.org/2000/09/xmldsig#}"
_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


class SamlMetadataError(ValueError):
    """Unusable IdP metadata; the message is German and shown to admins."""


@dataclass(frozen=True, slots=True)
class IdpMetadataInfo:
    entity_ids: tuple[str, ...]
    signing_certs: tuple[str, ...]  # base64 DER as in the XML
    sso_redirect_url: str | None
    slo_redirect_url: str | None
    slo_post_url: str | None
    valid_until: datetime | None


def _parse_instant(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SamlMetadataError("Die Metadaten enthalten ein ungültiges Ablaufdatum.") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _entities(root: Element) -> Iterator[tuple[Element, list[Element]]]:
    """Yield ``(EntityDescriptor, ancestors)`` for every entity in the document."""

    def walk(node: Element, ancestors: list[Element]) -> Iterator[tuple[Element, list[Element]]]:
        if node.tag == f"{_MD}EntityDescriptor":
            yield node, ancestors
        elif node.tag == f"{_MD}EntitiesDescriptor":
            for child in node:
                yield from walk(child, [*ancestors, node])

    yield from walk(root, [])


def _clean_cert(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def _location(idp: Element, service: str, binding: str) -> str | None:
    for el in idp.findall(f"{_MD}{service}"):
        if el.get("Binding") == binding and el.get("Location"):
            return el.get("Location")
    return None


def parse_idp_metadata(xml: str, *, entity_id: str | None) -> IdpMetadataInfo:
    """Parse *xml* and pick one IdP entity (the only one, or the one named *entity_id*)."""
    if len(xml.encode("utf-8", "ignore")) > MAX_METADATA_BYTES:
        raise SamlMetadataError("Die Metadaten sind zu groß.")
    try:
        root = safe_et.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:  # defusedxml.* and ParseError
        raise SamlMetadataError(
            "Die Metadaten sind kein gültiges XML (oder enthalten eine DOCTYPE-Definition)."
        ) from exc
    found = [
        (ed, anc)
        for ed, anc in _entities(root)
        if ed.get("entityID") and ed.find(f"{_MD}IDPSSODescriptor") is not None
    ]
    if not found:
        raise SamlMetadataError("Die Metadaten enthalten keinen Anmeldedienst (IDPSSODescriptor).")
    wanted = (entity_id or "").strip()
    if wanted:
        picked = [(ed, anc) for ed, anc in found if ed.get("entityID") == wanted]
        if not picked:
            raise SamlMetadataError("Die angegebene Entity-ID kommt in den Metadaten nicht vor.")
    elif len(found) > 1:
        raise SamlMetadataError(
            "Die Metadaten enthalten mehrere Anmeldedienste – bitte die Entity-ID angeben."
        )
    else:
        picked = found
    entity, ancestors = picked[0]
    idp = entity.find(f"{_MD}IDPSSODescriptor")
    assert idp is not None

    certs: list[str] = []
    for kd in idp.findall(f"{_MD}KeyDescriptor"):
        if kd.get("use") not in (None, "signing"):
            continue
        for cert in kd.iter(f"{_DS}X509Certificate"):
            value = _clean_cert(cert.text)
            if value and value not in certs:
                certs.append(value)
    if not certs:
        raise SamlMetadataError("Die Metadaten enthalten kein Signaturzertifikat.")

    valid_until: datetime | None = None
    for node in (*ancestors, entity, idp):
        raw = node.get("validUntil")
        if raw:
            when = _parse_instant(raw)
            valid_until = when if valid_until is None else min(valid_until, when)
    if valid_until is not None and valid_until < datetime.now(UTC):
        raise SamlMetadataError(
            "Die Metadaten sind abgelaufen (validUntil liegt in der Vergangenheit)."
        )

    return IdpMetadataInfo(
        entity_ids=tuple(ed.get("entityID") or "" for ed, _ in found),
        signing_certs=tuple(certs),
        sso_redirect_url=_location(idp, "SingleSignOnService", _REDIRECT),
        slo_redirect_url=_location(idp, "SingleLogoutService", _REDIRECT),
        slo_post_url=_location(idp, "SingleLogoutService", _POST),
        valid_until=valid_until,
    )


async def fetch_idp_metadata(
    url: str,
    *,
    allow_private_network: bool,
    timeout: float = 10.0,  # noqa: ASYNC109 - httpx client timeout, signature fixed by D12
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Download metadata over HTTPS from a pinned IP; any failure becomes ``SamlMetadataError``."""
    try:
        pinned = pin_outbound_url(
            url, require_https=True, allow_private_networks=allow_private_network
        )
    except OutboundURLError as exc:
        raise SamlMetadataError(f"Die Metadaten-URL ist nicht erlaubt: {exc}") from exc
    # identity: the 5 MB cap counts what arrives, not a decompressed bomb.
    headers = pinned.request_headers(
        {"Accept": "application/samlmetadata+xml, application/xml", "Accept-Encoding": "identity"}
    )
    try:
        async with (
            asyncio.timeout(TOTAL_FETCH_TIMEOUT),
            httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, transport=transport
            ) as client,
            client.stream(
                "GET", pinned.request_url, headers=headers, extensions=pinned.request_extensions()
            ) as response,
        ):
            if response.status_code != 200:
                raise SamlMetadataError(
                    f"Die Metadaten konnten nicht geladen werden (HTTP {response.status_code})."
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_METADATA_BYTES:
                    raise SamlMetadataError("Die Metadaten sind zu groß (über 5 MB).")
    except TimeoutError as exc:
        raise SamlMetadataError("Die Metadaten konnten nicht rechtzeitig geladen werden.") from exc
    except httpx.HTTPError as exc:
        raise SamlMetadataError(
            f"Die Metadaten konnten nicht geladen werden ({type(exc).__name__})."
        ) from exc
    try:
        return bytes(body).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SamlMetadataError("Die Metadaten sind nicht UTF-8-kodiert.") from exc


def effective_metadata_xml(record: ProviderRecord) -> str | None:
    """Pasted XML wins over the cached download (``record.metadata_xml``)."""
    pasted = str(getattr(record.config, "idp_metadata_xml", "") or "").strip()
    return pasted or record.metadata_xml or None
