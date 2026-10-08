"""SAML service provider: pysaml2 wrapper with strict response validation.

pysaml2 stays behind this module. Every pysaml2 call runs in the default executor
(it shells out to ``xmlsec1`` and parses XML). On top of pysaml2's own checks
(signature against the metadata keys, audience, validity window, ``InResponseTo``)
this module enforces what pysaml2 does not:

* the XML must carry no DTD/entities (``defusedxml``) and exactly ONE assertion
  anywhere in the document (signature-wrapping defence);
* the assertion itself must be signed;
* the signed ``SubjectConfirmationData/@Recipient`` must equal the ACS URL the
  response was posted to; ``Destination`` (unsigned unless the response is) must
  equal it too when present;
* an ``InResponseTo`` must be one of the outstanding requests of this browser flow;
* the issuer must be the configured IdP.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar

import defusedxml
import defusedxml.ElementTree as safe_et
import structlog

from tiqora.config import Settings
from tiqora.identity.config_models import SamlProviderConfig
from tiqora.identity.mapping import normalize_attributes
from tiqora.identity.saml_keys import KeyFiles, materialize_keypair
from tiqora.identity.saml_metadata import (
    SamlMetadataError,
    effective_metadata_xml,
    parse_idp_metadata,
)
from tiqora.identity.saml_runtime import (
    SamlAvailability,
    SamlUnavailable,
    import_saml2,
    saml_availability,
)
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import UserKind
from tiqora.identity.urls import ProviderUrls, provider_urls

if TYPE_CHECKING:
    from tiqora.identity.sso_sessions import SsoSessionInfo

logger = structlog.get_logger(__name__)
T = TypeVar("T")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
NS_ASSERTION = "urn:oasis:names:tc:SAML:2.0:assertion"
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
CM_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"

NAME_ID_FORMATS: dict[str, str] = {
    "unspecified": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
    "emailAddress": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    "persistent": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    "transient": "urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
}


class SamlResponseInvalid(Exception):  # noqa: N818 - domain term
    """A SAML message was rejected; ``reason`` is logged, never shown to the user."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail  # pysaml2's message; for logs and tests only


@dataclass(frozen=True, slots=True)
class SamlRedirect:
    request_id: str
    url: str


@dataclass(frozen=True, slots=True)
class SamlAssertionResult:
    in_response_to: str | None
    assertion_id: str
    not_on_or_after: datetime | None
    name_id: str
    name_id_format: str | None
    name_qualifier: str | None
    sp_name_qualifier: str | None
    session_index: str | None
    attributes: dict[str, tuple[str, ...]]
    destination: str | None


@dataclass(frozen=True, slots=True)
class LogoutRequestData:
    name_id: str
    session_indexes: tuple[str, ...]


def _sp_conf(
    record: ProviderRecord,
    settings: Settings,
    availability: SamlAvailability,
    key_files: KeyFiles,
    idp_metadata_xml: str,
) -> dict[str, Any]:
    """The pysaml2 SP configuration dict (D12)."""
    saml2 = import_saml2()
    cfg = _cfg(record)
    urls = provider_urls(settings, record)
    key = {"key_file": key_files.key_path, "cert_file": key_files.cert_path}
    return {
        "entityid": urls.sp_entity_id,
        "xmlsec_binary": availability.xmlsec_binary,
        **key,
        "encryption_keypairs": [dict(key)],
        "metadata": {"inline": [idp_metadata_xml]},
        "accepted_time_diff": cfg.clock_skew_seconds,
        "allow_unknown_attributes": True,
        "only_use_keys_in_metadata": True,
        "service": {
            "sp": {
                "endpoints": {
                    "assertion_consumer_service": [
                        (urls.agent_acs_url, saml2.BINDING_HTTP_POST),
                        (urls.customer_acs_url, saml2.BINDING_HTTP_POST),
                    ],
                    "single_logout_service": [
                        (urls.slo_url, saml2.BINDING_HTTP_REDIRECT),
                        (urls.slo_url, saml2.BINDING_HTTP_POST),
                    ],
                },
                "want_assertions_signed": True,
                "want_response_signed": cfg.want_response_signed,
                "authn_requests_signed": cfg.sign_requests,
                "logout_requests_signed": True,
                "allow_unsolicited": cfg.allow_idp_initiated,
                "name_id_policy_format": NAME_ID_FORMATS[cfg.name_id_format],
            }
        },
    }


def _cfg(record: ProviderRecord) -> SamlProviderConfig:
    cfg = record.config
    if not isinstance(cfg, SamlProviderConfig):
        raise TypeError("not a SAML provider record")
    return cfg


