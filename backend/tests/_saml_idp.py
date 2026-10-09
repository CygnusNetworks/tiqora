"""Shared helpers for SAML tests (Task 9 adds the test IdP here)."""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tiqora.config import Settings
from tiqora.identity.config_models import AttributeMap, JitDefaults, SamlProviderConfig
from tiqora.identity.saml_keys import generate_sp_keypair
from tiqora.identity.store import ProviderRecord


def require_saml() -> None:
    """Skip (or fail under TIQORA_REQUIRE_SAML_TESTS=1) when pysaml2/xmlsec1 are missing."""
    msg = None
    if importlib.util.find_spec("saml2") is None:
        msg = "pysaml2 missing (uv sync --extra saml)"
    elif shutil.which("xmlsec1") is None:
        msg = "xmlsec1 binary missing (brew install libxmlsec1 / apt install xmlsec1)"
    if msg is None:
        return
    if os.environ.get("TIQORA_REQUIRE_SAML_TESTS") == "1":
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg, allow_module_level=True)


# --------------------------------------------------------------------------- test IdP

IDP_ENTITY_ID = "https://idp.example.org/saml"
IDP_SSO_URL = "https://idp.example.org/saml/sso"
IDP_SLO_URL = "https://idp.example.org/saml/slo"
SIG_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
SIG_RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
DIGEST_SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
NAMEID_FORMAT_PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"


def make_test_cert(common_name: str, key_path: Path, cert_path: Path) -> str:
    """Write a fresh RSA key + self-signed cert (test only); return the cert PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    cert_path.write_text(pem)
    return pem


class SamlTestIdp:
    """In-process pysaml2 IdP with a fresh key pair; trusts exactly the SP metadata it is given."""

    def __init__(self, tmp_path: Path, *, entity_id: str = IDP_ENTITY_ID) -> None:
        self.entity_id = entity_id
        self.key_path = tmp_path / "idp.key"
        self.cert_path = tmp_path / "idp.crt"
        self.cert_pem = make_test_cert("idp.example.org", self.key_path, self.cert_path)
        self._sp_metadata: list[str] = []
        self._server: Any = self._build_server([])

    def _conf(self, sp_metadata: list[str]) -> dict[str, Any]:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

        conf: dict[str, Any] = {
            "entityid": self.entity_id,
            "xmlsec_binary": shutil.which("xmlsec1"),
            "key_file": str(self.key_path),
            "cert_file": str(self.cert_path),
            "service": {
                "idp": {
                    "signing_algorithm": SIG_RSA_SHA256,
                    "digest_algorithm": DIGEST_SHA256,
                    "endpoints": {
                        "single_sign_on_service": [(IDP_SSO_URL, BINDING_HTTP_REDIRECT)],
                        "single_logout_service": [(IDP_SLO_URL, BINDING_HTTP_REDIRECT)],
                    },
                    "policy": {
                        "default": {
                            "lifetime": {"minutes": 15},
                            "attribute_restrictions": None,
                            "name_form": "urn:oasis:names:tc:SAML:2.0:attrname-format:basic",
                        }
                    },
                    "logout_requests_signed": True,
                }
            },
        }
        if sp_metadata:
            conf["metadata"] = {"inline": list(sp_metadata)}
        return conf

    def _build_server(self, sp_metadata: list[str]) -> Any:
        from saml2.config import IdPConfig  # noqa: PLC0415
        from saml2.server import Server  # noqa: PLC0415

        cfg = IdPConfig()
        cfg.load(self._conf(sp_metadata))
        return Server(config=cfg)

    def metadata_xml(self) -> str:
        from saml2.config import IdPConfig  # noqa: PLC0415
        from saml2.metadata import create_metadata_string  # noqa: PLC0415

        cfg = IdPConfig()
        cfg.load(self._conf([]))
        out = create_metadata_string(None, config=cfg)
        return out.decode() if isinstance(out, bytes) else str(out)

    def trust_sp(self, sp_metadata_xml: str) -> None:
        """Trust *sp_metadata_xml* (additive: several SPs can be trusted at once)."""
        self._sp_metadata.append(sp_metadata_xml)
        self._server = self._build_server(self._sp_metadata)

    def parse_login_redirect(self, url: str) -> tuple[str, str, str]:
        """Return ``(request_id, acs_url, relay_state)`` of an SP-initiated redirect."""
        q = parse_qs(urlparse(url).query)
        xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15).decode()
        req_id = re.search(r'\bID="([^"]+)"', xml)
        acs = re.search(r'AssertionConsumerServiceURL="([^"]+)"', xml)
        assert req_id is not None and acs is not None, xml
        return req_id.group(1), acs.group(1), q.get("RelayState", [""])[0]

    def login_response(
        self,
        *,
        in_response_to: str | None,
        acs_url: str,
        sp_entity_id: str,
        name_id: str = "u-1001",
        name_id_format: str = NAMEID_FORMAT_PERSISTENT,
        attributes: dict[str, list[str]] | None = None,
        sign_assertion: bool = True,
        sign_response: bool = False,
        encrypt_for_cert: str | None = None,
        sha1: bool = False,
    ) -> str:
        from saml2.saml import NameID  # noqa: PLC0415

        kwargs: dict[str, Any] = {}
        if sha1:
            kwargs["sign_alg"] = SIG_RSA_SHA1
            kwargs["digest_alg"] = DIGEST_SHA1
        if encrypt_for_cert is not None:
            kwargs["encrypt_assertion"] = True
            kwargs["encrypt_assertion_self_contained"] = True
            kwargs["encrypt_cert_assertion"] = encrypt_for_cert
        response = self._server.create_authn_response(
            attributes or {},
            in_response_to,
            acs_url,
            sp_entity_id,
            name_id=NameID(text=name_id, format=name_id_format),
            sign_assertion=sign_assertion,
            sign_response=sign_response,
            authn={
                "class_ref": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
                "authn_auth": self.entity_id,
            },
            **kwargs,
        )
        return base64.b64encode(str(response).encode()).decode()

    def _redirect(
        self, msg: str, destination: str, *, response: bool, sigalg: str | None = None
    ) -> str:
        from saml2 import BINDING_HTTP_REDIRECT  # noqa: PLC0415

        info = self._server.apply_binding(
            BINDING_HTTP_REDIRECT,
            msg,
            destination,
            relay_state="",
            response=response,
            sign=True,
            sigalg=sigalg,
        )
        headers = dict(info["headers"])
        return str(headers["Location"])

    def logout_request_redirect(
        self,
        *,
        sp_slo_url: str,
        name_id: str,
        session_index: str,
        sigalg: str | None = None,
        issuer: str | None = None,
        issue_instant: str | None = None,
    ) -> str:
        """A signed redirect-binding LogoutRequest. ``issuer``/``issue_instant`` rewrite the
        message BEFORE signing (validly signed by this IdP's key, but lying)."""
        from saml2.saml import NameID  # noqa: PLC0415

        _req_id, req = self._server.create_logout_request(
            sp_slo_url,
            self.entity_id,
            name_id=NameID(text=name_id, format=NAMEID_FORMAT_PERSISTENT),
            session_indexes=[session_index],
            sign=False,
        )
        xml = str(req)
        if issuer is not None:
            xml, n = re.subn(r"(<(?:\w+:)?Issuer\b[^>]*>)[^<]*(</)", rf"\g<1>{issuer}\g<2>", xml)
            assert n == 1, xml
        if issue_instant is not None:
            xml, n = re.subn(r'IssueInstant="[^"]+"', f'IssueInstant="{issue_instant}"', xml)
            assert n == 1, xml
        return self._redirect(xml, sp_slo_url, response=False, sigalg=sigalg)

    def parse_logout_response(self, url: str) -> ParsedLogoutResponse:
        """Decode the SP's redirect-binding LogoutResponse; require a SHA-2 signed redirect."""
        q = parse_qs(urlparse(url).query)
        assert "Signature" in q and q["SigAlg"] == [SIG_RSA_SHA256], "response must be signed"
        xml = zlib.decompress(base64.b64decode(q["SAMLResponse"][0]), -15).decode()
        irt = re.search(r'InResponseTo="([^"]+)"', xml)
        status = re.search(r'<(?:\w+:)?StatusCode\b[^>]*Value="([^"]+)"', xml)
        dest = re.search(r'Destination="([^"]+)"', xml)
        assert irt is not None and status is not None, xml
        return ParsedLogoutResponse(
            in_response_to=irt.group(1),
            status=status.group(1),
            destination=dest.group(1) if dest else None,
            relay_state=q.get("RelayState", [None])[0],
        )

    def parse_logout_request(self, url: str) -> ParsedLogoutRequest:
        """Decode the SAMLRequest of an SP-initiated logout redirect; require a signed redirect."""
        q = parse_qs(urlparse(url).query)
        assert "Signature" in q and "SigAlg" in q, "logout redirect must be signed"
        xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15).decode()
        req_id = re.search(r'\bID="([^"]+)"', xml)
        name_id = re.search(r"<(?:\w+:)?NameID\b[^>]*>([^<]*)</", xml)
        index = re.search(r"<(?:\w+:)?SessionIndex\b[^>]*>([^<]*)</", xml)
        assert req_id is not None and name_id is not None, xml
        return ParsedLogoutRequest(
            request_id=req_id.group(1),
            name_id=name_id.group(1),
            session_index=index.group(1) if index else None,
        )

    def logout_response_redirect(
        self, *, sp_slo_url: str, in_response_to: str, success: bool = True
    ) -> str:
        from saml2 import samlp  # noqa: PLC0415

        status = None
        if not success:
            status = samlp.Status(
                status_code=samlp.StatusCode(value="urn:oasis:names:tc:SAML:2.0:status:Responder")
            )

        resp = self._server._status_response(
            samlp.LogoutResponse,
            self._server._issuer(),
            status,
            False,
            in_response_to=in_response_to,
            destination=sp_slo_url,
        )
        return self._redirect(str(resp), sp_slo_url, response=True)


