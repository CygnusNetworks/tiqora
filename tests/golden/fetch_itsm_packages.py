#!/usr/bin/env python3
"""Download, verify and unpack the Znuny ITSM packages of a golden ITSM peer (I15).

    python3 tests/golden/fetch_itsm_packages.py znuny-6.5-itsm            # fetch + verify
    python3 tests/golden/fetch_itsm_packages.py znuny-7.3 --record        # sha256 on first fetch

``<peer>`` is an ITSM peer id (``base:`` in peers.yaml resolves the package list) or a base
peer id. Files land in ``tests/golden/itsm-packages/<base>/`` (mounted read-only into the peer
at /opt/itsm-packages); the unpacked sources for reading go to
``tests/golden/itsm-packages/src/<Package>-<version>/``. The sources are never executed by
Tiqora. The whole directory is gitignored.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

GOLDEN_DIR = Path(__file__).resolve().parent
PACKAGES_FILE = GOLDEN_DIR / "itsm-packages.yaml"
PEERS_FILE = GOLDEN_DIR / "peers.yaml"
OUT_DIR = GOLDEN_DIR / "itsm-packages"
MAX_NESTING = 3  # bundle -> package; a little slack, never unbounded


def base_peer(peer: str) -> str:
    peers = yaml.safe_load(PEERS_FILE.read_text(encoding="utf-8"))["peers"]
    meta = peers.get(peer)
    if meta is None:
        raise SystemExit(f"unknown peer {peer!r}")
    return str(meta.get("base") or peer)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    if not url.startswith("https://download.znuny.org/"):
        raise SystemExit(f"refusing to download from {url!r}")
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as out:  # noqa: S310
        while chunk := response.read(1 << 20):
            out.write(chunk)
    tmp.replace(target)


def _safe_relative(location: str) -> Path:
    path = Path(location)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"unsafe file location in package: {location!r}")
    return path


def unpack(opm: Path, dest_root: Path, depth: int = 0) -> None:
    """Write the ``<File>`` entries of an .opm below ``dest_root/<Name>-<Version>/``."""
    if depth > MAX_NESTING:
        raise SystemExit(f"package nesting deeper than {MAX_NESTING}: {opm}")
    root = ET.fromstring(opm.read_bytes())  # noqa: S314 - official, hash-pinned packages
    name = (root.findtext("Name") or "").strip()
    version = (root.findtext("Version") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]+", name) or not re.fullmatch(r"[0-9][0-9.]*", version):
        raise SystemExit(f"unexpected package identity in {opm}: {name!r} {version!r}")
    target = dest_root / f"{name}-{version}"
    target.mkdir(parents=True, exist_ok=True)
    for entry in root.findall("./Filelist/File"):
        location = _safe_relative(entry.get("Location", ""))
        raw = entry.text or ""
        data = base64.b64decode(raw) if entry.get("Encode") == "Base64" else raw.encode()
        out = target / location
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        if location.suffix == ".opm":  # bundle: unpack the embedded packages too
            unpack(out, dest_root, depth + 1)
    print(f"  unpacked {name}-{version} -> {target.relative_to(GOLDEN_DIR)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("peer")
    parser.add_argument("--record", action="store_true", help="write missing sha256 values")
    parser.add_argument("--no-unpack", action="store_true")
    args = parser.parse_args(argv)

    base = base_peer(args.peer)
    spec = yaml.safe_load(PACKAGES_FILE.read_text(encoding="utf-8"))
    if base not in spec:
        raise SystemExit(f"no package list for {base!r} in {PACKAGES_FILE.name}")
    package_dir = OUT_DIR / base
    package_dir.mkdir(parents=True, exist_ok=True)
    text = PACKAGES_FILE.read_text(encoding="utf-8")
    changed = False
    for package in spec[base]["packages"]:
        target = package_dir / f"{package['name']}-{package['version']}.opm"
        if not target.is_file():
            print(f"download {package['url']}")
            download(package["url"], target)
        actual = sha256_of(target)
        expected = package.get("sha256") or ""
        if not expected:
            if not args.record:
                raise SystemExit(f"{target.name}: no sha256 recorded (run with --record)")
            marker = f'{package["url"]}\n      sha256: ""'
            if marker not in text:
                raise SystemExit(f"cannot locate sha256 slot for {package['url']}")
            text = text.replace(marker, f'{package["url"]}\n      sha256: "{actual}"')
            changed = True
            print(f"recorded {target.name} {actual}")
        elif actual != expected:
            target.unlink()
            raise SystemExit(f"{target.name}: sha256 mismatch (expected {expected}, got {actual})")
        else:
            print(f"ok {target.name} {actual[:12]}")
        if not args.no_unpack:
            unpack(target, OUT_DIR / "src")
    # Read by znuny-entrypoint.sh inside the peer (no YAML parser needed there).
    order = [f"{p['name']}-{p['version']}.opm" for p in spec[base]["packages"]]
    (package_dir / "install-order.txt").write_text("\n".join(order) + "\n", encoding="utf-8")
    (package_dir / "provides.txt").write_text(
        "\n".join(spec[base]["provides"]) + "\n", encoding="utf-8"
    )
    if changed:
        PACKAGES_FILE.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
