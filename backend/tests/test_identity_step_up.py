"""Step-up MFA evidence check (pure): what counts as an IdP-side second factor."""

from __future__ import annotations

from tiqora.identity.config_models import (
    AttributeMap,
    JitDefaults,
    MfaPolicy,
    OidcProviderConfig,
    SamlProviderConfig,
)
from tiqora.identity.step_up import (
    OIDC_ACR_ATTR,
    OIDC_AMR_ATTR,
    SAML_AUTHN_CONTEXT_ATTR,
    STEP_UP_ATTRS,
    check_mfa,
)
from tiqora.identity.store import ProviderRecord

MS = "http://schemas.microsoft.com/claims/"
MOBILE_2FA = "urn:oasis:names:tc:SAML:2.0:ac:classes:MobileTwoFactorContract"


def _record(
    *, kind: str, require_mfa_claim: bool, mfa_policy: MfaPolicy | None = None
) -> ProviderRecord:
    config = (
        OidcProviderConfig(issuer="https://idp.example.org", client_id="tiqora", client_secret="s")
        if kind == "oidc"
        else SamlProviderConfig()
    )
    return ProviderRecord(
        id=1,
        slug="idptest-stepup",
        kind=kind,  # type: ignore[arg-type]
        source="db",
        name="Step up",
        audience="agent",
        enabled=True,
        sort=100,
        button_label=None,
        button_icon=None,
        preset=None,
        config=config,
        secrets_set=frozenset(),
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
        require_mfa_claim=require_mfa_claim,
        mfa_policy=mfa_policy or MfaPolicy(),
    )


def test_step_up_attr_names() -> None:
    assert {"oidc:amr", "oidc:acr", "saml:authn_context"} == STEP_UP_ATTRS


def test_check_mfa_not_required() -> None:
    rec = _record(kind="oidc", require_mfa_claim=False)
    check = check_mfa(rec, {OIDC_AMR_ATTR: ("pwd",)})
    assert (check.required, check.satisfied) == (False, True)
    assert check.evidence == ("pwd",)
    bare = check_mfa(rec, {})
    assert (bare.required, bare.satisfied, bare.evidence) == (False, True, ())


def test_oidc_amr_and_acr() -> None:
    rec = _record(
        kind="oidc", require_mfa_claim=True, mfa_policy=MfaPolicy(oidc_acr_values=["gold"])
    )
    assert check_mfa(rec, {"oidc:amr": ("pwd", "mfa")}).satisfied
    assert check_mfa(rec, {"oidc:acr": ("gold",)}).satisfied
    assert not check_mfa(rec, {"oidc:amr": ("pwd",)}).satisfied
    assert not check_mfa(rec, {"amr": ("mfa",)}).satisfied  # plain claim name never counts
    assert not check_mfa(rec, {}).satisfied
    seen = check_mfa(rec, {"oidc:amr": ("pwd",), "oidc:acr": ("silver",)})
    assert seen.required and seen.evidence == ("pwd", "silver")


def test_saml_class_or_attribute() -> None:
    rec = _record(kind="saml", require_mfa_claim=True)
    assert check_mfa(rec, {SAML_AUTHN_CONTEXT_ATTR: (MOBILE_2FA,)}).satisfied
    by_attr = check_mfa(rec, {f"{MS}authnmethodsreferences": (f"{MS}multipleauthn",)})
    assert by_attr.satisfied and by_attr.evidence == (f"{MS}multipleauthn",)
    # attribute name matches case-insensitively (lookup), value does not
    assert check_mfa(rec, {f"{MS}AuthnMethodsReferences": (f"{MS}multipleauthn",)}).satisfied
    password = "urn:oasis:names:tc:SAML:2.0:ac:classes:Password"
    assert not check_mfa(rec, {SAML_AUTHN_CONTEXT_ATTR: (password,)}).satisfied
    assert not check_mfa(rec, {f"{MS}authnmethodsreferences": ("other",)}).satisfied
    assert not check_mfa(rec, {}).satisfied


def test_oidc_values_do_not_satisfy_a_saml_provider() -> None:
    rec = _record(kind="saml", require_mfa_claim=True)
    assert not check_mfa(rec, {OIDC_AMR_ATTR: ("mfa",), OIDC_ACR_ATTR: ("gold",)}).satisfied


def test_values_are_case_sensitive() -> None:
    oidc = _record(kind="oidc", require_mfa_claim=True)
    assert not check_mfa(oidc, {"oidc:amr": ("MFA",)}).satisfied
    assert check_mfa(oidc, {"oidc:amr": ("mfa",)}).satisfied
    saml = _record(kind="saml", require_mfa_claim=True)
    assert not check_mfa(saml, {SAML_AUTHN_CONTEXT_ATTR: (MOBILE_2FA.lower(),)}).satisfied
