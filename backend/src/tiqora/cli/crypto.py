"""``tiqora crypto ...`` CLI: PGP / S/MIME key management and store maintenance.

Keys land in the same stores the admin UI and a parallel Znuny use (see
:mod:`tiqora.crypto.config`); every mutation writes a ``tiqora_crypto_key``
audit row (user id 1 — the CLI runs as the installation admin).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory

_CLI_USER_ID = 1


def add_crypto_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("crypto", help="PGP/S/MIME key management")
    crypto_sub = p.add_subparsers(dest="crypto_command")

    s = crypto_sub.add_parser("status", help="Show backend configuration and self-check")
    s.set_defaults(func=_cmd_status)

    s = crypto_sub.add_parser("pgp-import", help="Import an ASCII-armored PGP key file")
    s.add_argument("key_file", help="Path to an ASCII-armored .asc/.pgp key file")
    s.add_argument("--email", default=None, help="Audit-log email annotation")
    s.add_argument("--purpose", default="both", choices=["sign", "encrypt", "both"])
    s.set_defaults(func=_cmd_pgp_import)

    s = crypto_sub.add_parser("pgp-list", help="List PGP keys in the keyring")
    s.set_defaults(func=_cmd_pgp_list)

    s = crypto_sub.add_parser("pgp-delete", help="Delete a PGP key (or only its secret part)")
    s.add_argument("key_id", help="Key id or fingerprint")
    s.add_argument("--secret-only", action="store_true", help="Keep the public key")
    s.set_defaults(func=_cmd_pgp_delete)

    s = crypto_sub.add_parser("smime-list", help="List S/MIME certificates")
    s.set_defaults(func=_cmd_smime_list)

    s = crypto_sub.add_parser("smime-add-cert", help="Add an S/MIME certificate (PEM or DER)")
    s.add_argument("cert_file")
    s.set_defaults(func=_cmd_smime_add_cert)

    s = crypto_sub.add_parser(
        "smime-add-key", help="Add the private key for an already stored certificate"
    )
    s.add_argument("key_file")
    s.add_argument(
        "--secret",
        default="",
        help="Passphrase of the key; omit for an unencrypted key (a secret is generated)",
    )
    s.set_defaults(func=_cmd_smime_add_key)

    s = crypto_sub.add_parser(
        "smime-rehash",
        help="Rename certificates to their current openssl subject hash and re-sync smime_keys",
    )
    s.set_defaults(func=_cmd_smime_rehash)

    s = crypto_sub.add_parser(
        "migrate-flat-store",
        help="One-off: move pre-B1 <email>.crt/<email>.key files into the Znuny layout",
    )
    s.add_argument(
        "--from-cert-dir",
        default=None,
        help="Directory with <email>.crt files (default: the configured S/MIME cert dir)",
    )
    s.add_argument(
        "--from-private-dir",
        default=None,
        help="Directory with <email>.key files (default: the configured private dir)",
    )
    s.add_argument("--secret", default="", help="Passphrase of encrypted flat keys")
    s.add_argument("--keep", action="store_true", help="Do not delete the flat files")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=_cmd_migrate_flat_store)


def _err(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)  # noqa: T201
    return 1


async def _config() -> object:
    from tiqora.crypto.config import load_crypto_config

    factory = get_session_factory()
    async with factory() as session:
        return await load_crypto_config(session, get_settings())


async def _cmd_status(args: argparse.Namespace) -> int:
    from tiqora.crypto.config import CryptoConfig, backend_status_sync

    cfg = await _config()
    assert isinstance(cfg, CryptoConfig)
    for st in await asyncio.to_thread(backend_status_sync, cfg):
        state = "enabled" if st.enabled else "disabled"
        usable = "usable" if st.available else "NOT usable"
        print(f"{st.backend}: {state}, {usable} — {st.binary.path} {st.binary.version}")  # noqa: T201
        for k, v in st.paths.items():
            print(f"  {k}: {v or '-'}")  # noqa: T201
        for problem in st.problems:
            print(f"  problem: {problem}")  # noqa: T201
    return 0


async def _pgp_engine() -> object:
    from tiqora.crypto.config import CryptoConfig
    from tiqora.crypto.pgp import PgpEngine

    cfg = await _config()
    assert isinstance(cfg, CryptoConfig)
    return PgpEngine.from_config(cfg.pgp)


async def _smime_store() -> object:
    from tiqora.crypto.config import CryptoConfig
    from tiqora.crypto.smime_store import SmimeStore

    cfg = await _config()
    assert isinstance(cfg, CryptoConfig)
    return SmimeStore.from_config(cfg.smime)


async def _cmd_pgp_import(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import import_pgp_key
    from tiqora.crypto.pgp import PgpEngine

    key_data = await asyncio.to_thread(Path(args.key_file).read_text)
    try:
        engine = await _pgp_engine()
        assert isinstance(engine, PgpEngine)
        async with get_session_factory()() as session:
            fingerprints = await import_pgp_key(
                session,
                engine,
                key_data,
                email=args.email,
                purpose=args.purpose,
                user_id=_CLI_USER_ID,
            )
    except CryptoError as exc:
        return _err(str(exc))
    print(f"Imported {len(fingerprints)} PGP key(s): {', '.join(fingerprints)}")  # noqa: T201
    return 0


async def _cmd_pgp_list(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.pgp import PgpEngine

    try:
        engine = await _pgp_engine()
        assert isinstance(engine, PgpEngine)
        keys = await asyncio.to_thread(engine.list_keys)
    except CryptoError as exc:
        return _err(str(exc))
    for k in keys:
        kind = "sec" if k.has_secret else "pub"
        expires = k.expires.date().isoformat() if k.expires else "never"
        uids = ", ".join(k.uids)
        print(f"{kind} {k.znuny_key_id} [{k.status}] {k.fingerprint} {expires} {uids}")  # noqa: T201
    return 0


async def _cmd_pgp_delete(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import delete_pgp_key
    from tiqora.crypto.pgp import PgpEngine

    try:
        engine = await _pgp_engine()
        assert isinstance(engine, PgpEngine)
        async with get_session_factory()() as session:
            key = await delete_pgp_key(
                session, engine, args.key_id, secret_only=args.secret_only, user_id=_CLI_USER_ID
            )
    except CryptoError as exc:
        return _err(str(exc))
    print(f"Deleted {'secret key of ' if args.secret_only else ''}{key.fingerprint}")  # noqa: T201
    return 0


async def _cmd_smime_list(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import smime_status
    from tiqora.crypto.smime_store import SmimeStore

    try:
        store = await _smime_store()
        assert isinstance(store, SmimeStore)
        entries = await asyncio.to_thread(store.list_entries)
    except CryptoError as exc:
        return _err(str(exc))
    for e in entries:
        if e.info is None:
            print(f"{e.filename} INVALID")  # noqa: T201
            continue
        key = " +key" if e.has_private else ""
        print(  # noqa: T201
            f"{e.filename} [{smime_status(e)}]{key} {e.info.subject} "
            f"until {e.info.not_after.date().isoformat()} {e.info.email_joined}"
        )
    return 0


async def _cmd_smime_add_cert(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import add_smime_certificate
    from tiqora.crypto.smime_store import SmimeStore

    data = await asyncio.to_thread(Path(args.cert_file).read_bytes)
    try:
        store = await _smime_store()
        assert isinstance(store, SmimeStore)
        async with get_session_factory()() as session:
            entry = await add_smime_certificate(session, store, data, user_id=_CLI_USER_ID)
    except CryptoError as exc:
        return _err(str(exc))
    print(f"Certificate stored as {entry.filename}")  # noqa: T201
    return 0


async def _cmd_smime_add_key(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import add_smime_private_key
    from tiqora.crypto.smime_store import SmimeStore

    data = await asyncio.to_thread(Path(args.key_file).read_bytes)
    try:
        store = await _smime_store()
        assert isinstance(store, SmimeStore)
        async with get_session_factory()() as session:
            entry, generated = await add_smime_private_key(
                session, store, data, args.secret, user_id=_CLI_USER_ID
            )
    except CryptoError as exc:
        return _err(str(exc))
    extra = " (key encrypted with a generated secret)" if generated else ""
    print(f"Private key stored as {entry.filename}{extra}")  # noqa: T201
    return 0


async def _cmd_smime_rehash(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import rehash_smime_store
    from tiqora.crypto.smime_store import SmimeStore

    try:
        store = await _smime_store()
        assert isinstance(store, SmimeStore)
        async with get_session_factory()() as session:
            renames, changes = await rehash_smime_store(session, store, user_id=_CLI_USER_ID)
    except CryptoError as exc:
        return _err(str(exc))
    for old, new in renames.items():
        print(f"renamed {old} -> {new}")  # noqa: T201
    print(f"{len(renames)} file(s) renamed, {changes} smime_keys row(s) changed")  # noqa: T201
    return 0


async def _cmd_migrate_flat_store(args: argparse.Namespace) -> int:
    from tiqora.crypto import CryptoError
    from tiqora.crypto.keystore import migrate_flat_store
    from tiqora.crypto.smime_store import SmimeStore

    settings = get_settings()
    try:
        store = await _smime_store()
        assert isinstance(store, SmimeStore)
        async with get_session_factory()() as session:
            report = await migrate_flat_store(
                session,
                store,
                args.from_cert_dir or str(store.cert_dir),
                args.from_private_dir
                or (str(store.private_dir) if store.private_dir else "")
                or settings.crypto_smime_private_dir,
                secret=args.secret,
                keep=args.keep,
                dry_run=args.dry_run,
                user_id=_CLI_USER_ID,
            )
    except CryptoError as exc:
        return _err(str(exc))
    for line in report.migrated:
        print(f"migrated: {line}")  # noqa: T201
    for line in report.skipped:
        print(f"skipped: {line}")  # noqa: T201
    for path in report.removed_files:
        print(f"removed: {path}")  # noqa: T201
    for line in report.errors:
        print(f"error: {line}", file=sys.stderr)  # noqa: T201
    return 1 if report.errors else 0
