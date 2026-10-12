"""SAML service provider: round trips and the malicious-response corpus (pure, in-process IdP)."""

from __future__ import annotations

import base64
import re
import urllib.parse
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as safe_et
import pytest

from tests._saml_idp import IDP_ENTITY_ID, IDP_SLO_URL, SamlTestIdp, require_saml, saml_record
from tiqora.config import Settings
from tiqora.identity.saml_sp import MAX_MESSAGE_CHARS, SamlResponseInvalid, SamlServiceProvider
from tiqora.identity.sso_sessions import SsoSessionInfo

pytestmark = [pytest.mark.saml]
require_saml()

SLUG = "idptest-saml"
SP_ENTITY = f"https://helpdesk.example.org/api/v1/auth/saml/{SLUG}/metadata"
AGENT_ACS = f"https://helpdesk.example.org/api/v1/auth/saml/{SLUG}/acs"
CUSTOMER_ACS = f"https://helpdesk.example.org/api/portal/auth/saml/{SLUG}/acs"
SP_SLO = f"https://helpdesk.example.org/api/v1/auth/saml/{SLUG}/sls"
OTHER_SP = "https://other.example.org/sp"
MAIL = "anna@example.org"
ATTRS = {"mail": [MAIL], "groups": ["Support"]}
S = Settings(
    environment="test",
    secret_key="unit-test-secret-key-for-identity-saml-sp",
    public_base_url="https://helpdesk.example.org",
)

REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
SIG_RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"


@pytest.fixture(scope="module")
def idp(tmp_path_factory: pytest.TempPathFactory) -> SamlTestIdp:
    return SamlTestIdp(tmp_path_factory.mktemp("idp"))


@pytest.fixture(scope="module")
def sp(idp: SamlTestIdp) -> SamlServiceProvider:
    provider = SamlServiceProvider(saml_record(slug=SLUG, idp=idp, settings=S), settings=S)
    idp.trust_sp(provider.sp_metadata_xml())
    return provider


def _good(idp: SamlTestIdp, **kw: object) -> tuple[str, str]:
    """A fresh valid response for the agent ACS: ``(request_id, base64)``."""
    req_id = "id-" + base64.b16encode(__import__("os").urandom(8)).decode()
    args: dict[str, object] = {
        "in_response_to": req_id,
        "acs_url": AGENT_ACS,
        "sp_entity_id": SP_ENTITY,
        "attributes": ATTRS,
    }
    args.update(kw)
    return req_id, idp.login_response(**args)  # type: ignore[arg-type]


def _xml(b64: str) -> str:
    return base64.b64decode(b64).decode()


