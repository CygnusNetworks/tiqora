"""Resolved PGP / S/MIME configuration: env, Znuny SysConfig and Tiqora admin settings.

Tiqora shares its key stores with a (possibly still running) Znuny and reads
Znuny's own SysConfig entries (``Framework.xml``); every one that has an effect
in Tiqora can also be set in the admin UI. Per field the precedence is
``TIQORA_CRYPTO_*`` env > Znuny value *set* in ``sysconfig_modified`` > Tiqora
admin value > Znuny default > code default — see
:mod:`tiqora.crypto.settings_store` for the field list:

* ``PGP``, ``PGP::Bin``, ``PGP::Options`` (``--homedir`` + extra gpg options),
  ``PGP::Options::DigestPreference``, ``PGP::Method``, ``PGP::TrustedNetwork``,
  ``PGP::Key::Password`` (merged per key id)
* ``SMIME``, ``SMIME::Bin``, ``SMIME::CertPath``, ``SMIME::PrivatePath``,
  ``SMIME::FetchFromCustomer``, ``SMIME::NoVerify``; the CA bundle for chain
  validation is Tiqora-only (``TIQORA_CRYPTO_SMIME_CA_PATH`` or admin UI)

``SMIME::CacheTTL`` and ``PGP::Log`` are deliberately ignored (no effect here).

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
    #: ``PGP::Method`` — ``Detached`` or ``Inline`` (notifications without rich text).
    method: str = "Detached"
    #: ``PGP::TrustedNetwork`` — encrypt/decrypt with ``always_trust``.
    trusted_network: bool = True


@dataclass(frozen=True)
class SmimeConfig:
    enabled: bool = False
    openssl_bin: str = "openssl"
    cert_path: str = ""
    private_path: str = ""
    #: Extra CA bundle (-CAfile) for chain validation, on top of CertPath (-CApath).
    ca_path: str = ""
    #: Also trust the bundled Mozilla e-mail roots (``smime.PUBLIC_ROOTS_FILE``).
    public_roots: bool = True
    #: ``SMIME::FetchFromCustomer`` — import certificates from the customer
    #: backend attribute ``UserSMIMECertificate`` (see ``customer_fetch``).
    fetch_from_customer: bool = False
    #: ``SMIME::NoVerify`` — a valid signature counts as verified without a
    #: trusted certificate chain.
    no_verify: bool = False


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
                public_roots=settings.crypto_smime_public_roots is not False,
            ),
        )


def _resolve_bin(env_value: str, configured: str, default: str) -> str:
    """Env override > configured binary (if it exists here) > ``PATH`` lookup of *default*.

    *configured* may be a path (Znuny's ``/usr/bin/gpg``) or a bare program
    name set in the admin UI (``gpg2``), which is looked up on ``PATH``.
    """
    if env_value:
        return env_value
    if configured and (os.path.isfile(configured) or shutil.which(configured)):
        return configured
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


async def resolve_crypto_config(
    settings: Settings,
    sysconfig: SysConfig | None,
    tiqora: dict[str, str] | None = None,
) -> CryptoConfig:
    """Merge env overrides, Znuny SysConfig and the Tiqora admin settings.

    Precedence per field: env > Znuny set > Tiqora > Znuny default > code
    default (:mod:`tiqora.crypto.settings_store`). *tiqora* (the
    ``crypto.*`` rows of ``tiqora_settings``) is loaded through the
    SysConfig's session when not given.
    """
    from tiqora.crypto import settings_store as store

    if sysconfig is None:
        return CryptoConfig.from_settings(settings)
    if tiqora is None:
        tiqora = {}
        if sysconfig.session is not None:
            try:
                tiqora = await store.load_tiqora_values(sysconfig.session)
            except Exception:  # noqa: BLE001 — tiqora_settings absent (fresh test DB)
                tiqora = {}

    fields = await store.resolve_fields(settings, sysconfig, tiqora)
    pw = await store.resolve_passwords(settings, sysconfig, tiqora)

    def v(name: str) -> Any:
        return fields[name].value

    def binary(name: str, default: str) -> str:
        f = fields[name]
        if f.source == "env":
            return str(f.value)
        return _resolve_bin("", str(f.value or ""), default)

    try:
        options = tuple(shlex.split(str(v("pgp.options"))))
    except ValueError:
        options = tuple(str(v("pgp.options")).split())
    return CryptoConfig(
        pgp=PgpConfig(
            enabled=bool(v("pgp.enabled")),
            gpg_bin=binary("pgp.gpg_bin", "gpg"),
            homedir=str(v("pgp.homedir")),
            options=tuple(o for o in options if o not in ("--batch", "--no-tty")),
            passwords=pw.passwords,
            digest=str(v("pgp.digest")),
            method=str(v("pgp.method")) or "Detached",
            trusted_network=bool(v("pgp.trusted_network")),
        ),
        smime=SmimeConfig(
            enabled=bool(v("smime.enabled")),
            openssl_bin=binary("smime.openssl_bin", "openssl"),
            cert_path=str(v("smime.cert_path")),
            private_path=str(v("smime.private_path")),
            ca_path=str(v("smime.ca_path")),
            public_roots=bool(v("smime.public_roots")),
            fetch_from_customer=bool(v("smime.fetch_from_customer")),
            no_verify=bool(v("smime.no_verify")),
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
    if not p.exists():
        # Created on first use (PgpEngine) — only if the nearest existing
        # ancestor lets us, e.g. not /opt/otrs/.gnupg in an unprivileged container.
        parent = p.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not os.access(parent, os.W_OK | os.X_OK):
            return f"{label} {path} does not exist and cannot be created"
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