def _expect_xml_safe(raw: bytes) -> None:
    """DTD/entity pre-check and the single-assertion rule on the raw bytes pysaml2 will see."""
    try:
        root = safe_et.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except defusedxml.DefusedXmlException as exc:
        raise SamlResponseInvalid("dtd") from exc
    except Exception as exc:
        raise SamlResponseInvalid("xml") from exc
    count = sum(
        1
        for el in root.iter()
        if el.tag in (f"{{{NS_ASSERTION}}}Assertion", f"{{{NS_ASSERTION}}}EncryptedAssertion")
    )
    if count != 1:
        raise SamlResponseInvalid("assertion_count")


def _decode_b64(b64: str) -> bytes:
    if len(b64) > MAX_RESPONSE_BYTES * 4 // 3 + 16:
        raise SamlResponseInvalid("too_large")
    try:
        raw = base64.b64decode(b64.encode("ascii", "ignore"))
    except (binascii.Error, ValueError) as exc:
        raise SamlResponseInvalid("base64") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SamlResponseInvalid("too_large")
    if not raw.strip():
        raise SamlResponseInvalid("empty")
    return raw


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(node: Any) -> str | None:
    value = getattr(node, "text", None)
    return str(value).strip() if value else None


class SamlServiceProvider:
    """One SAML provider row as pysaml2 service provider (config built lazily, cached)."""

    def __init__(self, record: ProviderRecord, *, settings: Settings) -> None:
        self._record = record
        self._cfg = _cfg(record)
        self._settings = settings
        self._urls: ProviderUrls = provider_urls(settings, record)
        self._lock = threading.Lock()
        self._client: Any = None
        self._idp_entity_id: str | None = None
        self._idp_slo: str | None = None

    # ------------------------------------------------------------------ plumbing

    @property
    def sp_certificate_pem(self) -> str:
        return self._cfg.sp_certificate

    @property
    def urls(self) -> ProviderUrls:
        return self._urls

    @staticmethod
    async def _run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))

    def _ensure(self) -> Any:
        """Build (once) and return the ``Saml2Client``. Blocking: call inside the executor."""
        with self._lock:
            if self._client is not None:
                return self._client
            availability = saml_availability()
            if not availability.available:
                raise SamlUnavailable(f"SAML runtime unavailable: {availability.missing}")
            xml = effective_metadata_xml(self._record)
            if not xml:
                raise SamlMetadataError("Es sind keine Metadaten des Anmeldedienstes hinterlegt.")
            info = parse_idp_metadata(xml, entity_id=self._cfg.idp_entity_id or None)
            idp_id = self._cfg.idp_entity_id or info.entity_ids[0]
            secret = self._cfg.sp_private_key
            if secret is None or not self._cfg.sp_certificate:
                raise SamlMetadataError("Das Zertifikat des Dienstes fehlt.")
            key_files = materialize_keypair(secret.get_secret_value(), self._cfg.sp_certificate)
            conf = _sp_conf(self._record, self._settings, availability, key_files, xml)
            from saml2.client import Saml2Client  # noqa: PLC0415
            from saml2.config import SPConfig  # noqa: PLC0415

            sp_config = SPConfig()
            sp_config.load(conf)
            sp_config.allow_unknown_attributes = True
            self._client = Saml2Client(config=sp_config)
            self._idp_entity_id = idp_id
            self._idp_slo = info.slo_redirect_url
            return self._client

    def _idp_id(self) -> str:
        self._ensure()
        assert self._idp_entity_id is not None
        return self._idp_entity_id

    # ------------------------------------------------------------------ metadata

    def sp_metadata_xml(self) -> str:
        client = self._ensure()
        from saml2.metadata import create_metadata_string  # noqa: PLC0415

        out = create_metadata_string(None, config=client.config)
        return out.decode("utf-8") if isinstance(out, bytes) else str(out)

    # ------------------------------------------------------------------ login

    async def login_redirect(self, *, relay_state: str, user_kind: UserKind) -> SamlRedirect:
        return await self._run(self._login_redirect_sync, relay_state, user_kind)

    def _login_redirect_sync(self, relay_state: str, user_kind: UserKind) -> SamlRedirect:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

        client = self._ensure()
        acs = self._urls.agent_acs_url if user_kind == "agent" else self._urls.customer_acs_url
        req_id, info = client.prepare_for_authenticate(
            entityid=self._idp_id(),
            relay_state=relay_state,
            binding=BINDING_HTTP_REDIRECT,
            nameid_format=NAME_ID_FORMATS[self._cfg.name_id_format],
            sign=self._cfg.sign_requests,
            assertion_consumer_service_url=acs,
        )
        return SamlRedirect(request_id=str(req_id), url=_location(info))

    async def parse_login_response(
        self, saml_response_b64: str, *, outstanding: Mapping[str, str], expected_acs: str
    ) -> SamlAssertionResult:
        raw = _decode_b64(saml_response_b64)
        _expect_xml_safe(raw)
        canonical = base64.b64encode(raw).decode("ascii")  # pysaml2 sees exactly what we checked
        try:
            return await self._run(
                self._parse_login_sync, canonical, dict(outstanding), expected_acs
            )
        except SamlResponseInvalid:
            raise
        except Exception as exc:
            logger.warning(
                "saml_response_rejected",
                slug=self._record.slug,
                error=type(exc).__name__,
                detail=str(exc)[:300],
            )
            raise SamlResponseInvalid(_reason_for(exc), str(exc)[:300]) from exc

    def _parse_login_sync(
        self, b64: str, outstanding: dict[str, str], expected_acs: str
    ) -> SamlAssertionResult:
        from saml2 import BINDING_HTTP_POST  # noqa: PLC0415

        client = self._ensure()
        resp = client.parse_authn_request_response(b64, BINDING_HTTP_POST, outstanding=outstanding)
        if resp is None:
            raise SamlResponseInvalid("no_response")
        assertions = list(getattr(resp, "assertions", None) or [])
        assertion = getattr(resp, "assertion", None)
        if assertion is None or len(assertions) != 1:
            raise SamlResponseInvalid("assertion_count")
        # pysaml2 enforces want_assertions_signed; assert it again (also after decryption).
        if not getattr(assertion, "signature", None):
            raise SamlResponseInvalid("assertion_unsigned")
        if _text(resp.issuer()) != self._idp_id() and _text(assertion.issuer) != self._idp_id():
            raise SamlResponseInvalid("issuer_mismatch")

        in_response_to = getattr(resp, "in_response_to", None) or None
        if in_response_to is not None and in_response_to not in outstanding:
            raise SamlResponseInvalid("unknown_request")
        destination = getattr(resp.response, "destination", None) or None
        if destination is not None and destination != expected_acs:
            raise SamlResponseInvalid("destination_mismatch")
        if not _recipient_matches(assertion, expected_acs):
            raise SamlResponseInvalid("recipient_mismatch")

        name_id_obj = getattr(resp, "name_id", None)
        name_id = _text(name_id_obj)
        if not name_id:
            raise SamlResponseInvalid("no_name_id")
        session_index = None
        statements = getattr(assertion, "authn_statement", None) or []
        if statements:
            session_index = getattr(statements[0], "session_index", None) or None
        conditions = getattr(assertion, "conditions", None)
        attributes = normalize_attributes(dict(getattr(resp, "ava", None) or {}))
        attributes["saml:name_id"] = (name_id,)
        return SamlAssertionResult(
            in_response_to=in_response_to,
            assertion_id=str(assertion.id),
            not_on_or_after=_instant(getattr(conditions, "not_on_or_after", None)),
            name_id=name_id,
            name_id_format=getattr(name_id_obj, "format", None) or None,
            name_qualifier=getattr(name_id_obj, "name_qualifier", None) or None,
            sp_name_qualifier=getattr(name_id_obj, "sp_name_qualifier", None) or None,
            session_index=session_index,
            attributes=attributes,
            destination=destination,
        )

    # ------------------------------------------------------------------ logout

    async def logout_redirect(
        self, info: SsoSessionInfo, *, relay_state: str
    ) -> SamlRedirect | None:
        return await self._run(self._logout_redirect_sync, info, relay_state)

    def _logout_redirect_sync(self, info: SsoSessionInfo, relay_state: str) -> SamlRedirect | None:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415
        from saml2.saml import NameID  # noqa: PLC0415

        client = self._ensure()
        if not self._idp_slo or not info.name_id:
            return None
        name_id = NameID(
            text=info.name_id,
            format=info.name_id_format,
            name_qualifier=info.name_qualifier,
            sp_name_qualifier=info.sp_name_qualifier,
        )
        req_id, request = client.create_logout_request(
            self._idp_slo,
            self._idp_id(),
            name_id=name_id,
            session_indexes=[info.session_index] if info.session_index else None,
            sign=False,
        )
        http = client.apply_binding(
            BINDING_HTTP_REDIRECT, str(request), self._idp_slo, relay_state, sign=True
        )
        return SamlRedirect(request_id=str(req_id), url=_location(http))

    async def parse_logout_response(self, saml_response: str, *, binding: str) -> str | None:
        """``InResponseTo`` of an IdP LogoutResponse (the caller matches it to its state)."""
        try:
            return await self._run(self._parse_logout_response_sync, saml_response, binding)
        except SamlResponseInvalid:
            raise
        except Exception as exc:
            raise SamlResponseInvalid(type(exc).__name__, str(exc)[:300]) from exc

    def _parse_logout_response_sync(self, saml_response: str, binding: str) -> str | None:
        client = self._ensure()
        resp = client.parse_logout_request_response(saml_response, binding)
        if resp is None:
            raise SamlResponseInvalid("no_response")
        return getattr(resp, "in_response_to", None) or None

    async def handle_logout_request(
        self,
        saml_request: str,
        *,
        binding: str,
        relay_state: str | None,
        sigalg: str | None,
        signature: str | None,
    ) -> tuple[LogoutRequestData, str]:
        try:
            return await self._run(
                self._handle_logout_sync, saml_request, binding, relay_state, sigalg, signature
            )
        except SamlResponseInvalid:
            raise
        except Exception as exc:
            logger.warning("saml_logout_rejected", slug=self._record.slug, error=type(exc).__name__)
            raise SamlResponseInvalid(type(exc).__name__, str(exc)[:300]) from exc

    def _handle_logout_sync(
        self,
        saml_request: str,
        binding: str,
        relay_state: str | None,
        sigalg: str | None,
        signature: str | None,
    ) -> tuple[LogoutRequestData, str]:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415
        from saml2.sigver import verify_redirect_signature  # noqa: PLC0415

        client = self._ensure()
        if binding != BINDING_HTTP_REDIRECT:
            raise SamlResponseInvalid("logout_binding")
        # pysaml2 only checks a redirect signature when ITS idp-scoped "want signed" flag is
        # set, which an SP config never has: verify it here and refuse unsigned requests.
        if not sigalg or not signature:
            raise SamlResponseInvalid("logout_unsigned")
        req = client.parse_logout_request(
            saml_request,
            binding,
            relay_state=relay_state,
            sigalg=sigalg,
            signature=signature,
        )
        if req is None or req.message is None:
            raise SamlResponseInvalid("no_request")
        issuer = _text(req.message.issuer)
        if issuer != self._idp_id():
            raise SamlResponseInvalid("issuer_mismatch")
        signed_msg = {"SAMLRequest": saml_request, "Signature": signature, "SigAlg": sigalg}
        if relay_state is not None:
            signed_msg["RelayState"] = relay_state
        certs = client.metadata.certs(issuer, "any", "signing")
        if not any(
            verify_redirect_signature(signed_msg, client.sec.sec_backend, cert)
            for _name, cert in certs
        ):
            raise SamlResponseInvalid("logout_signature")
        name_id = _text(req.message.name_id)
        if not name_id:
            raise SamlResponseInvalid("no_name_id")
        indexes = tuple(str(i.text).strip() for i in (req.message.session_index or []) if _text(i))
        response = client.create_logout_response(req.message, [BINDING_HTTP_REDIRECT], sign=False)
        http = client.apply_binding(
            BINDING_HTTP_REDIRECT,
            str(response),
            self._idp_slo or _first_slo(client, issuer),
            relay_state or "",
            response=True,
            sign=True,
        )
        return LogoutRequestData(name_id=name_id, session_indexes=indexes), _location(http)