def _b64(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


def _signed_assertion(xml: str) -> str:
    m = re.search(r"<(\w+):Assertion\b.*?</\1:Assertion>", xml, re.S)
    assert m is not None and "Signature" in m.group(0)
    return m.group(0)


def _evil_copy(assertion: str) -> str:
    evil = re.sub(r"<(\w+:)?Signature\b.*?</(\w+:)?Signature>", "", assertion, flags=re.S)
    evil = re.sub(r'\bID="[^"]+"', 'ID="evil-1"', evil, count=1)
    return evil.replace(">u-1001<", ">boss<")


async def _reject(
    sp: SamlServiceProvider,
    b64: str,
    req_id: str | None,
    *,
    acs: str = AGENT_ACS,
    outstanding: dict[str, str] | None = None,
) -> SamlResponseInvalid:
    if outstanding is None:
        outstanding = {req_id: acs} if req_id else {}
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.parse_login_response(b64, outstanding=outstanding, expected_acs=acs)
    return err.value


def _expect(err: SamlResponseInvalid, reason: str, detail: str) -> None:
    assert err.reason == reason
    assert detail in err.detail, err.detail


# --------------------------------------------------------------------------- positives


async def test_round_trip_agent(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    redir = await sp.login_redirect(relay_state="rs1", user_kind="agent")
    req_id, acs, relay = idp.parse_login_redirect(redir.url)
    assert redir.request_id == req_id and relay == "rs1"
    assert acs == "https://helpdesk.example.org/api/v1/auth/saml/idptest-saml/acs"
    b64 = idp.login_response(
        in_response_to=req_id, acs_url=acs, sp_entity_id=SP_ENTITY, attributes=ATTRS
    )
    res = await sp.parse_login_response(b64, outstanding={req_id: acs}, expected_acs=acs)
    assert res.name_id == "u-1001" and res.attributes["mail"] == (MAIL,)
    assert res.attributes["saml:name_id"] == ("u-1001",) and res.session_index
    assert res.in_response_to == req_id and res.destination == acs
    assert res.name_id_format == "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
    assert res.assertion_id and res.not_on_or_after is not None


async def test_login_redirect_customer_uses_customer_acs_and_is_signed(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    redir = await sp.login_redirect(relay_state="rs2", user_kind="customer")
    _, acs, _ = idp.parse_login_redirect(redir.url)
    assert acs == CUSTOMER_ACS
    query = urllib.parse.parse_qs(urllib.parse.urlparse(redir.url).query)
    assert redir.url.startswith("https://idp.example.org/saml/sso?")
    assert "Signature" in query and "SigAlg" in query


async def test_signed_response_and_assertion_accepted(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, sign_response=True)
    res = await sp.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.name_id == "u-1001"


async def test_encrypted_assertion_accepted(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    cert = sp.sp_certificate_pem
    req_id, b64 = _good(idp, encrypt_for_cert=cert)
    assert "EncryptedAssertion" in _xml(b64) and MAIL not in _xml(b64)
    res = await sp.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.attributes["mail"] == (MAIL,)


async def test_idp_initiated_accepted_only_when_enabled(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    _, b64 = _good(idp, in_response_to=None)
    assert (await _reject(sp, b64, None)).reason == "UnsolicitedResponse"

    enabled = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, allow_idp_initiated=True), settings=S
    )
    idp.trust_sp(enabled.sp_metadata_xml())
    res = await enabled.parse_login_response(b64, outstanding={}, expected_acs=AGENT_ACS)
    assert res.in_response_to is None and res.name_id == "u-1001"


async def test_in_response_to_still_required_known_when_idp_initiated_enabled(
    idp: SamlTestIdp,
) -> None:
    enabled = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, allow_idp_initiated=True), settings=S
    )
    idp.trust_sp(enabled.sp_metadata_xml())
    _, b64 = _good(idp)  # carries an InResponseTo nobody asked for
    err = await _reject(enabled, b64, None, outstanding={})
    assert err.reason == "unknown_request"


def test_sp_metadata_lists_both_acs_and_slo(sp: SamlServiceProvider) -> None:
    root = safe_et.fromstring(sp.sp_metadata_xml(), forbid_dtd=True)
    md = "{urn:oasis:names:tc:SAML:2.0:metadata}"
    acs = {e.get("Location") for e in root.iter(f"{md}AssertionConsumerService")}
    slo = {e.get("Location") for e in root.iter(f"{md}SingleLogoutService")}
    assert acs == {AGENT_ACS, CUSTOMER_ACS}
    assert slo == {SP_SLO}
    bindings = {e.get("Binding") for e in root.iter(f"{md}SingleLogoutService")}
    assert bindings == {REDIRECT}  # advertise only what handle_logout_request accepts
    assert root.get("entityID") == SP_ENTITY


# --------------------------------------------------------------------------- corpus


async def test_unsigned_assertion_rejected(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    req_id, b64 = _good(idp, sign_assertion=False)
    assert "Signature" not in _xml(b64)
    _expect(await _reject(sp, b64, req_id), "SignatureError", "Signature missing for assertion")


async def test_signed_response_unsigned_assertion_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, sign_response=True, sign_assertion=False)
    assert "Signature" in _xml(b64)
    _expect(await _reject(sp, b64, req_id), "SignatureError", "Signature missing for assertion")


async def test_tampered_attribute_rejected(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    assert MAIL in xml
    _expect(
        await _reject(sp, _b64(xml.replace(MAIL, "boss@example.org")), req_id),
        "SignatureError",
        "Failed to verify signature",
    )


async def test_xsw_evil_assertion_before_signed_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    signed = _signed_assertion(xml)
    evil = _evil_copy(signed)
    assert evil != signed and "Signature" not in evil
    attack = xml.replace(signed, evil + signed, 1)
    assert attack.count(":Assertion ") == 2
    assert (await _reject(sp, _b64(attack), req_id)).reason == "assertion_count"


async def test_xsw_signed_assertion_wrapped_in_extensions_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    signed = _signed_assertion(xml)
    prefix = re.search(r"<(\w+):Status\b", xml)
    assert prefix is not None
    p = prefix.group(1)
    attack = xml.replace(signed, _evil_copy(signed), 1).replace(
        f"<{p}:Status", f"<{p}:Extensions>{signed}</{p}:Extensions><{p}:Status", 1
    )
    assert attack.count(":Assertion ") == 2 and "Extensions" in attack
    assert (await _reject(sp, _b64(attack), req_id)).reason == "assertion_count"


async def test_doctype_rejected(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    xml = re.sub(r"^<\?xml[^>]*\?>\s*", "", xml)
    attack = '<!DOCTYPE r [<!ENTITY x "y">]>' + xml
    assert (await _reject(sp, _b64(attack), req_id)).reason == "dtd"


async def test_wrong_audience_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp, tmp_path: Path
) -> None:
    other = SamlServiceProvider(saml_record(slug="idptest-other", idp=idp, settings=S), settings=S)
    idp.trust_sp(other.sp_metadata_xml())
    other_entity = "https://helpdesk.example.org/api/v1/auth/saml/idptest-other/metadata"
    req_id, b64 = _good(idp, sp_entity_id=other_entity)
    assert other_entity in _xml(b64) and SP_ENTITY not in _xml(b64)
    _expect(await _reject(sp, b64, req_id), "audience_mismatch", "AudienceRestrictions")


async def test_expired_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    import saml2.time_util as tu

    req_id, b64 = _good(idp)
    real = tu.utc_now
    monkeypatch.setattr(tu, "utc_now", lambda: real() + 3600)
    _expect(await _reject(sp, b64, req_id), "ResponseLifetimeExceed", "too old")


async def test_unknown_in_response_to_rejected(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    _, b64 = _good(idp)
    _expect(
        await _reject(sp, b64, None, outstanding={}), "UnsolicitedResponse", "Unsolicited response"
    )


async def test_customer_acs_response_posted_to_agent_acs_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, acs_url=CUSTOMER_ACS)
    assert (await _reject(sp, b64, req_id, acs=AGENT_ACS)).reason == "destination_mismatch"


async def test_stripped_destination_still_bound_by_recipient(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, acs_url=CUSTOMER_ACS)
    xml = _xml(b64)
    stripped = re.sub(r'\sDestination="[^"]*"', "", xml, count=1)
    assert "Destination" not in stripped.split("Assertion")[0]
    assert (await _reject(sp, _b64(stripped), req_id, acs=AGENT_ACS)).reason == "recipient_mismatch"


async def test_wrong_recipient_with_matching_destination_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, acs_url=CUSTOMER_ACS)
    xml = _xml(b64)
    forged = xml.replace(f'Destination="{CUSTOMER_ACS}"', f'Destination="{AGENT_ACS}"', 1)
    assert forged != xml and f'Recipient="{CUSTOMER_ACS}"' in forged
    assert (await _reject(sp, _b64(forged), req_id, acs=AGENT_ACS)).reason == "recipient_mismatch"


async def test_signature_by_unknown_key_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp, tmp_path: Path
) -> None:
    rogue = SamlTestIdp(tmp_path)  # same entity id, different key
    assert rogue.entity_id == IDP_ENTITY_ID
    rogue.trust_sp(sp.sp_metadata_xml())
    req_id, b64 = _good(rogue)
    _expect(await _reject(sp, b64, req_id), "SignatureError", "Failed to verify signature")


