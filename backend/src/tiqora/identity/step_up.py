"""Second-factor proof from the IdP (step-up MFA, pure part).

Adapters (OIDC callback, SAML ACS) inject three pseudo attributes into the identity's
attribute map; :func:`check_mfa` decides whether they satisfy the provider's policy.
The plain ``amr`` / ``acr`` claim names never count: only the injected pseudo attributes do,
and the OIDC adapter fills them from the verified id_token alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from tiqora.identity.mapping import lookup

if TYPE_CHECKING:
    from tiqora.identity.store import ProviderRecord

OIDC_AMR_ATTR: Final = "oidc:amr"
OIDC_ACR_ATTR: Final = "oidc:acr"
SAML_AUTHN_CONTEXT_ATTR: Final = "saml:authn_context"
STEP_UP_ATTRS: Final = frozenset({OIDC_AMR_ATTR, OIDC_ACR_ATTR, SAML_AUTHN_CONTEXT_ATTR})


@dataclass(frozen=True, slots=True)
class MfaCheck:
    required: bool
    satisfied: bool
    evidence: tuple[str, ...]  # the values seen (amr/acr/class refs/attribute values)


def _exact(seen: tuple[str, ...], accepted: list[str]) -> bool:
    """Exact, case-sensitive match of any seen value against the accepted list."""
    return any(v in accepted for v in seen)


def check_mfa(provider: ProviderRecord, attributes: Mapping[str, tuple[str, ...]]) -> MfaCheck:
    """Does the IdP's proof in *attributes* satisfy *provider*'s second-factor policy?"""
    policy = provider.mfa_policy
    satisfied = False
    evidence: tuple[str, ...] = ()
    if provider.kind == "oidc":
        amr = tuple(attributes.get(OIDC_AMR_ATTR, ()))
        acr = tuple(attributes.get(OIDC_ACR_ATTR, ()))
        evidence = amr + acr
        satisfied = _exact(amr, policy.oidc_amr_values) or _exact(acr, policy.oidc_acr_values)
    elif provider.kind == "saml":
        classes = tuple(attributes.get(SAML_AUTHN_CONTEXT_ATTR, ()))
        values = lookup(attributes, policy.saml_mfa_attribute) if policy.saml_mfa_attribute else ()
        evidence = classes + values
        satisfied = _exact(classes, policy.saml_authn_context_classes) or _exact(
            values, policy.saml_mfa_attribute_values
        )
    if not provider.require_mfa_claim:
        return MfaCheck(required=False, satisfied=True, evidence=evidence)
    return MfaCheck(required=True, satisfied=satisfied, evidence=evidence)