@dataclass(frozen=True)
class ParsedLogoutResponse:
    in_response_to: str
    status: str
    destination: str | None
    relay_state: str | None


@dataclass(frozen=True)
class ParsedLogoutRequest:
    request_id: str
    name_id: str
    session_index: str | None


def saml_record(*, slug: str, idp: SamlTestIdp, settings: Settings, **cfg: Any) -> ProviderRecord:
    """A SAML ``ProviderRecord`` (not persisted) trusting *idp*, with a fresh SP key pair."""
    key_pem, cert_pem = generate_sp_keypair(slug)
    config = SamlProviderConfig(
        **{
            "idp_metadata_xml": idp.metadata_xml(),
            "sp_certificate": cert_pem,
            "sp_private_key": key_pem,
            **cfg,
        }
    )
    return ProviderRecord(
        id=1,
        slug=slug,
        kind="saml",
        source="db",
        name=f"Test {slug}",
        audience="agent",
        enabled=True,
        sort=100,
        button_label=None,
        button_icon=None,
        preset="generic_saml",
        config=config,
        secrets_set=frozenset({"sp_private_key"}),
        match_by="login",
        require_sso_opt_in=False,
        attribute_map=AttributeMap(),
        jit_enabled=False,
        jit_defaults=JitDefaults(),
        role_rules=(),
        sync_mode="managed",
        metadata_xml=None,
        metadata_fetched_at=None,
        metadata_error=None,
    )