async def test_more_than_one_assertion_rejected(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    signed = _signed_assertion(xml)
    attack = xml.replace(signed, signed + signed, 1)
    assert (await _reject(sp, _b64(attack), req_id)).reason == "assertion_count"


async def test_sha1_signed_response_rejected_by_default(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, sha1=True, sign_response=True)
    assert "rsa-sha1" in _xml(b64)
    assert (await _reject(sp, b64, req_id)).reason == "sha1_signature"


async def test_sha1_signed_response_accepted_with_opt_in(idp: SamlTestIdp) -> None:
    legacy = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, allow_sha1_signatures=True), settings=S
    )
    idp.trust_sp(legacy.sp_metadata_xml())
    req_id, b64 = _good(idp, sha1=True)
    assert "rsa-sha1" in _xml(b64)
    res = await legacy.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.name_id == "u-1001"


def test_sp_signs_with_sha256(sp: SamlServiceProvider) -> None:
    client = sp._ensure()
    assert client.signing_algorithm.endswith("rsa-sha256")
    assert client.digest_algorithm.endswith("#sha256")


async def test_root_in_response_to_stripped_rejected_when_idp_initiated_enabled(
    idp: SamlTestIdp,
) -> None:
    """The signed SubjectConfirmationData InResponseTo still names an unknown request."""
    enabled = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, allow_idp_initiated=True), settings=S
    )
    idp.trust_sp(enabled.sp_metadata_xml())
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    stripped = re.sub(r'\sInResponseTo="[^"]*"', "", xml, count=1)
    assert stripped.count("InResponseTo") == xml.count("InResponseTo") - 1 > 0
    err = await _reject(enabled, _b64(stripped), None, outstanding={})
    assert err.reason == "unknown_request"
    # With the request outstanding the signed value is what the result reports.
    res = await enabled.parse_login_response(
        _b64(stripped), outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.in_response_to == req_id


async def test_root_and_signed_in_response_to_must_agree(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp)
    xml = _xml(b64)
    forged = xml.replace(f'InResponseTo="{req_id}"', 'InResponseTo="id-other"', 1)
    assert forged != xml
    err = await _reject(
        sp, _b64(forged), None, outstanding={req_id: AGENT_ACS, "id-other": AGENT_ACS}
    )
    # pysaml2 refuses the pair first; our own mismatch check is the second line of defence.
    assert err.reason == "UnsolicitedResponse"


async def test_assertion_from_other_idp_in_aggregate_metadata_rejected(
    idp: SamlTestIdp, tmp_path: Path
) -> None:
    """IdP B's keys are in the aggregate metadata, but the provider is configured for IdP A."""
    other_entity = "https://other-idp.example.org/saml"
    idp_b = SamlTestIdp(tmp_path, entity_id=other_entity)
    entities = [
        re.search(r"<(\w+:)?EntityDescriptor\b.*</(\w+:)?EntityDescriptor>", x.metadata_xml(), re.S)
        for x in (idp, idp_b)
    ]
    assert all(entities)
    aggregate = (
        '<md:EntitiesDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata">'
        + "".join(m.group(0) for m in entities if m)
        + "</md:EntitiesDescriptor>"
    )
    provider = SamlServiceProvider(
        saml_record(
            slug=SLUG,
            idp=idp,
            settings=S,
            idp_metadata_xml=aggregate,
            idp_entity_id=IDP_ENTITY_ID,
        ),
        settings=S,
    )
    idp.trust_sp(provider.sp_metadata_xml())
    idp_b.trust_sp(provider.sp_metadata_xml())
    good_id, good = _good(idp)  # A signs: accepted
    res = await provider.parse_login_response(
        good, outstanding={good_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.name_id == "u-1001"
    req_id, forged = _good(idp_b)  # B signs with a key the metadata does contain
    err = await _reject(provider, forged, req_id)
    assert err.reason == "issuer_mismatch"


async def test_oversized_response_rejected(sp: SamlServiceProvider) -> None:
    big = base64.b64encode(b"<a>" + b"x" * (2 * 1024 * 1024 + 1) + b"</a>").decode()
    assert (await _reject(sp, big, None, outstanding={})).reason == "too_large"


async def test_garbage_rejected(sp: SamlServiceProvider) -> None:
    assert (await _reject(sp, "!!!not base64!!!", None, outstanding={})).reason in {
        "base64",
        "xml",
    }


# --------------------------------------------------------------------------- logout


async def test_logout_redirect_none_without_idp_slo(idp: SamlTestIdp) -> None:
    no_slo = re.sub(r"<(\w+:)?SingleLogoutService\b[^>]*/>", "", idp.metadata_xml(), flags=re.S)
    assert "SingleLogoutService" not in no_slo
    provider = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, idp_metadata_xml=no_slo), settings=S
    )
    info = SsoSessionInfo(
        provider_id=1,
        slug=SLUG,
        kind="saml",
        subject_hash="h",
        name_id="u-1001",
        session_index="s1",
    )
    assert await provider.logout_redirect(info, relay_state="rs") is None


async def test_logout_redirect_is_signed_and_targets_idp(sp: SamlServiceProvider) -> None:
    info = SsoSessionInfo(
        provider_id=1,
        slug=SLUG,
        kind="saml",
        subject_hash="h",
        name_id="u-1001",
        name_id_format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        session_index="s1",
    )
    out = await sp.logout_redirect(info, relay_state="rs9")
    assert out is not None and out.url.startswith(IDP_SLO_URL + "?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(out.url).query)
    assert query["RelayState"] == ["rs9"] and "Signature" in query


async def test_handle_logout_request_verified_and_answered(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
    for raw in (urllib.parse.urlparse(url).query, None):  # raw octets, then decoded fallback
        data, reply = await sp.handle_logout_request(
            q["SAMLRequest"],
            binding=REDIRECT,
            relay_state=q.get("RelayState"),
            sigalg=q.get("SigAlg"),
            signature=q.get("Signature"),
            raw_query=raw,
        )
        assert data.name_id == "u-1001" and data.session_indexes == ("s1",)
        assert reply.startswith(IDP_SLO_URL + "?") and "SAMLResponse=" in reply
        assert "SigAlg=http%3A%2F%2Fwww.w3.org%2F2001%2F04%2Fxmldsig-more%23rsa-sha256" in reply


async def test_logout_signature_verified_over_raw_octets(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    """Re-encoding the query differently (lowercase hex) must not be accepted as "the same"."""
    url = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    query = urllib.parse.urlparse(url).query
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    lowered = re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), query.split("&Signature=")[0])
    assert lowered != query.split("&Signature=")[0]
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.handle_logout_request(
            q["SAMLRequest"],
            binding=REDIRECT,
            relay_state=None,
            sigalg=q["SigAlg"],
            signature=q["Signature"],
            raw_query=lowered + "&Signature=" + query.split("&Signature=")[1],
        )
    assert err.value.reason == "logout_signature"


async def test_logout_request_signed_with_sha1_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_request_redirect(
        sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1", sigalg=SIG_RSA_SHA1
    )
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
    assert q["SigAlg"] == SIG_RSA_SHA1
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.handle_logout_request(
            q["SAMLRequest"],
            binding=REDIRECT,
            relay_state=None,
            sigalg=q["SigAlg"],
            signature=q["Signature"],
            raw_query=urllib.parse.urlparse(url).query,
        )
    assert err.value.reason == "logout_sigalg"


@pytest.mark.parametrize("mode", ["unsigned", "bad_signature"])
async def test_handle_logout_request_rejects_unsigned_or_forged(
    sp: SamlServiceProvider, idp: SamlTestIdp, mode: str
) -> None:
    url = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
    sig = None if mode == "unsigned" else base64.b64encode(b"x" * 256).decode()
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.handle_logout_request(
            q["SAMLRequest"],
            binding=REDIRECT,
            relay_state=q.get("RelayState"),
            sigalg=None if mode == "unsigned" else q.get("SigAlg"),
            signature=sig,
        )
    assert err.value.reason == ("logout_unsigned" if mode == "unsigned" else "logout_signature")


async def test_parse_logout_response_returns_in_response_to(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_response_redirect(sp_slo_url=SP_SLO, in_response_to="id-logout-1")
    got = await sp.parse_logout_response(**_response_call(url))
    assert got == "id-logout-1"


async def test_parse_logout_response_requires_success_status(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_response_redirect(
        sp_slo_url=SP_SLO, in_response_to="id-logout-2", success=False
    )
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.parse_logout_response(**_response_call(url))
    assert err.value.reason == "StatusError"  # pysaml2 refuses a non-Success status itself


def _response_call(url: str, **over: object) -> dict[str, Any]:
    raw = urllib.parse.urlparse(url).query
    q = {k: v[-1] for k, v in urllib.parse.parse_qs(raw).items()}
    args: dict[str, Any] = {
        "saml_response": q["SAMLResponse"],
        "binding": REDIRECT,
        "relay_state": q.get("RelayState"),
        "sigalg": q.get("SigAlg"),
        "signature": q.get("Signature"),
        "raw_query": raw,
    }
    args.update(over)
    return args


async def _response_reject(sp: SamlServiceProvider, args: dict[str, Any]) -> str:
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.parse_logout_response(**args)
    return err.value.reason


async def test_logout_response_must_be_signed(sp: SamlServiceProvider, idp: SamlTestIdp) -> None:
    url = idp.logout_response_redirect(sp_slo_url=SP_SLO, in_response_to="id-logout-3")
    unsigned = _response_call(url, sigalg=None, signature=None, raw_query=None)
    assert await _response_reject(sp, unsigned) == "logout_unsigned"
    forged = _response_call(url, signature=base64.b64encode(b"x" * 256).decode(), raw_query=None)
    assert await _response_reject(sp, forged) == "logout_signature"
    sha1 = _response_call(url, sigalg="http://www.w3.org/2000/09/xmldsig#rsa-sha1")
    assert await _response_reject(sp, sha1) == "logout_sigalg"


async def test_logout_response_signed_by_foreign_key_rejected(
    sp: SamlServiceProvider, tmp_path: Path
) -> None:
    impostor = SamlTestIdp(tmp_path)  # same entity id, key not in the metadata
    url = impostor.logout_response_redirect(sp_slo_url=SP_SLO, in_response_to="id-logout-4")
    assert await _response_reject(sp, _response_call(url)) == "logout_signature"


async def test_logout_response_post_binding_refused(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_response_redirect(sp_slo_url=SP_SLO, in_response_to="id-logout-5")
    post = _response_call(url, binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    assert await _response_reject(sp, post) == "logout_binding"


async def test_handle_logout_request_returns_request_id(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    url = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    args = _logout_call(url)
    data, _reply = await sp.handle_logout_request(str(args.pop("saml_request")), **args)  # type: ignore[arg-type]
    xml = zlib.decompress(
        base64.b64decode(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["SAMLRequest"][0]),
        -15,
    ).decode()
    assert f'ID="{data.request_id}"' in xml and data.request_id


@pytest.mark.parametrize("age", [timedelta(hours=1), timedelta(minutes=-30)])
async def test_stale_or_future_logout_request_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp, age: timedelta
) -> None:
    when = (datetime.now(UTC) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = idp.logout_request_redirect(
        sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1", issue_instant=when
    )
    assert await _logout_reject(sp, _logout_call(url)) == "issue_instant"


async def test_logout_request_with_foreign_issuer_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    # Signed with the trusted key, but claims to come from another entity.
    url = idp.logout_request_redirect(
        sp_slo_url=SP_SLO,
        name_id="u-1001",
        session_index="s1",
        issuer="https://other.example.org/saml",
    )
    assert await _logout_reject(sp, _logout_call(url)) == "issuer_mismatch"


# --------------------------------------------------------------------------- round 2


def _logout_call(url: str, *, query: str | None = None, **over: object) -> dict[str, object]:
    """Arguments as a route would pass them; parsed values follow Starlette (last wins)."""
    raw = urllib.parse.urlparse(url).query if query is None else query
    q = {k: v[-1] for k, v in urllib.parse.parse_qs(raw).items()}
    args: dict[str, object] = {
        "saml_request": q["SAMLRequest"],
        "binding": REDIRECT,
        "relay_state": q.get("RelayState"),
        "sigalg": q.get("SigAlg"),
        "signature": q.get("Signature"),
        "raw_query": raw,
    }
    args.update(over)
    return args


async def _logout_reject(sp: SamlServiceProvider, args: dict[str, object]) -> str:
    saml_request = str(args.pop("saml_request"))
    with pytest.raises(SamlResponseInvalid) as err:
        await sp.handle_logout_request(saml_request, **args)  # type: ignore[arg-type]
    return err.value.reason


async def test_logout_appended_duplicate_samlrequest_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    signed = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="attacker", session_index="s1")
    victim = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="victim", session_index="s1")
    victim_req = urllib.parse.parse_qs(urllib.parse.urlparse(victim).query)["SAMLRequest"][0]
    query = urllib.parse.urlparse(signed).query + "&SAMLRequest=" + urllib.parse.quote(victim_req)
    args = _logout_call(signed, query=query)
    assert args["saml_request"] == victim_req  # what Starlette would hand to the parser
    assert await _logout_reject(sp, args) == "logout_signature"


async def test_logout_percent_encoded_duplicate_key_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    signed = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="attacker", session_index="s1")
    victim = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="victim", session_index="s1")
    victim_req = urllib.parse.parse_qs(urllib.parse.urlparse(victim).query)["SAMLRequest"][0]
    query = urllib.parse.urlparse(signed).query + "&SAML%52equest=" + urllib.parse.quote(victim_req)
    args = _logout_call(signed, query=query)
    assert args["saml_request"] == victim_req
    assert await _logout_reject(sp, args) == "logout_signature"


async def test_logout_relay_state_must_match_raw_query(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    signed = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    base = urllib.parse.urlparse(signed).query
    # Appended RelayState: not part of what was signed.
    appended = _logout_call(signed, query=base + "&RelayState=evil")
    assert appended["relay_state"] == "evil"
    assert await _logout_reject(sp, appended) == "logout_signature"
    # Parsed RelayState that the raw query does not carry.
    invented = _logout_call(signed, relay_state="evil")
    assert await _logout_reject(sp, invented) == "logout_signature"


async def test_logout_request_for_other_destination_rejected(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    other = "https://helpdesk.example.org/api/v1/auth/saml/other/sls"
    url = idp.logout_request_redirect(sp_slo_url=other, name_id="u-1001", session_index="s1")
    reason = await _logout_reject(sp, _logout_call(url))
    assert reason in {"destination_mismatch", "OtherError"}


async def test_sha1_inside_encrypted_assertion_rejected_unless_opted_in(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, sha1=True, encrypt_for_cert=sp.sp_certificate_pem)
    xml = _xml(b64)
    assert "EncryptedAssertion" in xml and "rsa-sha1" not in xml  # hidden until decrypted
    assert (await _reject(sp, b64, req_id)).reason == "sha1_signature"

    legacy = SamlServiceProvider(
        saml_record(slug=SLUG, idp=idp, settings=S, allow_sha1_signatures=True), settings=S
    )
    idp.trust_sp(legacy.sp_metadata_xml())
    req_id, b64 = _good(idp, sha1=True, encrypt_for_cert=legacy.sp_certificate_pem)
    res = await legacy.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.name_id == "u-1001"


# --------------------------------------------------------------------------- T21 fix round 1


def _signed_logout_xml(idp: SamlTestIdp, mutate: Any) -> str:
    """A LogoutRequest whose XML is rewritten by *mutate* and THEN validly signed by the IdP."""
    from saml2.saml import NameID  # noqa: PLC0415

    _rid, req = idp._server.create_logout_request(
        SP_SLO,
        idp.entity_id,
        name_id=NameID(
            text="u-1001", format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
        ),
        session_indexes=["s1"],
        sign=False,
    )
    return idp._redirect(mutate(str(req)), SP_SLO, response=False)


@pytest.fixture
def parse_spy(sp: SamlServiceProvider, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    client = sp._ensure()

    def spy(name: str) -> Any:
        real = getattr(client, name)

        def wrapped(*a: Any, **k: Any) -> Any:
            calls.append(name)
            return real(*a, **k)

        return wrapped

    for name in ("parse_logout_request", "parse_logout_request_response"):
        monkeypatch.setattr(client, name, spy(name))
    return calls


async def test_logout_request_signature_checked_before_parsing(
    sp: SamlServiceProvider, idp: SamlTestIdp, tmp_path: Path, parse_spy: list[str]
) -> None:
    url = idp.logout_request_redirect(sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1")
    forged = _logout_call(url, signature=base64.b64encode(b"x" * 256).decode(), raw_query=None)
    assert await _logout_reject(sp, forged) == "logout_signature"
    foreign = SamlTestIdp(tmp_path).logout_request_redirect(
        sp_slo_url=SP_SLO, name_id="u-1001", session_index="s1"
    )
    assert await _logout_reject(sp, _logout_call(foreign)) == "logout_signature"
    unsigned = _logout_call(url, sigalg=None, signature=None)
    assert await _logout_reject(sp, unsigned) == "logout_unsigned"
    oversized = _logout_call(url, saml_request="A" * (MAX_MESSAGE_CHARS + 1), raw_query=None)
    assert await _logout_reject(sp, oversized) == "too_large"
    assert parse_spy == []  # nothing unverified reached pysaml2

    args = _logout_call(url)
    await sp.handle_logout_request(str(args.pop("saml_request")), **args)  # type: ignore[arg-type]
    assert parse_spy == ["parse_logout_request"]


async def test_signed_inflate_bomb_and_dtd_refused_before_pysaml2(
    sp: SamlServiceProvider, idp: SamlTestIdp, parse_spy: list[str]
) -> None:
    bomb = _signed_logout_xml(idp, lambda xml: xml + "<!--" + "A" * (3 * 1024 * 1024) + "-->")
    args = _logout_call(bomb)
    assert len(str(args["saml_request"])) < 64 * 1024  # small on the wire
    assert await _logout_reject(sp, args) == "too_large"

    def with_dtd(xml: str) -> str:
        decl, sep, rest = xml.partition("?>")
        doctype = '<!DOCTYPE x [<!ENTITY e "boom">]>'
        return f"{decl}{sep}{doctype}{rest}" if sep else doctype + xml

    assert await _logout_reject(sp, _logout_call(_signed_logout_xml(idp, with_dtd))) == "dtd"
    assert parse_spy == []


async def test_logout_response_signature_checked_before_parsing(
    sp: SamlServiceProvider, idp: SamlTestIdp, parse_spy: list[str]
) -> None:
    url = idp.logout_response_redirect(sp_slo_url=SP_SLO, in_response_to="id-logout-9")
    forged = _response_call(url, signature=base64.b64encode(b"x" * 256).decode(), raw_query=None)
    assert await _response_reject(sp, forged) == "logout_signature"
    assert parse_spy == []
    assert await sp.parse_logout_response(**_response_call(url)) == "id-logout-9"
    assert parse_spy == ["parse_logout_request_response"]


def test_saml_replay_ttl_never_truncates() -> None:
    """The replay key must live at least until NotOnOrAfter + skew (rounded up)."""
    from datetime import UTC, datetime, timedelta

    from tiqora.identity.sso_routes import _assertion_ttl

    not_after = datetime.now(UTC) + timedelta(seconds=300.4)
    assert _assertion_ttl(not_after, 120) >= 300.4 + 120
    assert _assertion_ttl(None, 120) == 86400 + 120


# --------------------------------------------------------------------------- MFA evidence

MOBILE_2FA = "urn:oasis:names:tc:SAML:2.0:ac:classes:MobileTwoFactorContract"
PASSWORD_CLASS = "urn:oasis:names:tc:SAML:2.0:ac:classes:Password"


async def test_saml_result_has_context_and_session_not_after(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp, class_ref=MOBILE_2FA, session_not_on_or_after="2099-01-01T08:00:00Z")
    res = await sp.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.authn_context_class_refs == (MOBILE_2FA,)
    assert res.session_not_on_or_after == datetime(2099, 1, 1, 8, 0, tzinfo=UTC)


async def test_saml_result_defaults_without_session_end(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(idp)
    res = await sp.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert res.authn_context_class_refs == (PASSWORD_CLASS,)
    assert res.session_not_on_or_after is None


async def test_saml_assertion_attribute_cannot_pose_as_proof(
    sp: SamlServiceProvider, idp: SamlTestIdp
) -> None:
    req_id, b64 = _good(
        idp, attributes={**ATTRS, "saml:authn_context": [MOBILE_2FA], "oidc:amr": ["mfa"]}
    )
    res = await sp.parse_login_response(
        b64, outstanding={req_id: AGENT_ACS}, expected_acs=AGENT_ACS
    )
    assert "saml:authn_context" not in res.attributes and "oidc:amr" not in res.attributes
    assert res.attributes["mail"] == (MAIL,)


def _authn_request_xml(url: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15).decode()


async def test_saml_request_authn_context(idp: SamlTestIdp) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from tiqora.identity.config_models import MfaPolicy  # noqa: PLC0415

    base = saml_record(slug=SLUG, idp=idp, settings=S)
    plain = await SamlServiceProvider(base, settings=S).login_redirect(
        relay_state="rs", user_kind="agent"
    )
    assert "RequestedAuthnContext" not in _authn_request_xml(plain.url)

    rec = replace(base, mfa_policy=MfaPolicy(saml_request_authn_context=True))
    redirect = await SamlServiceProvider(rec, settings=S).login_redirect(
        relay_state="rs", user_kind="agent"
    )
    xml = _authn_request_xml(redirect.url)
    assert re.search(r'<(\w+:)?RequestedAuthnContext\b[^>]*Comparison="minimum"', xml), xml
    assert MOBILE_2FA in xml


async def test_idp_initiated_without_context_is_not_mfa(idp: SamlTestIdp) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from tiqora.identity.config_models import MfaPolicy  # noqa: PLC0415
    from tiqora.identity.step_up import SAML_AUTHN_CONTEXT_ATTR, check_mfa  # noqa: PLC0415

    base = saml_record(slug=SLUG, idp=idp, settings=S, allow_idp_initiated=True)
    provider = SamlServiceProvider(base, settings=S)
    idp.trust_sp(provider.sp_metadata_xml())
    _, b64 = _good(idp, in_response_to=None, class_ref=PASSWORD_CLASS)
    res = await provider.parse_login_response(b64, outstanding={}, expected_acs=AGENT_ACS)
    assert res.in_response_to is None
    attrs = dict(res.attributes)
    if res.authn_context_class_refs:
        attrs[SAML_AUTHN_CONTEXT_ATTR] = res.authn_context_class_refs
    rec = replace(base, require_mfa_claim=True, mfa_policy=MfaPolicy())
    check = check_mfa(rec, attrs)
    assert check.required and not check.satisfied
    assert check.evidence == (PASSWORD_CLASS,)
