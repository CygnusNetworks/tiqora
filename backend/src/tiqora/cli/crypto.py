"""``tiqora crypto ...`` CLI: PGP/S/MIME key import and roundtrip smoke tests."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory


def add_crypto_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("crypto", help="PGP/S/MIME key management")
    crypto_sub = p.add_subparsers(dest="crypto_command")

    pgp_import_p = crypto_sub.add_parser(
        "pgp-import", help="Import an ASCII-armored PGP key file into the Tiqora keyring"
    )
    pgp_import_p.add_argument("key_file", help="Path to an ASCII-armored .asc/.pgp key file")
    pgp_import_p.add_argument("--email", default=None, help="Audit-log email annotation")
    pgp_import_p.add_argument("--purpose", default="both", choices=["sign", "encrypt", "both"])
    pgp_import_p.set_defaults(func=_cmd_pgp_import)

    smime_register_p = crypto_sub.add_parser(
        "smime-register", help="Register an S/MIME cert/key pair for an email address"
    )
    smime_register_p.add_argument("email", help="Email address the cert/key pair belongs to")
    smime_register_p.add_argument("--cert-file", default=None, help="Path to a PEM certificate")
    smime_register_p.add_argument("--key-file", default=None, help="Path to a PEM private key")
    smime_register_p.add_argument("--purpose", default="both", choices=["sign", "encrypt", "both"])
    smime_register_p.set_defaults(func=_cmd_smime_register)


async def _cmd_pgp_import(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError, CryptoUnavailableError
    from tiqora.crypto.keystore import import_pgp_key
    from tiqora.crypto.pgp import PgpEngine

    settings = get_settings()
    key_data = await asyncio.to_thread(Path(args.key_file).read_text)
    factory = get_session_factory()
    try:
        engine = PgpEngine(settings.crypto_pgp_gnupghome)
        async with factory() as session:
            fingerprints = await import_pgp_key(
                session, engine, key_data, email=args.email, purpose=args.purpose
            )
    except (CryptoError, CryptoUnavailableError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"Imported {len(fingerprints)} PGP key(s): {', '.join(fingerprints)}")  # noqa: T201
    return 0


async def _cmd_smime_register(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import SmimeKeyStore, register_smime_key

    settings = get_settings()
    if not args.cert_file and not args.key_file:
        print("ERROR: pass at least one of --cert-file / --key-file", file=sys.stderr)  # noqa: T201
        return 2
    cert_pem = await asyncio.to_thread(Path(args.cert_file).read_bytes) if args.cert_file else None
    key_pem = await asyncio.to_thread(Path(args.key_file).read_bytes) if args.key_file else None
    store = SmimeKeyStore(settings.crypto_smime_cert_dir, settings.crypto_smime_private_dir)
    factory = get_session_factory()
    try:
        async with factory() as session:
            paths = await register_smime_key(
                session,
                store,
                args.email,
                cert_pem=cert_pem,
                key_pem=key_pem,
                purpose=args.purpose,
            )
    except CryptoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"Registered S/MIME key for {args.email}: cert={paths.cert_path} key={paths.key_path}")  # noqa: T201
    return 0