def _reason_for(exc: Exception) -> str:
    """pysaml2 raises a bare ``Exception`` for audience mismatches; name that one."""
    if type(exc) is Exception and "AudienceRestrictions" in str(exc):
        return "audience_mismatch"
    return type(exc).__name__


def _first_slo(client: Any, entity_id: str) -> str:
    from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

    urls = client.metadata.single_logout_service(entity_id, BINDING_HTTP_REDIRECT, "idpsso")
    if not urls:
        raise SamlResponseInvalid("no_idp_slo")
    return str(urls[0]["location"])


def _recipient_matches(assertion: Any, expected_acs: str) -> bool:
    """A bearer SubjectConfirmationData inside the (signed) assertion must name this ACS."""
    subject = getattr(assertion, "subject", None)
    for confirmation in getattr(subject, "subject_confirmation", None) or []:
        if getattr(confirmation, "method", None) != CM_BEARER:
            continue
        data = getattr(confirmation, "subject_confirmation_data", None)
        if data is not None and getattr(data, "recipient", None) == expected_acs:
            return True
    return False


def _location(http_info: Mapping[str, Any]) -> str:
    for key, value in http_info.get("headers", []):
        if str(key).lower() == "location":
            return str(value)
    raise SamlResponseInvalid("no_redirect_location")
