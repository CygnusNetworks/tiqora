"""Layered PGP / S/MIME settings (tiqora/crypto/settings_store.py).

Precedence per field: env > Znuny set (sysconfig_modified) > Tiqora admin value >
Znuny default > code default; ``PGP::Key::Password`` merges per key id.
SysConfig layers are injected, so these tests need no database.
"""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.config import Settings
from tiqora.crypto import settings_store as store
from tiqora.crypto.config import resolve_crypto_config
from tiqora.crypto.secret import encrypt_secret


class _LayeredSysConfig:
    """Stand-in for SysConfig.get_layers with separate set / default values."""

    session = None

    def __init__(self, modified: dict[str, Any], default: dict[str, Any]) -> None:
        self._m, self._d = modified, default

    async def get_layers(self, name: str) -> tuple[Any, Any]:
        return self._m.get(name), self._d.get(name)


_DEFAULTS = {
    "PGP": "0",
    "PGP::Options": "--homedir /opt/otrs/.gnupg/ --batch --no-tty --yes",
    "PGP::Options::DigestPreference": "sha256",
    "PGP::Method": "Detached",
    "PGP::TrustedNetwork": "0",
    "PGP::Key::Password": {"488A0B8F": "SomePassword"},
    "SMIME": "0",
    "SMIME::NoVerify": "0",
}


def _tq(**values: str) -> dict[str, str]:
    return {"crypto." + k.replace("__", "."): v for k, v in values.items()}


async def test_znuny_default_applies_without_anything_set() -> None:
    fields = await store.resolve_fields(Settings(), _LayeredSysConfig({}, _DEFAULTS), {})
    assert fields["pgp.homedir"].value == "/opt/otrs/.gnupg/"
    assert fields["pgp.homedir"].source == "znuny_default"
    assert fields["pgp.options"].value == "--yes"
    assert fields["pgp.enabled"].value is False
    assert fields["smime.cert_path"].source == "default"
    assert not fields["pgp.enabled"].locked


async def test_tiqora_value_beats_znuny_default() -> None:
    tq = _tq(pgp__enabled="1", pgp__homedir="/var/lib/tiqora/gnupg", pgp__method="Inline")
    fields = await store.resolve_fields(Settings(), _LayeredSysConfig({}, _DEFAULTS), tq)
    assert fields["pgp.enabled"].value is True and fields["pgp.enabled"].source == "tiqora"
    assert fields["pgp.homedir"].value == "/var/lib/tiqora/gnupg"
    assert fields["pgp.method"].value == "Inline"
    assert fields["pgp.options"].source == "znuny_default"  # untouched field


async def test_znuny_set_value_beats_tiqora_and_locks() -> None:
    sc = _LayeredSysConfig({"PGP": "0", "PGP::Options": "--homedir /znuny/gpg"}, _DEFAULTS)
    tq = _tq(pgp__enabled="1", pgp__homedir="/tiqora/gpg", pgp__options="--yes")
    fields = await store.resolve_fields(Settings(), sc, tq)
    assert fields["pgp.enabled"].value is False and fields["pgp.enabled"].source == "znuny"
    assert fields["pgp.enabled"].locked
    assert fields["pgp.enabled"].tiqora_value is True  # kept, shown, used again later
    # PGP::Options set in Znuny locks both derived fields.
    assert fields["pgp.homedir"].value == "/znuny/gpg"
    assert fields["pgp.options"].value == "" and fields["pgp.options"].locked


async def test_empty_tiqora_value_is_explicit() -> None:
    fields = await store.resolve_fields(
        Settings(), _LayeredSysConfig({}, _DEFAULTS), _tq(pgp__digest="")
    )
    assert fields["pgp.digest"].value == "" and fields["pgp.digest"].source == "tiqora"


async def test_env_beats_everything() -> None:
    sc = _LayeredSysConfig({"PGP::Options": "--homedir /znuny/gpg"}, _DEFAULTS)
    settings = Settings(TIQORA_CRYPTO_PGP_GNUPGHOME="/env/gpg", TIQORA_CRYPTO_PGP_ENABLED="0")
    fields = await store.resolve_fields(settings, sc, _tq(pgp__enabled="1"))
    assert fields["pgp.homedir"].value == "/env/gpg" and fields["pgp.homedir"].source == "env"
    assert fields["pgp.enabled"].value is False and fields["pgp.enabled"].source == "env"


