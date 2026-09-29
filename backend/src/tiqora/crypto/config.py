"""Resolved PGP / S/MIME configuration: Znuny SysConfig + ``TIQORA_CRYPTO_*`` overrides.

Tiqora shares its key stores with a (possibly still running) Znuny, so the
authoritative settings are Znuny's own SysConfig entries (``Framework.xml``):

* ``PGP`` (on/off) — ``TIQORA_CRYPTO_PGP_ENABLED``
* ``PGP::Bin`` (``/usr/bin/gpg``) — ``TIQORA_CRYPTO_GPG_BIN``
* ``PGP::Options`` (``--homedir <dir> --batch …``, extra gpg options such as a
  trust model) — ``TIQORA_CRYPTO_PGP_GNUPGHOME`` overrides the homedir only
* ``PGP::Key::Password`` (key id → passphrase) — no override
* ``PGP::Options::DigestPreference`` (signature digest) — no override
* ``SMIME`` (on/off) — ``TIQORA_CRYPTO_SMIME_ENABLED``
* ``SMIME::Bin`` (``/usr/bin/openssl``) — ``TIQORA_CRYPTO_OPENSSL_BIN``
* ``SMIME::CertPath`` (``<hash>.<n>`` certificates) — ``TIQORA_CRYPTO_SMIME_CERT_DIR``
* ``SMIME::PrivatePath`` (``<hash>.<n>`` keys + ``.P`` secrets) —
  ``TIQORA_CRYPTO_SMIME_PRIVATE_DIR``

``SMIME::CacheTTL`` is deliberately ignored (Tiqora reads the files directly).

Env vars win when set: inside a container the Znuny host paths usually do not
exist, so the compose file mounts the key directories somewhere and points the
env vars there. A configured binary path that does not exist on this host
(Znuny's ``/usr/bin/gpg`` on a box where gpg lives elsewhere) falls back to a
``PATH`` lookup instead of failing.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiqora.config import Settings
    from tiqora.znuny.sysconfig import SysConfig

_TRUE = {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in _TRUE


@dataclass(frozen=True)
class PgpConfig:
    enabled: bool = False
    gpg_bin: str = "gpg"
    homedir: str = ""
    #: ``PGP::Options`` without ``--homedir`` (python-gnupg adds the batch flags).
    options: tuple[str, ...] = ()
    #: ``PGP::Key::Password`` — key id (short/long/fingerprint) → passphrase.
    passwords: dict[str, str] = field(default_factory=dict)
    digest: str = ""


@dataclass(frozen=True)
class SmimeConfig:
    enabled: bool = False
    openssl_bin: str = "openssl"
    cert_path: str = ""
    private_path: str = ""
    #: Extra CA bundle (-CAfile) for chain validation, on top of CertPath (-CApath).
    ca_path: str = ""
    #: ``SMIME::FetchFromCustomer`` — import certificates from the customer
    #: backend attribute ``UserSMIMECertificate`` (see ``customer_fetch``).
    fetch_from_customer: bool = False


@dataclass(frozen=True)
class CryptoConfig:
    pgp: PgpConfig
    smime: SmimeConfig

    @classmethod
    def from_settings(cls, settings: Settings) -> CryptoConfig:
        """Env-only view (no DB) — used where no SysConfig session is at hand."""
        return cls(
            pgp=PgpConfig(
                enabled=bool(settings.crypto_pgp_enabled),
                gpg_bin=_resolve_bin(settings.crypto_gpg_bin, "", "gpg"),
                homedir=settings.crypto_pgp_gnupghome,
            ),
            smime=SmimeConfig(
                enabled=bool(settings.crypto_smime_enabled),
                openssl_bin=_resolve_bin(settings.crypto_openssl_bin, "", "openssl"),
                cert_path=settings.crypto_smime_cert_dir,
                private_path=settings.crypto_smime_private_dir,
                ca_path=settings.crypto_smime_ca_path,
            ),
        )


def _resolve_bin(env_value: str, sysconfig_value: str, default: str) -> str:
    """Env override > SysConfig path (if it exists here) > ``PATH`` lookup of *default*."""
    if env_value:
        return env_value
    if sysconfig_value and os.path.isfile(sysconfig_value):
        return sysconfig_value
    return default


def parse_pgp_options(raw: str) -> tuple[str, tuple[str, ...]]:
    """Split ``PGP::Options`` into ``(homedir, remaining_options)``.

    ``--homedir /x``, ``--homedir=/x`` are extracted; everything else (e.g.
    ``--trust-model always``, ``--always-trust``) is passed through to gpg.
    ``--batch``/``--no-tty`` are dropped because python-gnupg always sets them
    and duplicates are harmless but noisy.
    """
    try:
        tokens = shlex.split(raw or "")
    except ValueError:
        tokens = (raw or "").split()
    homedir = ""
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--homedir" and i + 1 < len(tokens):
            homedir = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--homedir="):
            homedir = tok.split("=", 1)[1]
        elif tok not in ("--batch", "--no-tty"):
            rest.append(tok)
        i += 1
    return homedir, tuple(rest)


def _as_password_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in value.items() if str(k).strip()}


async def resolve_crypto_config(settings: Settings, sysconfig: SysConfig | None) -> CryptoConfig:
    """Merge Znuny SysConfig with the ``TIQORA_CRYPTO_*`` env overrides."""
    if sysconfig is None:
        return CryptoConfig.from_settings(settings)

    async def _get(name: str) -> Any:
        try:
            return await sysconfig.get(name)
        except Exception:  # noqa: BLE001 — SysConfig tables absent (fresh test DB)
            return None

    pgp_sc = await _get("PGP")
    pgp_bin = await _get("PGP::Bin")
    pgp_options = await _get("PGP::Options")
    pgp_passwords = await _get("PGP::Key::Password")
    pgp_digest = await _get("PGP::Options::DigestPreference")
    smime_sc = await _get("SMIME")
    smime_bin = await _get("SMIME::Bin")
    cert_path = await _get("SMIME::CertPath")
    private_path = await _get("SMIME::PrivatePath")
    fetch_from_customer = await _get("SMIME::FetchFromCustomer")

    sc_homedir, options = parse_pgp_options(str(pgp_options or ""))
    pgp_enabled = (
        settings.crypto_pgp_enabled if settings.crypto_pgp_enabled is not None else _truthy(pgp_sc)
    )
    smime_enabled = (
        settings.crypto_smime_enabled
        if settings.crypto_smime_enabled is not None
        else _truthy(smime_sc)
    )
    return CryptoConfig(
        pgp=PgpConfig(
            enabled=bool(pgp_enabled),
            gpg_bin=_resolve_bin(settings.crypto_gpg_bin, str(pgp_bin or ""), "gpg"),
            homedir=settings.crypto_pgp_gnupghome or sc_homedir,
            options=options,
            passwords=_as_password_map(pgp_passwords),
            digest=str(pgp_digest or ""),
        ),
        smime=SmimeConfig(
            enabled=bool(smime_enabled),
            openssl_bin=_resolve_bin(settings.crypto_openssl_bin, str(smime_bin or ""), "openssl"),
            cert_path=settings.crypto_smime_cert_dir or str(cert_path or ""),
            private_path=settings.crypto_smime_private_dir or str(private_path or ""),
            ca_path=settings.crypto_smime_ca_path,
            fetch_from_customer=_truthy(fetch_from_customer),
        ),
    )


async def setting_is_valid(session: AsyncSession, name: str) -> bool:
    """Whether the SysConfig setting *name* is active (``is_valid``).

    Unlike :meth:`SysConfig.get` a system-wide ``sysconfig_modified`` row with
    ``is_valid = 0`` counts: that is how Znuny's admin deactivates a module
    registration such as ``PostMaster::PreFilterModule###000-SMIMEFetchFromCustomer``.
    A setting missing from both tables counts as valid (Znuny's shipped default).
    """
    from sqlalchemy import text

    try:
        row = (
            await session.execute(
                text(
                    "SELECT is_valid FROM sysconfig_modified WHERE name = :n AND user_id IS NULL"
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"n": name},
            )
        ).first()
        if row is None:
            row = (
                await session.execute(
                    text("SELECT is_valid FROM sysconfig_default WHERE name = :n LIMIT 1"),
                    {"n": name},
                )
            ).first()
    except Exception:  # noqa: BLE001 — SysConfig tables absent (fresh test DB)
        return True
    return row is None or bool(row[0])


@dataclass(frozen=True)
class BinaryStatus:
    available: bool
    path: str
    version: str
    reason: str = ""


def probe_binary(binary: str, version_args: list[str]) -> BinaryStatus:
    """Check that *binary* runs; return its first version line."""
    resolved = shutil.which(binary) or (binary if os.path.isfile(binary) else "")
    if not resolved:
        return BinaryStatus(False, binary, "", f"{binary!r} not found")
    try:
        proc = subprocess.run(  # noqa: S603 — fixed arg list, binary from config
            [resolved, *version_args],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BinaryStatus(False, resolved, "", str(exc))
    first = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if proc.returncode != 0:
        return BinaryStatus(False, resolved, "", proc.stderr.decode("utf-8", "replace")[:200])
    return BinaryStatus(True, resolved, first[0] if first else "")


@dataclass(frozen=True)
class BackendStatus:
    """Self-check of one backend, shown in admin System-Info and the key pages."""

    backend: str  # "pgp" | "smime"
    enabled: bool
    available: bool  # binary runs, library present, key dirs configured
    binary: BinaryStatus
    paths: dict[str, str]
    problems: list[str]


def _dir_problem(label: str, path: str) -> str | None:
    if not path:
        return f"{label} is not configured"
    p = Path(path)
    if p.exists() and not p.is_dir():
        return f"{label} {path} is not a directory"
    if p.exists() and not os.access(p, os.W_OK):
        return f"{label} {path} is not writable"
    return None


def backend_status_sync(config: CryptoConfig) -> list[BackendStatus]:
    """Probe gpg/openssl + key directories for both backends (blocking)."""
    out: list[BackendStatus] = []

    pgp_problems: list[str] = []
    gpg = probe_binary(config.pgp.gpg_bin, ["--version"])
    if not gpg.available:
        pgp_problems.append(f"gpg: {gpg.reason}")
    try:
        import gnupg  # noqa: F401
    except ImportError:
        pgp_problems.append("python-gnupg is not installed (backend extra 'crypto')")
    home_problem = _dir_problem("PGP keyring (--homedir)", config.pgp.homedir)
    if home_problem:
        pgp_problems.append(home_problem)
    out.append(
        BackendStatus(
            backend="pgp",
            enabled=config.pgp.enabled,
            available=not pgp_problems,
            binary=gpg,
            paths={"homedir": config.pgp.homedir},
            problems=pgp_problems,
        )
    )

    smime_problems: list[str] = []
    openssl = probe_binary(config.smime.openssl_bin, ["version"])
    if not openssl.available:
        smime_problems.append(f"openssl: {openssl.reason}")
    for label, path in (
        ("SMIME::CertPath", config.smime.cert_path),
        ("SMIME::PrivatePath", config.smime.private_path),
    ):
        problem = _dir_problem(label, path)
        if problem:
            smime_problems.append(problem)
    out.append(
        BackendStatus(
            backend="smime",
            enabled=config.smime.enabled,
            available=not smime_problems,
            binary=openssl,
            paths={
                "cert_path": config.smime.cert_path,
                "private_path": config.smime.private_path,
            },
            problems=smime_problems,
        )
    )
    return out


async def load_crypto_config(
    session: AsyncSession, settings: Settings | None = None
) -> CryptoConfig:
    """Resolve the crypto config for a request / CLI run with an async DB session."""
    from tiqora.config import get_settings
    from tiqora.znuny.sysconfig import SysConfig

    return await resolve_crypto_config(settings or get_settings(), SysConfig(session))


__all__ = [
    "BackendStatus",
    "BinaryStatus",
    "CryptoConfig",
    "backend_status_sync",
    "load_crypto_config",
    "PgpConfig",
    "SmimeConfig",
    "parse_pgp_options",
    "probe_binary",
    "resolve_crypto_config",
]
