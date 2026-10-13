"""Build real (and deliberately broken) wheels and .tqplugin packages from the fixtures."""

from __future__ import annotations

import hashlib
import shutil
import tomllib
import warnings
import zipfile
from base64 import urlsafe_b64encode
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.plugins.helpers import FIXTURES, apply_manifest_overrides
from tiqora.plugins.package import add_signature, build_checksums, write_tqplugin
from tiqora.plugins.signing import (
    generate_keypair,
    key_id,
    load_private_key,
    parse_public_key,
    sign_bytes,
)

_DATE = (1980, 1, 1, 0, 0, 0)


def make_keypair() -> tuple[Ed25519PrivateKey, str, str]:
    pem, pub = generate_keypair()
    return load_private_key(pem), pub, key_id(parse_public_key(pub))


def _fresh_dir(tmp: Path, prefix: str) -> Path:
    n = 0
    while (tmp / f"{prefix}{n}").exists():
        n += 1
    path = tmp / f"{prefix}{n}"
    path.mkdir(parents=True)
    return path


def _zip_members(path: Path, members: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)


def build_wheel(
    tmp: Path,
    package: str = "tq_fixture_hello",
    *,
    plugin_id: str = "org.example.hello",
    version: str = "1.0.0",
    dist_name: str | None = None,
    tag: str = "py3-none-any",
    requires_dist: Sequence[str] = (),
    extra_members: Mapping[str, bytes] = {},  # noqa: B006 - read-only default
    drop: Sequence[str] = (),
    manifest_overrides: Mapping[str, Any] | None = None,
    record_tamper: str | None = None,
) -> Path:
    """A pip-less wheel of ``fixtures/<package>`` with real RECORD hashes.

    ``plugin_id`` and ``version`` also go into the manifest (so the default wheel is valid);
    override them with ``manifest_overrides`` to build a mismatch on purpose. ``drop`` names
    package files (relative to the package dir) or, with a ``….dist-info/`` prefix, wheel
    members; ``extra_members`` adds or replaces wheel members (path relative to wheel root).
    """
    work = _fresh_dir(tmp, "wheel")
    pkg_dir = work / "src" / package
    shutil.copytree(FIXTURES / package, pkg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    dist = dist_name or package.replace("_", "-")
    escaped = dist.replace("-", "_")
    info_dir = f"{escaped}-{version}.dist-info"
    for rel in drop:
        if ".dist-info/" not in rel:
            (pkg_dir / rel).unlink()
    overrides: dict[str, Any] = {"id": plugin_id, "version": version}
    overrides.update(manifest_overrides or {})
    apply_manifest_overrides(pkg_dir / "tiqora-plugin.toml", overrides)

    entry = tomllib.loads((pkg_dir / "tiqora-plugin.toml").read_text())["entry"]
    meta = ["Metadata-Version: 2.1", f"Name: {dist}", f"Version: {version}"]
    meta += [f"Requires-Dist: {r}" for r in requires_dist]
    members: dict[str, bytes] = {}
    for p in sorted(pkg_dir.rglob("*")):
        if p.is_file():
            members[p.relative_to(pkg_dir.parent).as_posix()] = p.read_bytes()
    members[f"{info_dir}/METADATA"] = ("\n".join(meta) + "\n").encode()
    members[f"{info_dir}/WHEEL"] = (
        f"Wheel-Version: 1.0\nGenerator: tiqora-test\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()
    )
    members[f"{info_dir}/entry_points.txt"] = f"[tiqora.plugins]\n{plugin_id} = {entry}\n".encode()
    members.update(extra_members)
    for rel in drop:
        members.pop(rel, None)
    lines = []
    for name, data in members.items():
        digest = urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if name == record_tamper:
            digest = "A" * 43
        lines.append(f"{name},sha256={digest},{len(data)}")
    lines.append(f"{info_dir}/RECORD,,")
    members[f"{info_dir}/RECORD"] = ("\n".join(lines) + "\n").encode()

    out = work / "dist"
    out.mkdir()
    wheel = out / f"{escaped}-{version}-{tag}.whl"
    _zip_members(wheel, members)
    return wheel


def build_tqplugin(
    tmp: Path,
    wheel: Path,
    *,
    sign_with: Ed25519PrivateKey | None = None,
    changelog: str | None = None,
    toml_override: bytes | None = None,
    extra_members: Mapping[str, bytes] = {},  # noqa: B006 - read-only default
    skip_checksum_for: Sequence[str] = (),
) -> Path:
    """A .tqplugin via the real writer; the knobs produce invalid packages on purpose."""
    out_dir = _fresh_dir(tmp, "pkg")
    plain = out_dir / f"{wheel.stem}.tqplugin"
    write_tqplugin(wheel, plain, changelog=changelog)
    result = plain
    if toml_override is not None or extra_members or skip_checksum_for:
        with zipfile.ZipFile(plain) as zf:
            members = {n: zf.read(n) for n in zf.namelist() if n not in ("CHECKSUMS", "SIGNATURE")}
        if toml_override is not None:
            members["tiqora-plugin.toml"] = toml_override
        members.update(extra_members)
        listed = {n: d for n, d in members.items() if n not in skip_checksum_for}
        members["CHECKSUMS"] = build_checksums(listed)
        result = out_dir / f"{wheel.stem}-custom.tqplugin"
        _zip_members(result, members)
    if sign_with is not None:
        with zipfile.ZipFile(result) as zf:
            checksums = zf.read("CHECKSUMS")
        signed = out_dir / f"{result.stem}-signed.tqplugin"
        add_signature(result, sign_bytes(sign_with, checksums), signed)
        result = signed
    return result


def raw_zip(tmp: Path, members: Sequence[tuple[str, bytes, int]]) -> Path:
    """Hand-made zip for attacks: ``(name, data, external_attr)``; duplicates are kept."""
    path = _fresh_dir(tmp, "raw") / "raw.zip"
    with warnings.catch_warnings(), zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        warnings.simplefilter("ignore")  # "Duplicate name" is the point
        for name, data, attr in members:
            info = zipfile.ZipInfo(name, date_time=_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = attr
            zf.writestr(info, data)
    return path
