"""`.tqplugin` envelope and wheel reading (D2/D4): valid packages and every rejection."""

from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path

import pytest

from tests.plugins.package_builder import build_tqplugin, build_wheel, make_keypair, raw_zip
from tiqora.plugins.package import (
    MAX_ENVELOPE_MEMBERS,
    PackageError,
    add_signature,
    build_checksums,
    read_tqplugin,
    write_tqplugin,
)
from tiqora.plugins.signing import parse_public_key, verify_signature

MB20 = 20 * 1024 * 1024
DIST_INFO = "tq_fixture_hello-1.0.0.dist-info"


def _members(pkg: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(pkg) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _envelope(
    tmp: Path,
    entries: list[tuple[str, bytes]],
    *,
    attrs: dict[str, int] | None = None,
    checksum_over: dict[str, bytes] | None = None,
) -> bytes:
    """Valid base package plus ``entries`` (CHECKSUMS recomputed unless overridden)."""
    base = _members(build_tqplugin(tmp, build_wheel(tmp)))
    base.pop("CHECKSUMS")
    rows = list(base.items()) + entries
    listed = checksum_over if checksum_over is not None else dict(rows)
    rows.append(("CHECKSUMS", build_checksums(listed)))
    path = raw_zip(tmp, [(n, d, (attrs or {}).get(n, 0o644 << 16)) for n, d in rows])
    return path.read_bytes()


def _patch_size(data: bytes, new_size: int) -> bytes:
    """Rewrite the uncompressed size in local and central header of the only member."""
    out = bytearray(data)
    local = out.index(b"PK\x03\x04")
    struct.pack_into("<L", out, local + 22, new_size)
    central = out.index(b"PK\x01\x02")
    struct.pack_into("<L", out, central + 24, new_size)
    return bytes(out)


def _make_case(tmp: Path, case: str) -> bytes:  # noqa: C901 - one branch per case
    wheel_kw: dict = {}
    match case:
        case "zip_slip":
            return _envelope(tmp, [("../evil.py", b"x")])
        case "absolute":
            return _envelope(tmp, [("/etc/evil", b"x")])
        case "backslash":
            return _envelope(tmp, [("a\\b", b"x")])
        case "drive":
            return _envelope(tmp, [("C:evil", b"x")])
        case "symlink":
            return _envelope(tmp, [("link", b"/etc/passwd")], attrs={"link": 0o120777 << 16})
        case "duplicate":
            return _envelope(tmp, [("CHANGELOG.md", b"a"), ("CHANGELOG.md", b"b")])
        case "duplicate_case":
            return _envelope(tmp, [("CHANGELOG.md", b"a"), ("changelog.md", b"b")])
        case "unlisted_member":
            base = _members(build_tqplugin(tmp, build_wheel(tmp)))
            return _envelope(
                tmp,
                [("extra.txt", b"x")],
                checksum_over={n: d for n, d in base.items() if n != "CHECKSUMS"},
            )
        case "listed_missing":
            base = _members(build_tqplugin(tmp, build_wheel(tmp)))
            listed = {n: d for n, d in base.items() if n != "CHECKSUMS"}
            listed["ghost.txt"] = b"g"
            return _envelope(tmp, [], checksum_over=listed)
        case "bad_hash":
            base = _members(build_tqplugin(tmp, build_wheel(tmp)))
            listed = {n: d for n, d in base.items() if n != "CHECKSUMS"}
            listed["tiqora-plugin.toml"] = b"something else"
            return _envelope(tmp, [], checksum_over=listed)
        case "checksums_header":
            members = _members(build_tqplugin(tmp, build_wheel(tmp)))
            members["CHECKSUMS"] = members["CHECKSUMS"].replace(
                b"tqplugin-checksums 1", b"tqplugin-checksums 2"
            )
            return raw_zip(tmp, [(n, d, 0o644 << 16) for n, d in members.items()]).read_bytes()
        case "two_wheels":
            return _envelope(tmp, [("python/other-1.0.0-py3-none-any.whl", b"x")])
        case "no_toml":
            members = _members(build_tqplugin(tmp, build_wheel(tmp)))
            del members["tiqora-plugin.toml"]
            members["CHECKSUMS"] = build_checksums(members)
            return raw_zip(tmp, [(n, d, 0o644 << 16) for n, d in members.items()]).read_bytes()
        case "toml_differs":
            toml = _members(build_tqplugin(tmp, build_wheel(tmp)))["tiqora-plugin.toml"]
            other = toml.replace(b"Testerweiterung", b"Andere Beschreibung")
            assert other != toml
            return build_tqplugin(tmp, build_wheel(tmp), toml_override=other).read_bytes()
        case "too_many_members":
            extra = [(f"extra/{i}.txt", b"x") for i in range(MAX_ENVELOPE_MEMBERS + 6)]
            return _envelope(tmp, extra)
        case "bomb_ratio":
            return raw_zip(tmp, [("big.bin", b"\0" * (20 * 1024 * 1024), 0o644 << 16)]).read_bytes()
        case "bomb_size_lie":
            zipped = raw_zip(tmp, [("big.bin", b"\0" * (60 * 1024 * 1024), 0o644 << 16)])
            return _patch_size(zipped.read_bytes(), 10)
        case "record_mismatch":
            wheel_kw = {"record_tamper": "tq_fixture_hello/plugin.py"}
        case "native_so":
            wheel_kw = {"extra_members": {"tq_fixture_hello/speed.so": b"\x7fELF"}}
        case "tag_cp312":
            wheel_kw = {"tag": "cp312-none-any"}
        case "data_dir":
            wheel_kw = {"extra_members": {"tq_fixture_hello-1.0.0.data/scripts/x": b"x"}}
        case "pth_file":
            wheel_kw = {"extra_members": {"evil.pth": b"import os"}}
        case "not_purelib":
            wheel_kw = {
                "extra_members": {
                    f"{DIST_INFO}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\n"
                }
            }
        case "entry_point_missing":
            wheel_kw = {"drop": [f"{DIST_INFO}/entry_points.txt"]}
        case "entry_mismatch":
            wheel_kw = {
                "extra_members": {
                    f"{DIST_INFO}/entry_points.txt": (
                        b"[tiqora.plugins]\norg.example.hello = tq_fixture_other:plugin\n"
                    )
                }
            }
        case "egg_info":
            wheel_kw = {"extra_members": {"tq_fixture_hello.egg-info/PKG-INFO": b"x"}}
        case "two_dist_info":
            wheel_kw = {"extra_members": {"other-2.0.dist-info/METADATA": b"Name: other\n"}}
        case "version_mismatch":
            wheel_kw = {"version": "1.0.1", "manifest_overrides": {"version": "1.0.0"}}
        case "not_a_zip":
            return b"this is not a zip file at all"
        case _:
            raise AssertionError(case)
    return build_tqplugin(tmp, build_wheel(tmp, **wheel_kw)).read_bytes()


def test_read_valid_package(tmp_path: Path) -> None:
    data = build_tqplugin(
        tmp_path, build_wheel(tmp_path), changelog="# 1.0.0\n- erste Version\n"
    ).read_bytes()
    pkg = read_tqplugin(data, max_bytes=MB20)
    assert pkg.manifest.id == "org.example.hello"
    assert pkg.wheel.top_level == ("tq_fixture_hello",)
    assert pkg.wheel.package == "tq_fixture_hello"
    assert pkg.wheel.version == "1.0.0"
    assert pkg.wheel.tag == "py3-none-any"
    assert pkg.wheel.entry_value == "tq_fixture_hello:plugin"
    assert pkg.wheel.filename == "tq_fixture_hello-1.0.0-py3-none-any.whl"
    assert pkg.signature is None
    assert pkg.sha256 == hashlib.sha256(data).hexdigest()
    assert pkg.size_bytes == len(data)
    assert pkg.changelog is not None and "erste Version" in pkg.changelog
    assert pkg.checksums.startswith(b"tqplugin-checksums 1\n")
    assert pkg.manifest_text.startswith("manifest_version = 1")
    # the wheel inside is the one that was packed
    assert zipfile.is_zipfile(__import__("io").BytesIO(pkg.wheel_bytes))


def test_wheel_files_keep_only_checkable_sources(tmp_path: Path) -> None:
    pkg = read_tqplugin(
        build_tqplugin(tmp_path, build_wheel(tmp_path)).read_bytes(), max_bytes=MB20
    )
    files = pkg.wheel.files
    assert "tq_fixture_hello/plugin.py" in files and "tq_fixture_hello/tiqora-plugin.toml" in files
    assert not any(n.endswith(("RECORD", "METADATA", ".css", ".svg")) for n in files)
    assert pkg.wheel.files_truncated is False


def test_wheel_files_are_capped(tmp_path: Path) -> None:
    big = "".join(f"v{i} = {i * 7919 % 10007}\n" for i in range(110_000)).encode()  # ~1.4 MB each
    wheel = build_wheel(
        tmp_path,
        extra_members={"tq_fixture_hello/big1.py": big, "tq_fixture_hello/big2.py": big},
    )
    pkg = read_tqplugin(build_tqplugin(tmp_path, wheel).read_bytes(), max_bytes=MB20)
    assert pkg.wheel.files_truncated is True
    assert sum(len(v) for v in pkg.wheel.files.values()) <= 2 * 1024 * 1024
    assert len(pkg.wheel.files) >= 1


def test_requires_dist_is_read(tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, requires_dist=["httpx>=0.27", "tiqora"])
    pkg = read_tqplugin(build_tqplugin(tmp_path, wheel).read_bytes(), max_bytes=MB20)
    assert pkg.wheel.requires_dist == ("httpx>=0.27", "tiqora")


@pytest.mark.parametrize(
    ("case", "needle"),
    [
        ("zip_slip", "Pfad"),
        ("absolute", "Pfad"),
        ("backslash", "Pfad"),
        ("drive", "Pfad"),
        ("symlink", "Verknüpfung"),
        ("duplicate", "doppelt"),
        ("duplicate_case", "doppelt"),
        ("unlisted_member", "CHECKSUMS"),
        ("listed_missing", "CHECKSUMS"),
        ("bad_hash", "Prüfsumme"),
        ("checksums_header", "CHECKSUMS"),
        ("two_wheels", "genau ein"),
        ("no_toml", "tiqora-plugin.toml"),
        ("toml_differs", "stimmt nicht"),
        ("too_many_members", "zu viele"),
        ("bomb_ratio", "zu groß"),
        ("bomb_size_lie", "zu groß"),
        ("record_mismatch", "RECORD"),
        ("native_so", "nativ"),
        ("tag_cp312", "reines Python"),
        ("data_dir", "Installationsdaten"),
        ("pth_file", ".pth"),
        ("not_purelib", "reines Python"),
        ("entry_point_missing", "Einstiegspunkt"),
        ("entry_mismatch", "Einstiegspunkt"),
        ("egg_info", "egg-info"),
        ("two_dist_info", "dist-info"),
        ("version_mismatch", "Version"),
        ("not_a_zip", "keine gültige"),
    ],
)
def test_read_rejects(tmp_path: Path, case: str, needle: str) -> None:
    data = _make_case(tmp_path, case)
    with pytest.raises(PackageError, match=needle):
        read_tqplugin(data, max_bytes=MB20)


def test_error_names_member_not_content(tmp_path: Path) -> None:
    data = _envelope(tmp_path, [("../evil.py", b"SECRET-CONTENT")])
    with pytest.raises(PackageError) as exc:
        read_tqplugin(data, max_bytes=MB20)
    assert "../evil.py" in str(exc.value) and "SECRET-CONTENT" not in str(exc.value)


def test_read_rejects_over_size_cap(tmp_path: Path) -> None:
    data = build_tqplugin(tmp_path, build_wheel(tmp_path)).read_bytes()
    with pytest.raises(PackageError, match="größer als"):
        read_tqplugin(data, max_bytes=100)


def test_unknown_listed_member_is_ignored(tmp_path: Path) -> None:
    pkg_path = build_tqplugin(tmp_path, build_wheel(tmp_path), extra_members={"README.txt": b"hi"})
    pkg = read_tqplugin(pkg_path.read_bytes(), max_bytes=MB20)
    assert pkg.manifest.id == "org.example.hello"


def test_signature_roundtrip(tmp_path: Path) -> None:
    priv, pub, _kid = make_keypair()
    p = build_tqplugin(tmp_path, build_wheel(tmp_path), sign_with=priv)
    pkg = read_tqplugin(p.read_bytes(), max_bytes=MB20)
    assert pkg.signature is not None
    verify_signature(pkg.signature, pkg.checksums, parse_public_key(pub))  # no exception


def test_signature_does_not_verify_after_tampering(tmp_path: Path) -> None:
    from tiqora.plugins.signing import SignatureError

    priv, pub, _kid = make_keypair()
    pkg = read_tqplugin(
        build_tqplugin(tmp_path, build_wheel(tmp_path), sign_with=priv).read_bytes(), max_bytes=MB20
    )
    assert pkg.signature is not None
    with pytest.raises(SignatureError):
        verify_signature(pkg.signature, pkg.checksums + b"x", parse_public_key(pub))


def test_add_signature_replaces_existing(tmp_path: Path) -> None:
    priv, _pub, _kid = make_keypair()
    signed = build_tqplugin(tmp_path, build_wheel(tmp_path), sign_with=priv)
    out = tmp_path / "again.tqplugin"
    add_signature(signed, b'{"format": 1}', out)
    assert _members(out)["SIGNATURE"] == b'{"format": 1}'
    assert sum(1 for n in zipfile.ZipFile(out).namelist() if n == "SIGNATURE") == 1


def test_write_tqplugin_is_deterministic(tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path)
    a, b = tmp_path / "a.tqplugin", tmp_path / "b.tqplugin"
    write_tqplugin(wheel, a, changelog="x")
    write_tqplugin(wheel, b, changelog="x")
    ma, mb = _members(a), _members(b)
    assert ma["CHECKSUMS"] == mb["CHECKSUMS"]
    assert a.read_bytes() == b.read_bytes()
    lines = ma["CHECKSUMS"].decode().split("\n")
    assert lines[0] == "tqplugin-checksums 1" and lines[-1] == ""
    names = [ln.split("  ", 1)[1] for ln in lines[1:-1]]
    assert names == sorted(names) and "CHECKSUMS" not in names and "SIGNATURE" not in names
    assert b"\r" not in ma["CHECKSUMS"]
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in zipfile.ZipFile(a).infolist())
