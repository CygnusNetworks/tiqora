"""PGP / S/MIME settings editable in the Tiqora admin UI, layered over Znuny SysConfig.

Every field resolves by the same precedence (see
``docs/superpowers/specs/2026-10-01-crypto-settings-web-ui-design.md``):

1. ``TIQORA_CRYPTO_*`` environment variable, where one exists
2. Znuny SysConfig *set* — a valid system-wide ``sysconfig_modified`` row
3. Tiqora value from the admin UI (``tiqora_settings``, key ``crypto.<field>``)
4. Znuny default (``sysconfig_default``, valid rows only)
5. code default

Levels 1 and 2 lock the field in the UI. ``PGP::Key::Password`` merges per key
id instead: Znuny-set entries win for their ids, Tiqora entries fill in the
others, Znuny's shipped demo entries come last. Tiqora passphrases are stored
Fernet-encrypted (:mod:`tiqora.crypto.secret`) and never leave the backend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.models import TiqoraSettings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiqora.config import Settings
    from tiqora.znuny.sysconfig import SysConfig

Source = Literal["env", "znuny", "tiqora", "znuny_default", "default"]
Kind = Literal["bool", "str", "choice"]

KEY_PREFIX = "crypto."
PASSWORDS_KEY = "crypto.pgp.key_passwords"
LOCKED_SOURCES: frozenset[str] = frozenset({"env", "znuny"})

_TRUE = {"1", "true", "yes", "on"}

#: ``PGP::Options::DigestPreference`` values Znuny offers ("" = gpg's choice).
DIGESTS = ("", "md5", "sha1", "ripemd160", "sha224", "sha256", "sha384", "sha512")
PGP_METHODS = ("Detached", "Inline")


@dataclass(frozen=True)
class FieldSpec:
    name: str  # UI / API name, e.g. "pgp.enabled"
    kind: Kind
    znuny: str | None  # SysConfig setting
    env: str | None  # Settings attribute of the TIQORA_CRYPTO_* override
    env_var: str | None  # its variable name, for the UI hint
    default: Any
    choices: tuple[str, ...] = ()
    #: False: Znuny's *shipped default* is skipped (an explicitly set Znuny
    #: value still wins) — for settings whose Znuny default would change
    #: established Tiqora behaviour.
    use_znuny_default: bool = True

    @property
    def backend(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def storage_key(self) -> str:
        return KEY_PREFIX + self.name


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "pgp.enabled", "bool", "PGP", "crypto_pgp_enabled", "TIQORA_CRYPTO_PGP_ENABLED", False
    ),
    FieldSpec("pgp.gpg_bin", "str", "PGP::Bin", "crypto_gpg_bin", "TIQORA_CRYPTO_GPG_BIN", ""),
    FieldSpec(
        "pgp.homedir",
        "str",
        "PGP::Options",
        "crypto_pgp_gnupghome",
        "TIQORA_CRYPTO_PGP_GNUPGHOME",
        "",
    ),
    FieldSpec("pgp.options", "str", "PGP::Options", None, None, ""),
    FieldSpec(
        "pgp.digest", "choice", "PGP::Options::DigestPreference", None, None, "", choices=DIGESTS
    ),
    FieldSpec("pgp.method", "choice", "PGP::Method", None, None, "Detached", choices=PGP_METHODS),
    # Tiqora has always encrypted with always_trust; Znuny ships 0, which
    # would refuse every imported (uncertified) customer key.
    FieldSpec(
        "pgp.trusted_network",
        "bool",
        "PGP::TrustedNetwork",
        None,
        None,
        True,
        use_znuny_default=False,
    ),
    FieldSpec(
        "smime.enabled",
        "bool",
        "SMIME",
        "crypto_smime_enabled",
        "TIQORA_CRYPTO_SMIME_ENABLED",
        False,
    ),
    FieldSpec(
        "smime.openssl_bin",
        "str",
        "SMIME::Bin",
        "crypto_openssl_bin",
        "TIQORA_CRYPTO_OPENSSL_BIN",
        "",
    ),
    FieldSpec(
        "smime.cert_path",
        "str",
        "SMIME::CertPath",
        "crypto_smime_cert_dir",
        "TIQORA_CRYPTO_SMIME_CERT_DIR",
        "",
    ),
    FieldSpec(
        "smime.private_path",
        "str",
        "SMIME::PrivatePath",
        "crypto_smime_private_dir",
        "TIQORA_CRYPTO_SMIME_PRIVATE_DIR",
        "",
    ),
    FieldSpec(
        "smime.ca_path", "str", None, "crypto_smime_ca_path", "TIQORA_CRYPTO_SMIME_CA_PATH", ""
    ),
    FieldSpec(
        "smime.public_roots",
        "bool",
        None,
        "crypto_smime_public_roots",
        "TIQORA_CRYPTO_SMIME_PUBLIC_ROOTS",
        True,
    ),
    FieldSpec("smime.fetch_from_customer", "bool", "SMIME::FetchFromCustomer", None, None, False),
    FieldSpec("smime.no_verify", "bool", "SMIME::NoVerify", None, None, False),
)

FIELDS_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in FIELDS}


@dataclass(frozen=True)
class ResolvedField:
    spec: FieldSpec
    value: Any
    source: Source
    #: Raw Tiqora value (``None`` = not set in Tiqora), shown even when locked.
    tiqora_value: Any

    @property
    def locked(self) -> bool:
        return self.source in LOCKED_SOURCES


@dataclass(frozen=True)
class ResolvedPasswords:
    passwords: dict[str, str]
    #: key id → "znuny" | "tiqora" | "znuny_default" (only ids with a passphrase).
    sources: dict[str, str]


# ------------------------------------------------------------------ parsing


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in _TRUE


def parse_value(spec: FieldSpec, raw: Any) -> Any:
    """Normalise a stored / SysConfig value to the field's Python type."""
    if spec.kind == "bool":
        return _truthy(raw)
    return "" if raw is None else str(raw).strip()


