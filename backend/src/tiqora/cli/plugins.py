"""``tiqora plugins ...`` CLI: inspect, verify and bake plugins (no DB access)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tiqora.plugins.bake import BakeError, bake, core_constraints, verify_installed_plugins
from tiqora.plugins.discovery import discover_plugins

DEFAULT_IMPORT_TIMEOUT = 20.0


def add_plugins_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("plugins", help="Plugins: list, verify and bake into an image")
    plugins_sub = p.add_subparsers(dest="plugins_command")

    list_p = plugins_sub.add_parser("list", help="List installed plugins (no import, no DB)")
    list_p.add_argument("--json", action="store_true", dest="as_json")
    list_p.set_defaults(func=_cmd_list)

    verify_p = plugins_sub.add_parser("verify", help="Check installed plugins; exit 1 on problems")
    verify_p.add_argument(
        "--import", action="store_true", dest="import_check", help="also load each entry point"
    )
    verify_p.add_argument(
        "--strict", action="store_true", help="fail when a plugin requires tiqora/tiqora-sdk"
    )
    verify_p.add_argument("--timeout", type=float, default=DEFAULT_IMPORT_TIMEOUT)
    verify_p.set_defaults(func=_cmd_verify)

    constraints_p = plugins_sub.add_parser(
        "constraints", help="Print Name==version of every installed distribution"
    )
    constraints_p.set_defaults(func=_cmd_constraints)

    bake_p = plugins_sub.add_parser(
        "bake",
        help="Generate Dockerfile + plugins.txt (+ wheels/) for an image with plugins baked in",
        description=(
            "plugins.txt: pinned names, 'name @ https://…whl', local .whl paths, optional "
            "--hash=sha256:… per line (recommended: pip then requires hashes) and at most one "
            "--index-url. Git sources and --extra-index-url are rejected."
        ),
    )
    bake_p.add_argument("plugins_txt", type=Path)
    bake_p.add_argument("--output", "-o", type=Path, required=True)
    bake_p.add_argument("--base-image", default=None)
    bake_p.set_defaults(func=_cmd_bake)

    keygen_p = plugins_sub.add_parser(
        "keygen", help="Create an Ed25519 signing key (PKCS#8 PEM, mode 0600)"
    )
    keygen_p.add_argument("--out", type=Path, required=True, help="private key file (new)")
    keygen_p.set_defaults(func=_cmd_keygen)

    pack_p = plugins_sub.add_parser("pack", help="Pack a plugin wheel into an unsigned .tqplugin")
    pack_p.add_argument("wheel", type=Path)
    pack_p.add_argument("--changelog", type=Path, default=None)
    pack_p.add_argument("--output", "-o", type=Path, required=True)
    pack_p.add_argument("--force", action="store_true", help="overwrite an existing output file")
    pack_p.set_defaults(func=_cmd_pack)

    sign_p = plugins_sub.add_parser("sign", help="Sign a .tqplugin (adds or replaces SIGNATURE)")
    sign_p.add_argument("package", type=Path)
    sign_p.add_argument("--key", type=Path, required=True, help="private key from keygen")
    sign_p.add_argument("--output", "-o", type=Path, default=None)
    sign_p.add_argument("--force", action="store_true", help="overwrite the input in place")
    sign_p.set_defaults(func=_cmd_sign)

    inspect_p = plugins_sub.add_parser(
        "inspect", help="Check a .tqplugin like the install dialog does (no database)"
    )
    inspect_p.add_argument("package", type=Path)
    inspect_p.add_argument("--json", action="store_true", dest="as_json")
    inspect_p.set_defaults(func=_cmd_inspect)

    migrate_p = plugins_sub.add_parser(
        "migrate", help="Run the database changes of one plugin and record the result"
    )
    migrate_p.add_argument("plugin_id")
    migrate_p.add_argument(
        "--path",
        type=Path,
        default=None,
        help="site directory of a freshly unpacked package (no activation of installed ones)",
    )
    migrate_p.set_defaults(func=_cmd_migrate)

    index_p = plugins_sub.add_parser("index", help="Build and sign a repository index.json")
    index_sub = index_p.add_subparsers(dest="index_command")
    build_p = index_sub.add_parser(
        "build",
        help="Index every *.tqplugin in a directory",
        description=(
            "One publisher per call; to combine several publishers edit index.json by hand "
            "(then sign it again)."
        ),
    )
    build_p.add_argument("directory", metavar="DIR", type=Path)
    build_p.add_argument("--repository-id", required=True)
    build_p.add_argument("--name", required=True)
    build_p.add_argument("--publisher-key", default=None, help="ed25519:… public key")
    build_p.add_argument("--publisher-id", default=None)
    build_p.add_argument("--publisher-name", default=None)
    build_p.add_argument("--output", "-o", type=Path, required=True)
    build_p.set_defaults(func=_cmd_index_build)
    isign_p = index_sub.add_parser("sign", help="Write index.json.sig for an index.json")
    isign_p.add_argument("index", type=Path)
    isign_p.add_argument("--key", type=Path, required=True)
    isign_p.set_defaults(func=_cmd_index_sign)


async def _activate_installed() -> None:
    """Best effort (5 s, no writes): make installed runtime packages visible to discovery."""
    from tiqora.config import get_settings
    from tiqora.plugins.runtime import activate_best_effort

    await activate_best_effort(get_settings(), materialize=False, status_only=True)


async def _cmd_list(args: argparse.Namespace) -> int:
    from tiqora.plugins.compat import check_compat, release_core_version

    await _activate_installed()
    core = release_core_version()
    rows = []
    for d in discover_plugins():
        problem = d.problem
        if problem is None and d.manifest is not None:
            problem = check_compat(d.manifest, core=core)
        rows.append(
            {
                "id": d.plugin_id,
                "name": d.manifest.name if d.manifest else None,
                "version": d.dist_version,
                "distribution": d.dist_name,
                "problem": problem,
            }
        )
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))  # noqa: T201 — CLI output
    else:
        for r in rows:
            status = r["problem"] or "ok"
            print(f"{r['id']}  {r['version']}  {status}")  # noqa: T201 — CLI output
    return 0


async def _cmd_verify(args: argparse.Namespace) -> int:
    await _activate_installed()
    problems = await verify_installed_plugins(
        import_check=args.import_check, strict=args.strict, timeout=args.timeout
    )
    for line in problems:
        print(line, file=sys.stderr)  # noqa: T201 — CLI output
    return 1 if problems else 0


async def _cmd_constraints(args: argparse.Namespace) -> int:
    for line in core_constraints():
        print(line)  # noqa: T201 — CLI output
    return 0


async def _cmd_bake(args: argparse.Namespace) -> int:
    try:
        bake(args.plugins_txt, args.output, base_image=args.base_image)
    except (BakeError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)  # noqa: T201 — CLI output
        return 2
    print(f"Geschrieben nach {args.output}")  # noqa: T201 — CLI output
    return 0


def _err(text: str) -> None:
    print(text, file=sys.stderr)  # noqa: T201 — CLI output


async def _cmd_keygen(args: argparse.Namespace) -> int:
    from tiqora.plugins.signing import generate_keypair, key_id, parse_public_key

    pem, public = generate_keypair()
    try:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _err(f"Fehler: {args.out} existiert bereits und wird nicht überschrieben")
        return 2
    except OSError as exc:
        _err(f"Fehler: {exc}")
        return 2
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
    except OSError as exc:
        os.unlink(args.out)
        _err(f"Fehler: {exc}")
        return 2
    print(f"Privater Schlüssel: {args.out}")  # noqa: T201 — CLI output
    print(f"public_key: {public}")  # noqa: T201 — CLI output
    print(f"key_id: {key_id(parse_public_key(public))}")  # noqa: T201 — CLI output
    return 0


def _load_key(path: Path) -> Any:
    from cryptography.exceptions import UnsupportedAlgorithm

    from tiqora.plugins.signing import SignatureError, load_private_key

    try:
        return load_private_key(path.read_bytes())
    except (SignatureError, OSError) as exc:
        _err(f"Fehler: {exc}")
        return None
    except UnsupportedAlgorithm:
        _err("Fehler: Privater Schlüssel konnte nicht gelesen werden")
        return None


async def _cmd_pack(args: argparse.Namespace) -> int:
    from tiqora.config import get_settings
    from tiqora.plugins.package import PackageError, read_tqplugin, write_tqplugin

    if args.output.exists() and not args.force:
        _err(f"Fehler: {args.output} existiert bereits (--force zum Überschreiben)")
        return 2
    tmp = args.output.with_name(args.output.name + ".tmp")
    try:
        changelog = args.changelog.read_text(encoding="utf-8") if args.changelog else None
        write_tqplugin(args.wheel, tmp, changelog=changelog)
        # Same reader the install paths use: fail at pack time, not at install time.
        read_tqplugin(tmp.read_bytes(), max_bytes=get_settings().plugins_max_package_bytes)
        os.replace(tmp, args.output)
    except (PackageError, OSError, UnicodeDecodeError) as exc:
        _err(f"Fehler: {exc}")
        return 1
    finally:
        tmp.unlink(missing_ok=True)
    print(f"Geschrieben nach {args.output}")  # noqa: T201 — CLI output
    return 0


def _same_path(a: Path, b: Path) -> bool:
    return os.path.abspath(a) == os.path.abspath(b)


async def _cmd_sign(args: argparse.Namespace) -> int:
    import zipfile

    from tiqora.config import get_settings
    from tiqora.plugins.package import PackageError, add_signature, read_tqplugin
    from tiqora.plugins.signing import sign_bytes

    out: Path = args.output or args.package
    if _same_path(out, args.package) and not args.force:
        _err("Fehler: Die Datei würde überschrieben (--output angeben oder --force)")
        return 2
    private = _load_key(args.key)
    if private is None:
        return 2
    try:
        read_tqplugin(args.package.read_bytes(), max_bytes=get_settings().plugins_max_package_bytes)
        with zipfile.ZipFile(args.package) as zf:
            checksums = zf.read("CHECKSUMS")
        add_signature(args.package, sign_bytes(private, checksums), out)
    except (PackageError, OSError, KeyError, zipfile.BadZipFile) as exc:
        _err(f"Fehler: {exc}")
        return 1
    print(f"Signiert: {out}")  # noqa: T201 — CLI output
    return 0


async def _cmd_inspect(args: argparse.Namespace) -> int:
    from tiqora.config import get_settings
    from tiqora.plugins.inspect import inspect_package
    from tiqora.plugins.package import PackageError
    from tiqora.plugins.signing import OFFICIAL_KEYS, KeyRing, local_keys, parse_public_key

    settings = get_settings()
    ring = KeyRing(
        official={k: parse_public_key(v) for k, v in OFFICIAL_KEYS.items()},
        local=local_keys(settings),
        publishers=(),
    )
    try:
        report = inspect_package(
            args.package.read_bytes(),
            settings=settings,
            ring=ring,
            installed={},
            image_plugin_ids=frozenset(),
        )
    except (PackageError, OSError) as exc:
        _err(f"Fehler: {exc}")
        return 1
    if args.as_json:
        print(report.model_dump_json(indent=2))  # noqa: T201 — CLI output
    else:
        print(f"{report.plugin_id} {report.version}  Vertrauen: {report.trust.value}")  # noqa: T201
        for line in report.problems:
            print(f"Problem: {line}")  # noqa: T201 — CLI output
        for line in report.warnings:
            print(f"Hinweis: {line}")  # noqa: T201 — CLI output
        print("installierbar" if report.installable else "nicht installierbar")  # noqa: T201
    return 0 if report.installable else 1


async def _cmd_migrate(args: argparse.Namespace) -> int:
    import asyncio
    from importlib.metadata import distributions

    from tiqora.config import get_settings
    from tiqora.jobs.service import utc_now
    from tiqora.plugins.discovery import ENTRY_POINT_GROUP
    from tiqora.plugins.migrations import (
        PluginMigrationError,
        plugin_migration_head,
        plugin_session_factory,
        record_migration,
        upgrade_plugin,
    )

    settings = get_settings()
    if args.path is not None:
        # no activation: the installed version must not be on sys.path next to the new one
        site = str(args.path.resolve())
        entry_points = [
            ep
            for dist in distributions(path=[site])
            for ep in dist.entry_points
            if ep.group == ENTRY_POINT_GROUP
        ]
        sys.path.insert(0, site)
        found = discover_plugins(entry_points_=entry_points)
    else:
        found = discover_plugins()
    matches = [d for d in found if d.plugin_id == args.plugin_id]
    if not matches:
        _err(f"Fehler: Erweiterung {args.plugin_id} nicht gefunden")
        return 1
    plugin = matches[0]
    if plugin.manifest is None or plugin.problem is not None:
        _err(f"Fehler: Erweiterung {args.plugin_id} ist ungültig: {plugin.problem}")
        return 1
    version = plugin.manifest.version
    error: str | None = None
    head: str | None = None
    try:
        # the plugin environment runs its own event loop: keep it off this one
        head = await asyncio.to_thread(upgrade_plugin, plugin, database_url=settings.database_url)
        head = head or plugin_migration_head(plugin)
    except PluginMigrationError as exc:
        error = " ".join(str(exc).split())  # one line: the parent reads the last stderr line
    async with plugin_session_factory(settings.database_url) as factory:
        await record_migration(
            factory, plugin.plugin_id, version=version, head=head, error=error, now=utc_now()
        )
    if error is not None:
        _err(error)
        return 1
    print(f"Erweiterung {plugin.plugin_id}: Datenbank aktuell ({version})")  # noqa: T201
    return 0


async def _cmd_index_build(args: argparse.Namespace) -> int:
    import hashlib

    from packaging.version import InvalidVersion, Version

    from tiqora.config import get_settings
    from tiqora.plugins.package import PackageError, TqPackage, read_tqplugin
    from tiqora.plugins.repo_index import IndexFormatError, parse_index
    from tiqora.plugins.signing import SignatureError, key_id, parse_public_key

    flags = (args.publisher_key, args.publisher_id, args.publisher_name)
    if any(flags) and not all(flags):
        _err("Fehler: --publisher-key, --publisher-id und --publisher-name nur zusammen angeben")
        return 2
    if not args.directory.is_dir():
        _err(f"Fehler: {args.directory} ist kein Verzeichnis")
        return 2
    max_bytes = get_settings().plugins_max_package_bytes
    found: dict[str, list[tuple[TqPackage, str]]] = {}
    digests: dict[str, str] = {}
    for path in sorted(args.directory.glob("*.tqplugin")):
        try:
            data = path.read_bytes()
            pkg = read_tqplugin(data, max_bytes=max_bytes)
        except (PackageError, OSError) as exc:
            _err(f"Fehler: {path.name}: {exc}")
            return 1
        found.setdefault(pkg.manifest.id, []).append((pkg, path.name))
        digests[path.name] = hashlib.sha256(data).hexdigest()

    def _ver(item: tuple[TqPackage, str]) -> Version:
        try:
            return Version(item[0].manifest.version)
        except InvalidVersion:
            return Version("0")

    publishers: list[dict[str, Any]] = []
    if args.publisher_key:
        try:
            key = parse_public_key(args.publisher_key)
        except SignatureError as exc:
            _err(f"Fehler: {exc}")
            return 2
        publishers.append(
            {
                "id": args.publisher_id,
                "name": args.publisher_name,
                "keys": [{"key_id": key_id(key), "public_key": args.publisher_key}],
            }
        )
    plugins: list[dict[str, Any]] = []
    for plugin_id in sorted(found):
        items = sorted(found[plugin_id], key=_ver, reverse=True)
        m = items[0][0].manifest
        versions = []
        for pkg, name in items:
            versions.append(
                {
                    "version": pkg.manifest.version,
                    "url": quote(name),
                    "sha256": digests[name],
                    "size": pkg.size_bytes,
                    "sdk": pkg.manifest.sdk,
                    "tiqora": pkg.manifest.tiqora,
                    "requires": dict(pkg.manifest.requires),
                    "changelog": pkg.changelog,
                }
            )
        plugins.append(
            {
                "id": m.id,
                "short_id": m.short_id,
                "name": m.name,
                "publisher_id": args.publisher_id,
                "description": m.description,
                "categories": list(m.categories),
                "license": m.license,
                "homepage": m.homepage,
                "support": m.support,
                "versions": versions,
            }
        )
    index = {
        "index_version": 1,
        "repository": {
            "id": args.repository_id,
            "name": args.name,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "publishers": publishers,
        "plugins": plugins,
    }
    raw = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        parse_index(raw)
    except IndexFormatError as exc:
        _err(f"Fehler: {exc}")
        return 1
    try:
        args.output.write_bytes(raw)
    except OSError as exc:
        _err(f"Fehler: {exc}")
        return 2
    print(f"Geschrieben nach {args.output} ({len(plugins)} Erweiterungen)")  # noqa: T201
    return 0


async def _cmd_index_sign(args: argparse.Namespace) -> int:
    from tiqora.plugins.signing import sign_bytes

    private = _load_key(args.key)
    if private is None:
        return 2
    try:
        raw = args.index.read_bytes()
    except OSError as exc:
        _err(f"Fehler: {exc}")
        return 2
    out = args.index.with_name(args.index.name + ".sig")
    try:
        out.write_bytes(sign_bytes(private, raw))
    except OSError as exc:
        _err(f"Fehler: {exc}")
        return 2
    print(f"Geschrieben nach {out}")  # noqa: T201 — CLI output
    return 0
