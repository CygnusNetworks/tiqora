"""Pure tests for identity config models, presets and SP key helpers (wave 1, Task 3)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from tiqora.identity.config_models import (
    CONFIG_MODELS,
    HIDDEN_CONFIG_FIELDS,
    READ_ONLY_CONFIG_FIELDS,
    LdapProviderConfig,
    OidcProviderConfig,
    SamlProviderConfig,
    merge_config,
    secret_fields,
)
from tiqora.identity.presets import PRESETS, get_preset
from tiqora.identity.saml_keys import generate_sp_keypair, materialize_keypair
from tiqora.settings_sections.section import SECRET_MASK, SettingsSection, validate_section_schema


@pytest.mark.parametrize("model", [OidcProviderConfig, SamlProviderConfig, LdapProviderConfig])
def test_config_models_fit_schema_form(model: type) -> None:
    validate_section_schema(SettingsSection(key="x", model=model, title_key="t", i18n_prefix="p"))


def test_secret_fields_per_kind() -> None:
    assert secret_fields("oidc") == frozenset({"client_secret"})
    assert secret_fields("saml") == frozenset({"sp_private_key"})
    assert secret_fields("ldap") == frozenset({"bind_password"})
    assert HIDDEN_CONFIG_FIELDS["saml"] <= READ_ONLY_CONFIG_FIELDS["saml"]


def test_merge_config_secret_mask_keeps_value() -> None:
    cur = OidcProviderConfig(issuer="https://idp.example.org", client_secret=SecretStr("s1"))
    out = merge_config("oidc", cur, {"client_secret": SECRET_MASK, "scopes": "openid"})
    assert isinstance(out, OidcProviderConfig)
    assert out.client_secret is not None and out.client_secret.get_secret_value() == "s1"
    assert out.scopes == "openid"
    cleared = merge_config("oidc", cur, {"client_secret": ""})
    assert isinstance(cleared, OidcProviderConfig) and cleared.client_secret is None
    rotated = merge_config("oidc", cur, {"client_secret": "s2"})
    assert isinstance(rotated, OidcProviderConfig)
    assert rotated.client_secret is not None and rotated.client_secret.get_secret_value() == "s2"


def test_merge_config_ignores_read_only_sp_keypair() -> None:
    cur = SamlProviderConfig(sp_certificate="CERT", sp_private_key=SecretStr("KEY"))
    out = merge_config("saml", cur, {"sp_certificate": "EVIL", "sp_private_key": "EVILKEY"})
    assert isinstance(out, SamlProviderConfig)
    assert out.sp_certificate == "CERT"
    assert out.sp_private_key is not None and out.sp_private_key.get_secret_value() == "KEY"


def test_merge_config_validates_values() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        merge_config("saml", SamlProviderConfig(), {"clock_skew_seconds": 9999})


def test_every_preset_builds_its_config_model() -> None:
    assert {p.key for p in PRESETS} == {
        "entra_id",
        "google_workspace",
        "keycloak",
        "authentik",
        "adfs",
        "generic_saml",
        "generic_oidc",
        "ldap",
        "active_directory",
    }
    for p in PRESETS:
        CONFIG_MODELS[p.kind].model_validate(p.config)
        assert get_preset(p.key) is p


def test_preset_specifics() -> None:
    assert get_preset("entra_id").config["issuer"].startswith("https://login.microsoftonline.com/")
    assert get_preset("google_workspace").attribute_map.login == ["email"]
    ad = get_preset("active_directory")
    assert ad.config["uid_attr"] == "sAMAccountName"
    assert ad.config["access_attr"] == "member"
    assert ad.config["subject_attr"] == "objectGUID"
    with pytest.raises(KeyError):
        get_preset("nope")


def test_generate_sp_keypair_roundtrip(tmp_path: Path) -> None:
    key_pem, cert_pem = generate_sp_keypair("idp-test.example.org", days=30)
    assert "BEGIN PRIVATE KEY" in key_pem and "BEGIN CERTIFICATE" in cert_pem
    kf = materialize_keypair(key_pem, cert_pem)
    assert oct(os.stat(kf.key_path).st_mode & 0o777) == "0o600"
    assert stat.S_IMODE(os.stat(kf.cert_path).st_mode) == 0o600
    assert Path(kf.key_path).read_text() == key_pem
    assert materialize_keypair(key_pem, cert_pem) == kf  # reused


def test_generated_certificate_properties() -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa

    from tiqora.identity.types import utc_now

    key_pem, cert_pem = generate_sp_keypair("idp-test.example.org", days=30)
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    assert isinstance(cert.signature_hash_algorithm, hashes.SHA256)
    pub = cert.public_key()
    assert isinstance(pub, rsa.RSAPublicKey) and pub.key_size == 3072
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "idp-test.example.org"
    assert cert.not_valid_before_utc.replace(tzinfo=None) < utc_now()


def test_allowed_email_domains_are_normalised() -> None:
    from tiqora.identity.config_models import JitDefaults

    got = JitDefaults(allowed_email_domains=[" Example.Test. ", "", "A.test", "a.test"])
    assert got.allowed_email_domains == ["example.test", "a.test"]


@pytest.mark.parametrize(
    "bad", ["@example.test", "user@example.test", "exa mple.test", "a.test\tb"]
)
def test_allowed_email_domains_reject_malformed_entries(bad: str) -> None:
    from pydantic import ValidationError

    from tiqora.identity.config_models import JitDefaults

    with pytest.raises(ValidationError):
        JitDefaults(allowed_email_domains=[bad])