def validate_value(spec: FieldSpec, value: Any) -> str:
    """Validate an admin-supplied value; return its storage string or raise ValueError."""
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{spec.name} must be true or false")
        return "1" if value else "0"
    if not isinstance(value, str):
        raise ValueError(f"{spec.name} must be a string")
    text: str = value.strip()
    if spec.kind == "choice" and text not in spec.choices:
        raise ValueError(
            f"{spec.name} must be one of: {', '.join(c or '(empty)' for c in spec.choices)}"
        )
    if "\n" in text or "\x00" in text:
        raise ValueError(f"{spec.name} must be a single line")
    return text


def _from_znuny(spec: FieldSpec, raw: Any) -> Any:
    """Map a decoded SysConfig value onto *spec* (``PGP::Options`` is split)."""
    if spec.znuny == "PGP::Options":
        from tiqora.crypto.config import parse_pgp_options

        homedir, rest = parse_pgp_options(str(raw or ""))
        return homedir if spec.name == "pgp.homedir" else " ".join(rest)
    return parse_value(spec, raw)


def _env_value(spec: FieldSpec, settings: Settings) -> Any | None:
    if spec.env is None:
        return None
    raw = getattr(settings, spec.env, None)
    if raw is None or (isinstance(raw, str) and raw == ""):
        return None
    return parse_value(spec, raw)


# ------------------------------------------------------------------ loading


async def load_tiqora_values(session: AsyncSession) -> dict[str, str]:
    """All ``crypto.*`` rows of ``tiqora_settings`` (storage key → raw value)."""
    rows = (
        await session.execute(
            select(TiqoraSettings).where(TiqoraSettings.key.like(KEY_PREFIX + "%"))
        )
    ).scalars()
    # "" is an explicit empty value (e.g. digest left to gpg); a cleared
    # field has no row at all.
    return {r.key: r.value for r in rows if r.value is not None}


