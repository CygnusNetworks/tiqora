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
* the issuer must be the configured IdP;
* redirect-binding logout messages (IdP LogoutRequest and LogoutResponse) carry a valid
  ``Signature`` over the raw query octets by a key in the IdP metadata, ``SigAlg`` from the
  SHA-2 allow-list, the configured issuer and our SLS as ``Destination``; a LogoutRequest
  must also be fresh (``IssueInstant``, ``NotOnOrAfter``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import threading
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import unquote_plus, urlencode

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
from tiqora.identity.step_up import STEP_UP_ATTRS
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import UserKind
from tiqora.identity.urls import ProviderUrls, provider_urls

if TYPE_CHECKING:
    from tiqora.identity.sso_sessions import SsoSessionInfo

logger = structlog.get_logger(__name__)
T = TypeVar("T")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# Longest base64 text that can decode to MAX_RESPONSE_BYTES (shared by ACS and SLS).
MAX_MESSAGE_CHARS = MAX_RESPONSE_BYTES * 4 // 3 + 16
NS_ASSERTION = "urn:oasis:names:tc:SAML:2.0:assertion"
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
CM_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
SIG_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
# Redirect-binding SigAlg values we verify; anything else (rsa-sha1, dsa, ...) is refused first.
REDIRECT_SIGALGS: dict[str, str] = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": "SHA256",
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": "SHA384",
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": "SHA512",
}

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
    # every AuthnContextClassRef of the (signed) assertion's AuthnStatements
    authn_context_class_refs: tuple[str, ...] = ()
    # earliest AuthnStatement SessionNotOnOrAfter
    session_not_on_or_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class LogoutRequestData:
    name_id: str
    session_indexes: tuple[str, ...]
    request_id: str = ""  # the LogoutRequest ID (replay cache key)