async def test_trusted_network_ignores_znuny_default_only() -> None:
    fields = await store.resolve_fields(Settings(), _LayeredSysConfig({}, _DEFAULTS), {})
    assert fields["pgp.trusted_network"].value is True
    assert fields["pgp.trusted_network"].source == "default"
    sc = _LayeredSysConfig({"PGP::TrustedNetwork": "0"}, _DEFAULTS)
    fields = await store.resolve_fields(Settings(), sc, {})
    assert fields["pgp.trusted_network"].value is False


async def test_passwords_merge_per_key_id() -> None:
    settings = Settings()
    tokens = {
        "5DF2D6B2": encrypt_secret(settings.secret_key, "from-tiqora"),
        "D2DF79FA": encrypt_secret(settings.secret_key, "loses-to-znuny"),
    }
    import json

    sc = _LayeredSysConfig({"PGP::Key::Password": {"D2DF79FA": "from-znuny"}}, _DEFAULTS)
    pw = await store.resolve_passwords(settings, sc, {store.PASSWORDS_KEY: json.dumps(tokens)})
    assert pw.passwords == {
        "488A0B8F": "SomePassword",
        "5DF2D6B2": "from-tiqora",
        "D2DF79FA": "from-znuny",
    }
    assert pw.sources == {"488A0B8F": "znuny_default", "5DF2D6B2": "tiqora", "D2DF79FA": "znuny"}


async def test_undecryptable_token_is_skipped() -> None:
    import json

    pw = await store.resolve_passwords(
        Settings(), None, {store.PASSWORDS_KEY: json.dumps({"ABCD1234": "garbage"})}
    )
    assert pw.passwords == {}


async def test_resolve_crypto_config_uses_layers() -> None:
    sc = _LayeredSysConfig({"SMIME::NoVerify": "1"}, _DEFAULTS)
    tq = _tq(
        smime__enabled="1",
        smime__cert_path="/c",
        smime__ca_path="/ca.pem",
        pgp__trusted_network="0",
        pgp__options="--trust-model always --batch",
    )
    cfg = await resolve_crypto_config(Settings(), sc, tq)  # type: ignore[arg-type]
    assert cfg.smime.enabled is True and cfg.smime.cert_path == "/c"
    assert cfg.smime.ca_path == "/ca.pem"
    assert cfg.smime.no_verify is True
    assert cfg.pgp.trusted_network is False
    assert cfg.pgp.options == ("--trust-model", "always")
    assert cfg.pgp.method == "Detached"
    assert cfg.smime.public_roots is True


async def test_public_roots_default_on_and_env_can_turn_it_off() -> None:
    sc = _LayeredSysConfig({}, _DEFAULTS)
    off = Settings(crypto_smime_public_roots=False)
    cfg = await resolve_crypto_config(off, sc, _tq(smime__public_roots="1"))  # type: ignore[arg-type]
    assert cfg.smime.public_roots is False
    cfg = await resolve_crypto_config(Settings(), sc, _tq(smime__public_roots="0"))  # type: ignore[arg-type]
    assert cfg.smime.public_roots is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("pgp.enabled", "yes"),
        ("pgp.method", "Sideways"),
        ("pgp.digest", "sha999"),
        ("pgp.homedir", 5),
        ("smime.cert_path", "/a\n/b"),
    ],
)
def test_invalid_values_are_rejected(name: str, value: Any) -> None:
    with pytest.raises(ValueError):
        store.validate_value(store.FIELDS_BY_NAME[name], value)


def test_valid_values_serialise() -> None:
    assert store.validate_value(store.FIELDS_BY_NAME["pgp.enabled"], True) == "1"
    assert store.validate_value(store.FIELDS_BY_NAME["pgp.digest"], "") == ""
    assert store.validate_value(store.FIELDS_BY_NAME["pgp.homedir"], " /x ") == "/x"