async def _layers(sysconfig: SysConfig | None, name: str) -> tuple[Any, Any]:
    if sysconfig is None:
        return None, None
    try:
        return await sysconfig.get_layers(name)
    except Exception:  # noqa: BLE001 — SysConfig tables absent (fresh test DB)
        return None, None


async def resolve_fields(
    settings: Settings, sysconfig: SysConfig | None, tiqora: dict[str, str]
) -> dict[str, ResolvedField]:
    out: dict[str, ResolvedField] = {}
    for spec in FIELDS:
        stored = tiqora.get(spec.storage_key)
        tiqora_value = parse_value(spec, stored) if stored is not None else None
        modified, default = await _layers(sysconfig, spec.znuny) if spec.znuny else (None, None)
        env = _env_value(spec, settings)
        value: Any
        source: Source
        if env is not None:
            value, source = env, "env"
        elif modified is not None:
            value, source = _from_znuny(spec, modified), "znuny"
        elif tiqora_value is not None:
            value, source = tiqora_value, "tiqora"
        elif default is not None and spec.use_znuny_default:
            value, source = _from_znuny(spec, default), "znuny_default"
        else:
            value, source = spec.default, "default"
        out[spec.name] = ResolvedField(spec, value, source, tiqora_value)
    return out


def _as_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in value.items() if str(k).strip()}


def _stored_tokens(tiqora: dict[str, str]) -> dict[str, str]:
    raw = tiqora.get(PASSWORDS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


async def resolve_passwords(
    settings: Settings, sysconfig: SysConfig | None, tiqora: dict[str, str]
) -> ResolvedPasswords:
    modified, default = await _layers(sysconfig, "PGP::Key::Password")
    passwords: dict[str, str] = {}
    sources: dict[str, str] = {}
    for key_id, pw in _as_map(default).items():
        passwords[key_id], sources[key_id] = pw, "znuny_default"
    for key_id, token in _stored_tokens(tiqora).items():
        plain = decrypt_secret(settings.secret_key, token)
        if plain is not None:
            passwords[key_id], sources[key_id] = plain, "tiqora"
    for key_id, pw in _as_map(modified).items():
        passwords[key_id], sources[key_id] = pw, "znuny"
    return ResolvedPasswords(passwords, sources)


# ------------------------------------------------------------------ writing


async def _upsert(session: AsyncSession, key: str, value: str | None) -> None:
    row = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if value is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(TiqoraSettings(key=key, value=value))
    else:
        row.value = value


async def save_field(session: AsyncSession, name: str, value: Any) -> None:
    """Store (or with ``None`` clear) one Tiqora value. Raises KeyError / ValueError."""
    spec = FIELDS_BY_NAME[name]
    await _upsert(session, spec.storage_key, None if value is None else validate_value(spec, value))


async def set_passphrase(
    session: AsyncSession, settings: Settings, key_id: str, passphrase: str | None
) -> None:
    """Store (or with ``None`` remove) the Tiqora passphrase for *key_id*."""
    tokens = _stored_tokens(await load_tiqora_values(session))
    if passphrase is None:
        tokens.pop(key_id, None)
    else:
        tokens[key_id] = encrypt_secret(settings.secret_key, passphrase)
    await _upsert(session, PASSWORDS_KEY, json.dumps(tokens, sort_keys=True) if tokens else None)


__all__ = [
    "DIGESTS",
    "FIELDS",
    "FIELDS_BY_NAME",
    "PGP_METHODS",
    "FieldSpec",
    "ResolvedField",
    "ResolvedPasswords",
    "load_tiqora_values",
    "parse_value",
    "resolve_fields",
    "resolve_passwords",
    "save_field",
    "set_passphrase",
    "validate_value",
]