# An IdP LogoutRequest travels IdP -> browser -> SP at once; older ones are refused, and a
# request id is remembered this long (+ skew) so a captured redirect URL cannot be replayed.
LOGOUT_REQUEST_MAX_AGE = 600


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
                # Read per service section (context sensitive); the pysaml2 default is SHA-1.
                "signing_algorithm": SIG_RSA_SHA256,
                "digest_algorithm": DIGEST_SHA256,
                "endpoints": {
                    "assertion_consumer_service": [
                        (urls.agent_acs_url, saml2.BINDING_HTTP_POST),
                        (urls.customer_acs_url, saml2.BINDING_HTTP_POST),
                    ],
                    # Only the redirect binding is implemented; advertise nothing else.
                    "single_logout_service": [(urls.slo_url, saml2.BINDING_HTTP_REDIRECT)],
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


def _weak_algorithms(root: Any) -> list[str]:
    """SHA-1/MD5 signature or digest algorithms used by any ``ds:SignedInfo`` under *root*.

    Only ``SignedInfo`` is inspected: the digest inside ``xenc:EncryptionMethod``
    (RSA-OAEP) is not a signature.
    """
    weak: list[str] = []
    for signed_info in root.iter(f"{{{NS_DSIG}}}SignedInfo"):
        for tag in ("SignatureMethod", "DigestMethod"):
            for el in signed_info.iter(f"{{{NS_DSIG}}}{tag}"):
                alg = (el.get("Algorithm") or "").lower()
                if alg.endswith("sha1") or "md5" in alg:
                    weak.append(alg)
    return weak


def _expect_xml_safe(raw: bytes, *, allow_sha1: bool) -> None:
    """DTD/entity pre-check, single-assertion rule and weak-signature rule on the raw bytes."""
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
    if not allow_sha1 and _weak_algorithms(root):
        raise SamlResponseInvalid("sha1_signature")


def check_message_size(value: str) -> None:
    """Size cap for any POSTed or redirect-bound SAML message, before it is decoded."""
    if len(value) > MAX_MESSAGE_CHARS:
        raise SamlResponseInvalid("too_large")


def inflate_redirect_message(b64: str) -> bytes:
    """Decode + DEFLATE-inflate a redirect-binding message with hard caps (no inflate bomb),
    then refuse DTDs/entities. Call only AFTER the redirect signature verified."""
    check_message_size(b64)
    try:
        raw = base64.b64decode(b64.encode("ascii", "ignore"), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise SamlResponseInvalid("base64") from exc
    inflater = zlib.decompressobj(-15)
    try:
        xml = inflater.decompress(raw, MAX_RESPONSE_BYTES)
    except zlib.error as exc:
        raise SamlResponseInvalid("inflate") from exc
    if inflater.unconsumed_tail or len(xml) >= MAX_RESPONSE_BYTES:
        raise SamlResponseInvalid("too_large")
    try:
        safe_et.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except defusedxml.DefusedXmlException as exc:
        raise SamlResponseInvalid("dtd") from exc
    except Exception as exc:
        raise SamlResponseInvalid("xml") from exc
    return xml


def decode_saml_b64(b64: str) -> bytes:
    check_message_size(b64)
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
        extra: dict[str, Any] = {}
        policy = self._record.mfa_policy
        if policy.saml_request_authn_context and policy.saml_authn_context_classes:
            # Comparison "minimum": the IdP may use a stronger method than the listed class.
            extra["requested_authn_context"] = {
                "authn_context_class_ref": list(policy.saml_authn_context_classes),
                "comparison": "minimum",
            }
        req_id, info = client.prepare_for_authenticate(
            entityid=self._idp_id(),
            relay_state=relay_state,
            binding=BINDING_HTTP_REDIRECT,
            nameid_format=NAME_ID_FORMATS[self._cfg.name_id_format],
            sign=self._cfg.sign_requests,
            assertion_consumer_service_url=acs,
            **extra,
        )
        return SamlRedirect(request_id=str(req_id), url=_location(info))

    async def parse_login_response(
        self, saml_response_b64: str, *, outstanding: Mapping[str, str], expected_acs: str
    ) -> SamlAssertionResult:
        raw = decode_saml_b64(saml_response_b64)
        try:
            return await self._run(self._parse_login_sync, raw, dict(outstanding), expected_acs)
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
        self, raw: bytes, outstanding: dict[str, str], expected_acs: str
    ) -> SamlAssertionResult:
        from saml2 import BINDING_HTTP_POST  # noqa: PLC0415

        allow_sha1 = self._cfg.allow_sha1_signatures
        _expect_xml_safe(raw, allow_sha1=allow_sha1)
        # pysaml2 sees exactly the bytes that were checked.
        b64 = base64.b64encode(raw).decode("ascii")
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
        if not allow_sha1 and _weak_algorithms(_parse_trusted(assertion.to_string())):
            raise SamlResponseInvalid("sha1_signature")  # e.g. inside an encrypted assertion
        # The signed assertion must come from the configured IdP (aggregate metadata may
        # hold other IdPs whose keys would verify); a response issuer, if any, must match too.
        idp_id = self._idp_id()
        if _text(assertion.issuer) != idp_id:
            raise SamlResponseInvalid("issuer_mismatch")
        response_issuer = _text(getattr(resp.response, "issuer", None))
        if response_issuer is not None and response_issuer != idp_id:
            raise SamlResponseInvalid("issuer_mismatch")

        destination = getattr(resp.response, "destination", None) or None
        if destination is not None and destination != expected_acs:
            raise SamlResponseInvalid("destination_mismatch")
        confirmation = _matching_confirmation(assertion, expected_acs)
        if confirmation is None:
            raise SamlResponseInvalid("recipient_mismatch")
        # The InResponseTo inside the signed SubjectConfirmationData is authoritative; the
        # unsigned root attribute may only agree with it. Any value must be an outstanding request.
        root_irt = getattr(resp, "in_response_to", None) or None
        signed_irt = getattr(confirmation, "in_response_to", None) or None
        if signed_irt is not None and root_irt is not None and signed_irt != root_irt:
            raise SamlResponseInvalid("in_response_to_mismatch")
        in_response_to = signed_irt or root_irt
        if in_response_to is not None and in_response_to not in outstanding:
            raise SamlResponseInvalid("unknown_request")

        name_id_obj = getattr(resp, "name_id", None)
        name_id = _text(name_id_obj)
        if not name_id:
            raise SamlResponseInvalid("no_name_id")
        session_index = None
        statements = getattr(assertion, "authn_statement", None) or []
        if statements:
            session_index = getattr(statements[0], "session_index", None) or None
        conditions = getattr(assertion, "conditions", None)
        # Earliest signed expiry (Conditions or bearer SCD) bounds the replay cache; an
        # assertion without any would never expire from it.
        expiries = [
            e
            for e in (
                _instant(getattr(conditions, "not_on_or_after", None)),
                _instant(getattr(confirmation, "not_on_or_after", None)),
            )
            if e is not None
        ]
        if not expiries:
            raise SamlResponseInvalid("no_expiry")
        attributes = normalize_attributes(dict(getattr(resp, "ava", None) or {}))
        # an assertion attribute must never pose as one of our injected proof attributes
        for reserved in STEP_UP_ATTRS:
            attributes.pop(reserved, None)
        attributes["saml:name_id"] = (name_id,)
        class_refs = tuple(
            dict.fromkeys(
                ref
                for st in statements
                if (
                    ref := _text(
                        getattr(getattr(st, "authn_context", None), "authn_context_class_ref", None)
                    )
                )
            )
        )
        session_ends = [
            e
            for e in (_instant(getattr(st, "session_not_on_or_after", None)) for st in statements)
            if e
        ]
        return SamlAssertionResult(
            in_response_to=in_response_to,
            assertion_id=str(assertion.id),
            not_on_or_after=min(expiries),
            name_id=name_id,
            name_id_format=getattr(name_id_obj, "format", None) or None,
            name_qualifier=getattr(name_id_obj, "name_qualifier", None) or None,
            sp_name_qualifier=getattr(name_id_obj, "sp_name_qualifier", None) or None,
            session_index=session_index,
            attributes=attributes,
            destination=destination,
            authn_context_class_refs=class_refs,
            session_not_on_or_after=min(session_ends) if session_ends else None,
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

    async def parse_logout_response(
        self,
        saml_response: str,
        *,
        binding: str,
        relay_state: str | None = None,
        sigalg: str | None = None,
        signature: str | None = None,
        raw_query: str | None = None,
    ) -> str | None:
        """``InResponseTo`` of a verified IdP LogoutResponse (the caller matches it to its state).

        Redirect binding only (the only SLS binding in our metadata): signature over the raw
        query octets, SHA-2 SigAlg, issuer, destination and Success status are all required.
        """
        try:
            return await self._run(
                self._parse_logout_response_sync,
                saml_response,
                binding,
                relay_state,
                sigalg,
                signature,
                raw_query,
            )
        except SamlResponseInvalid:
            raise
        except Exception as exc:
            raise SamlResponseInvalid(type(exc).__name__, str(exc)[:300]) from exc

    def _verify_redirect_signature(
        self,
        client: Any,
        param: str,
        value: str,
        relay_state: str | None,
        sigalg: str | None,
        signature: str | None,
        raw_query: str | None,
    ) -> None:
        if not sigalg or not signature:
            raise SamlResponseInvalid("logout_unsigned")
        if sigalg not in REDIRECT_SIGALGS:
            raise SamlResponseInvalid("logout_sigalg")
        certs = client.metadata.certs(self._idp_id(), "any", "signing")
        signed = _redirect_signed_octets(param, value, relay_state, sigalg, signature, raw_query)
        if not any(_rsa_verify(cert, signed, signature, sigalg) for _name, cert in certs):
            raise SamlResponseInvalid("logout_signature")

    def _parse_logout_response_sync(
        self,
        saml_response: str,
        binding: str,
        relay_state: str | None,
        sigalg: str | None,
        signature: str | None,
        raw_query: str | None,
    ) -> str | None:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

        client = self._ensure()
        if binding != BINDING_HTTP_REDIRECT:
            raise SamlResponseInvalid("logout_binding")
        # Size cap, then the signature over the raw query: nothing of an unverified message is
        # decoded, inflated or parsed. Then a bounded inflate + DTD check before pysaml2.
        check_message_size(saml_response)
        self._verify_redirect_signature(
            client, "SAMLResponse", saml_response, relay_state, sigalg, signature, raw_query
        )
        inflate_redirect_message(saml_response)
        resp = client.parse_logout_request_response(saml_response, binding)
        if resp is None:
            raise SamlResponseInvalid("no_response")
        message = resp.response
        if _text(getattr(message, "issuer", None)) != self._idp_id():
            raise SamlResponseInvalid("issuer_mismatch")
        destination = _text_attr(message, "destination")
        if destination is not None and destination != self._urls.slo_url:
            raise SamlResponseInvalid("destination_mismatch")
        status = getattr(getattr(message, "status", None), "status_code", None)
        if getattr(status, "value", None) != STATUS_SUCCESS:
            raise SamlResponseInvalid("logout_status")
        return getattr(resp, "in_response_to", None) or None

    async def handle_logout_request(
        self,
        saml_request: str,
        *,
        binding: str,
        relay_state: str | None,
        sigalg: str | None,
        signature: str | None,
        raw_query: str | None = None,
    ) -> tuple[LogoutRequestData, str]:
        """Verify and answer an IdP LogoutRequest (redirect binding only).

        ``raw_query`` is the request's undecoded query string: the signature covers the
        original URL-encoded octets (SAML Bindings 3.4.4.1), so pass it whenever available.
        """
        try:
            return await self._run(
                self._handle_logout_sync,
                saml_request,
                binding,
                relay_state,
                sigalg,
                signature,
                raw_query,
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
        raw_query: str | None,
    ) -> tuple[LogoutRequestData, str]:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

        client = self._ensure()
        if binding != BINDING_HTTP_REDIRECT:
            raise SamlResponseInvalid("logout_binding")
        # pysaml2 only checks a redirect signature when ITS idp-scoped "want signed" flag is
        # set, which an SP config never has. Order: size cap, signature over the raw query
        # octets (the IdP's metadata keys), and only then decode/inflate (bounded) and parse.
        check_message_size(saml_request)
        self._verify_redirect_signature(
            client, "SAMLRequest", saml_request, relay_state, sigalg, signature, raw_query
        )
        inflate_redirect_message(saml_request)
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
        destination = _text_attr(req.message, "destination")
        if destination != self._urls.slo_url:
            raise SamlResponseInvalid("destination_mismatch")
        _expect_fresh(req.message, self._cfg.clock_skew_seconds)
        request_id = _text_attr(req.message, "id")
        if not request_id:
            raise SamlResponseInvalid("no_request_id")
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
        return (
            LogoutRequestData(name_id=name_id, session_indexes=indexes, request_id=request_id),
            _location(http),
        )


def _expect_fresh(message: Any, skew: int) -> None:
    """IssueInstant within the last LOGOUT_REQUEST_MAX_AGE (+ skew), NotOnOrAfter not passed.

    pysaml2 computes a +-1 day window for IssueInstant but ignores its own result.
    """
    now = datetime.now(UTC)
    issued = _instant(getattr(message, "issue_instant", None))
    if issued is None:
        raise SamlResponseInvalid("issue_instant")
    age = (now - issued).total_seconds()
    if age > LOGOUT_REQUEST_MAX_AGE + skew or age < -skew:
        raise SamlResponseInvalid("issue_instant")
    expires = _instant(getattr(message, "not_on_or_after", None))
    if expires is not None and (now - expires).total_seconds() > skew:
        raise SamlResponseInvalid("not_on_or_after")


def _reason_for(exc: Exception) -> str:
    """pysaml2 raises a bare ``Exception`` for audience mismatches; name that one."""
    if type(exc) is Exception and "AudienceRestrictions" in str(exc):
        return "audience_mismatch"
    return type(exc).__name__


def _redirect_signed_octets(
    param: str,
    value: str,
    relay_state: str | None,
    sigalg: str,
    signature: str,
    raw_query: str | None,
) -> bytes:
    """``PARAM=..&RelayState=..&SigAlg=..`` exactly as the IdP signed it.

    With the raw query the original percent-encoding is reused verbatim; the decoded
    fallback re-encodes (which only matches IdPs that encode like Python).
    """
    if raw_query is not None:
        signed_names = (param, "RelayState", "SigAlg", "Signature")
        raw: dict[str, str] = {}
        for pair in raw_query.split("&"):
            key, _sep, val = pair.partition("=")
            name = unquote_plus(key)
            if name not in signed_names and name not in ("SAMLRequest", "SAMLResponse"):
                continue  # unrelated parameter, not part of the signed string
            # A repeated or oddly spelled key could make the parser (last wins) and this
            # verifier (first wins) read different messages: refuse outright.
            if name in raw or key != name:
                raise SamlResponseInvalid("logout_signature")
            raw[name] = val
        for other in ("SAMLRequest", "SAMLResponse"):
            if other != param and other in raw:
                raise SamlResponseInvalid("logout_signature")
        # Every value that is parsed must be the very value that was signed.
        if param not in raw or "SigAlg" not in raw or "Signature" not in raw:
            raise SamlResponseInvalid("logout_signature")
        if unquote_plus(raw[param]) != value or unquote_plus(raw["SigAlg"]) != sigalg:
            raise SamlResponseInvalid("logout_signature")
        if unquote_plus(raw["Signature"]) != signature:
            raise SamlResponseInvalid("logout_signature")
        raw_relay = unquote_plus(raw["RelayState"]) if "RelayState" in raw else None
        if raw_relay != relay_state:
            raise SamlResponseInvalid("logout_signature")
        parts = [f"{param}={raw[param]}"]
        if "RelayState" in raw:
            parts.append(f"RelayState={raw['RelayState']}")
        parts.append(f"SigAlg={raw['SigAlg']}")
        return "&".join(parts).encode("ascii")
    parts = [urlencode({param: value})]
    if relay_state is not None:
        parts.append(urlencode({"RelayState": relay_state}))
    parts.append(urlencode({"SigAlg": sigalg}))
    return "&".join(parts).encode("ascii")


def _text_attr(obj: Any, name: str) -> str | None:
    value = getattr(obj, name, None)
    return str(value) if value else None


def _as_bytes(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode("ascii")


def _rsa_verify(cert_b64: str, signed: bytes, signature_b64: str, sigalg: str) -> bool:
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: PLC0415
    from saml2.sigver import pem_format  # noqa: PLC0415

    try:
        key = x509.load_pem_x509_certificate(_as_bytes(pem_format(cert_b64))).public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            return False
        digest = getattr(hashes, REDIRECT_SIGALGS[sigalg])()
        key.verify(base64.b64decode(signature_b64), signed, padding.PKCS1v15(), digest)
    except Exception:  # InvalidSignature, bad cert/base64: all mean "not verified"
        return False
    return True


def _first_slo(client: Any, entity_id: str) -> str:
    from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

    urls = client.metadata.single_logout_service(entity_id, BINDING_HTTP_REDIRECT, "idpsso")
    if not urls:
        raise SamlResponseInvalid("no_idp_slo")
    return str(urls[0]["location"])


def _parse_trusted(xml: str) -> Any:
    return safe_et.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def _matching_confirmation(assertion: Any, expected_acs: str) -> Any | None:
    """The bearer SubjectConfirmationData (inside the signed assertion) naming this ACS."""
    subject = getattr(assertion, "subject", None)
    for confirmation in getattr(subject, "subject_confirmation", None) or []:
        if getattr(confirmation, "method", None) != CM_BEARER:
            continue
        data = getattr(confirmation, "subject_confirmation_data", None)
        if data is not None and getattr(data, "recipient", None) == expected_acs:
            return data
    return None


def _location(http_info: Mapping[str, Any]) -> str:
    for key, value in http_info.get("headers", []):
        if str(key).lower() == "location":
            return str(value)
    raise SamlResponseInvalid("no_redirect_location")
