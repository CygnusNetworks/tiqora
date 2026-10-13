"""``.tqplugin`` envelope and wheel reading (D2/D4), without pip and without touching disk.

Pure module: ``read_tqplugin`` takes bytes and either returns a validated ``TqPackage`` or
raises ``PackageError`` (German message, names a member, never its content). Every zip
member is read through a counting decompressor because zip headers can lie about sizes.
"""

from __future__ import annotations

import configparser
import csv
import email.parser
import hashlib
import io
import os
import re
import struct
import zipfile
import zlib
from base64 import urlsafe_b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from tiqora.plugins.discovery import ENTRY_POINT_GROUP, MANIFEST_NAME
from tiqora.plugins.manifest import ManifestError, PluginManifest, parse_manifest

MAX_ENVELOPE_MEMBERS: Final = 64
MAX_WHEEL_MEMBERS: Final = 5000
MAX_UNCOMPRESSED: Final = 200 * 1024 * 1024
MAX_MEMBER: Final = 50 * 1024 * 1024
MAX_RATIO: Final = 200
# The ratio is only meaningful for members big enough to hurt; tiny files compress oddly.
_RATIO_MIN_SIZE: Final = 64 * 1024
# WheelInfo.files keeps only what the static checks need (F4) and stays small.
MAX_KEPT_FILES_BYTES: Final = 2 * 1024 * 1024
_KEPT_SUFFIXES: Final = (".py", ".toml", ".json")
_MAX_CHANGELOG: Final = 64 * 1024
_MAX_SIGNATURE: Final = 4 * 1024
_CHUNK: Final = 1024 * 1024
_FIXED_DATE: Final = (1980, 1, 1, 0, 0, 0)
_CHECKSUMS_HEADER: Final = "tqplugin-checksums 1"
_SPECIAL: Final = ("CHECKSUMS", "SIGNATURE")
_NATIVE_RE: Final = re.compile(r"\.(so(\.\d+)*|pyd|dll|dylib|exe)$", re.IGNORECASE)
_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:")
_HEX_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_WHEEL_NAME_RE: Final = re.compile(
    r"^(?P<dist>[A-Za-z0-9_.]+)-(?P<ver>[A-Za-z0-9_.!+]+)(-\d[\w.]*)?-(?P<tag>[^-]+-[^-]+-[^-]+)\.whl$"
)
_LOCAL_HEADER: Final = struct.Struct("<4s5H3L2H")


class PackageError(ValueError):
    """The package is unusable; the message is German and shown to admins."""


@dataclass(frozen=True, slots=True)
class WheelInfo:
    filename: str
    dist_name: str
    version: str
    tag: str
    top_level: tuple[str, ...]
    package: str
    requires_dist: tuple[str, ...]
    entry_value: str
    # Only the .py/.toml/.json members the static checks need, at most MAX_KEPT_FILES_BYTES
    # (F4); ``files_truncated`` says the cap cut the rest off.
    files: Mapping[str, bytes]
    files_truncated: bool = False


@dataclass(frozen=True, slots=True)
class TqPackage:
    sha256: str
    size_bytes: int
    manifest_text: str
    manifest: PluginManifest
    checksums: bytes
    signature: bytes | None
    changelog: str | None
    wheel_bytes: bytes
    wheel: WheelInfo


# --- member names and counting reader ------------------------------------------------------


def _label(name: str) -> str:
    return name if len(name) <= 120 else name[:117] + "..."


def _member_err(name: str, text: str) -> PackageError:
    return PackageError(f"Paketinhalt „{_label(name)}“: {text}")


def _check_name(name: str) -> None:
    bad = (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or _DRIVE_RE.match(name) is not None
    )
    if not bad:
        segments = (name[:-1] if name.endswith("/") else name).split("/")
        bad = any(seg in ("", ".", "..") for seg in segments)
    if bad:
        raise _member_err(name, "unzulässiger Pfad")


@dataclass(slots=True)
class _Read:
    size: int
    sha256: bytes  # raw digest
    data: bytes | None


def _read_member(raw: bytes, info: zipfile.ZipInfo, keep: bool) -> _Read:
    """Decompress one member from ``raw`` with hard caps on the bytes actually produced."""
    name = info.filename
    if info.flag_bits & 0x1:
        raise _member_err(name, "verschlüsselte Einträge sind nicht erlaubt")
    start = info.header_offset
    head = raw[start : start + _LOCAL_HEADER.size]
    if len(head) < _LOCAL_HEADER.size:
        raise _member_err(name, "beschädigter Eintrag")
    sig, _v, _f, _c, _t, _d, _crc, _cs, _us, nlen, elen = _LOCAL_HEADER.unpack(head)
    if sig != b"PK\x03\x04":
        raise _member_err(name, "beschädigter Eintrag")
    body = raw[start + _LOCAL_HEADER.size + nlen + elen :][: info.compress_size]
    declared = info.file_size
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    crc = 0

    def take(out: bytes) -> None:
        nonlocal total, crc
        total += len(out)
        if total > MAX_MEMBER or total > declared:
            raise _member_err(name, "ist zu groß (Größenangabe stimmt nicht oder Zip-Bombe)")
        digest.update(out)
        crc = zlib.crc32(out, crc)
        if keep:
            chunks.append(out)

    if info.compress_type == zipfile.ZIP_STORED:
        for i in range(0, len(body), _CHUNK):
            take(body[i : i + _CHUNK])
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        dec = zlib.decompressobj(-15)
        try:
            for i in range(0, len(body), _CHUNK):
                pending = body[i : i + _CHUNK]
                while pending and not dec.eof:
                    out = dec.decompress(pending, _CHUNK)
                    pending = dec.unconsumed_tail
                    take(out)
            if not dec.eof:
                raise _member_err(name, "beschädigter Eintrag")
        except zlib.error:
            raise _member_err(name, "beschädigter Eintrag") from None
    else:
        raise _member_err(name, "nicht unterstützte Komprimierung")
    if total != declared or crc != info.CRC:
        raise _member_err(name, "beschädigter Eintrag (Größe oder CRC stimmt nicht)")
    if (
        total >= _RATIO_MIN_SIZE
        and total > MAX_RATIO * max(info.compress_size, 1)
        and info.compress_type != zipfile.ZIP_STORED
    ):
        raise _member_err(name, "Kompressionsverhältnis zu groß (Zip-Bombe?)")
    return _Read(total, digest.digest(), b"".join(chunks) if keep else None)


def _open_zip(raw: bytes, what: str) -> list[zipfile.ZipInfo]:
    try:
        return zipfile.ZipFile(io.BytesIO(raw)).infolist()
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise PackageError(f"{what} ist keine gültige ZIP-Datei") from None


def _read_zip(
    raw: bytes,
    infos: list[zipfile.ZipInfo],
    *,
    max_members: int,
    keep: Callable[[str], bool],
    what: str,
) -> dict[str, _Read]:
    """Validate names/sizes from the central directory, then read every file member."""
    files = [i for i in infos if not i.is_dir()]
    if len(infos) > max_members:
        raise PackageError(f"{what} enthält zu viele Einträge (mehr als {max_members})")
    seen: set[str] = set()
    total_declared = 0
    for info in infos:
        name = info.filename
        _check_name(name)
        folded = name.rstrip("/").lower()
        if folded in seen:
            raise _member_err(name, "kommt doppelt vor")
        seen.add(folded)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise _member_err(name, "Verknüpfungen (Symlinks) sind nicht erlaubt")
        if info.is_dir() and info.file_size:
            raise _member_err(name, "Verzeichniseintrag mit Inhalt")
        total_declared += info.file_size
        if info.file_size > MAX_MEMBER or total_declared > MAX_UNCOMPRESSED:
            raise _member_err(name, "ist zu groß")
        if (
            info.file_size >= _RATIO_MIN_SIZE
            and info.file_size > MAX_RATIO * max(info.compress_size, 1)
            and info.compress_type != zipfile.ZIP_STORED
        ):
            raise _member_err(name, "Kompressionsverhältnis zu groß (Zip-Bombe?)")
    out: dict[str, _Read] = {}
    running = 0
    for info in files:
        read = _read_member(raw, info, keep(info.filename))
        running += read.size
        if running > MAX_UNCOMPRESSED:
            raise _member_err(info.filename, "ist zu groß (Gesamtgröße überschritten)")
        out[info.filename] = read
    return out


# --- CHECKSUMS -----------------------------------------------------------------------------


def build_checksums(members: Mapping[str, bytes]) -> bytes:
    """Deterministic CHECKSUMS bytes for ``members`` (CHECKSUMS/SIGNATURE are skipped)."""
    lines = [_CHECKSUMS_HEADER]
    for name in sorted(n for n in members if n not in _SPECIAL):
        lines.append(f"{hashlib.sha256(members[name]).hexdigest()}  {name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _parse_checksums(raw: bytes) -> dict[str, str]:
    bad = PackageError("CHECKSUMS ist ungültig oder beschädigt")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad from None
    if not text.endswith("\n") or "\r" in text:
        raise bad
    lines = text[:-1].split("\n")
    if lines[0] != _CHECKSUMS_HEADER:
        raise bad
    listed: dict[str, str] = {}
    previous = ""
    for line in lines[1:]:
        digest, sep, name = line.partition("  ")
        if not sep or not _HEX_RE.match(digest) or not name or name in listed:
            raise bad
        if name <= previous:
            raise bad  # not sorted (or duplicate)
        previous = name
        listed[name] = digest
    return listed


# --- reading ---------------------------------------------------------------------------------


def _mb(n: int) -> str:
    return f"{n // (1024 * 1024)} MB" if n >= 1024 * 1024 else f"{n} Byte"


def read_tqplugin(data: bytes, *, max_bytes: int) -> TqPackage:
    if len(data) > max_bytes:
        raise PackageError(f"Das Paket ist größer als {_mb(max_bytes)}")
    infos = _open_zip(data, "Die Datei")
    if not infos:
        raise PackageError("Die Datei ist keine gültige .tqplugin-Datei (leer)")
    reads = _read_zip(
        data,
        infos,
        max_members=MAX_ENVELOPE_MEMBERS,
        keep=lambda _n: True,
        what="Das Paket",
    )
    members = {n: r.data or b"" for n, r in reads.items()}

    if "CHECKSUMS" not in members:
        raise PackageError("CHECKSUMS fehlt im Paket")
    checksums = members["CHECKSUMS"]
    listed = _parse_checksums(checksums)
    body = {n: r for n, r in reads.items() if n not in _SPECIAL}
    for name in sorted(body):
        if name not in listed:
            raise _member_err(name, "steht nicht in CHECKSUMS")
    for name in sorted(listed):
        if name not in body:
            raise _member_err(name, "steht in CHECKSUMS, fehlt aber im Paket")
    for name, read in body.items():
        if read.sha256.hex() != listed[name]:
            raise _member_err(name, "Prüfsumme stimmt nicht")

    if MANIFEST_NAME not in members:
        raise PackageError(f"{MANIFEST_NAME} fehlt im Paket")
    wheels = [n for n in members if re.fullmatch(r"python/[^/]+\.whl", n)]
    if len(wheels) != 1:
        raise PackageError("Das Paket muss genau ein Wheel unter python/ enthalten")
    signature = members.get("SIGNATURE")
    if signature is not None and len(signature) > _MAX_SIGNATURE:
        raise PackageError("SIGNATURE ist zu groß")
    changelog: str | None = None
    if "CHANGELOG.md" in members:
        if len(members["CHANGELOG.md"]) > _MAX_CHANGELOG:
            raise PackageError("CHANGELOG.md ist zu groß (mehr als 64 KB)")
        try:
            changelog = members["CHANGELOG.md"].decode("utf-8")
        except UnicodeDecodeError:
            raise PackageError("CHANGELOG.md ist kein gültiger UTF-8-Text") from None

    toml_bytes = members[MANIFEST_NAME]
    try:
        manifest_text = toml_bytes.decode("utf-8")
        manifest = parse_manifest(manifest_text)
    except UnicodeDecodeError:
        raise PackageError(f"{MANIFEST_NAME} ist kein gültiger UTF-8-Text") from None
    except ManifestError as exc:
        raise PackageError(str(exc)) from None

    wheel_bytes = members[wheels[0]]
    wheel = _read_wheel(wheels[0].split("/", 1)[1], wheel_bytes, manifest, toml_bytes)
    return TqPackage(
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        manifest_text=manifest_text,
        manifest=manifest,
        checksums=checksums,
        signature=signature,
        changelog=changelog,
        wheel_bytes=wheel_bytes,
        wheel=wheel,
    )


def _record_hash(digest: bytes) -> str:
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _read_wheel(
    filename: str, raw: bytes, manifest: PluginManifest, envelope_toml: bytes
) -> WheelInfo:
    pure = PackageError("Das Paket muss reines Python sein (Wheel-Tag py3-none-any)")
    try:
        _n, _v, _b, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        raise PackageError(f"Wheel-Dateiname „{_label(filename)}“ ist ungültig") from None
    match = _WHEEL_NAME_RE.match(filename)
    if match is None:
        raise PackageError(f"Wheel-Dateiname „{_label(filename)}“ ist ungültig")
    tag_strings = {str(t) for t in tags}
    if not tag_strings <= {"py3-none-any", "py2-none-any"} or "py3-none-any" not in tag_strings:
        raise pure
    dist, ver = match["dist"], match["ver"]
    dist_info = f"{dist}-{ver}.dist-info"

    infos = _open_zip(raw, "Das Wheel")

    def keep(name: str) -> bool:
        first = name.split("/", 1)[0]
        if first.endswith(".dist-info"):
            return name in (
                f"{dist_info}/WHEEL",
                f"{dist_info}/METADATA",
                f"{dist_info}/RECORD",
                f"{dist_info}/entry_points.txt",
            )
        return name.endswith(_KEPT_SUFFIXES)  # the 2 MB cap is applied below

    reads = _read_zip(raw, infos, max_members=MAX_WHEEL_MEMBERS, keep=keep, what="Das Wheel")
    names = list(reads)
    tops: set[str] = set()
    dist_infos: set[str] = set()
    for name in names:
        first = name.split("/", 1)[0]
        if first.endswith(".egg-info"):
            raise _member_err(name, "egg-info-Verzeichnisse sind nicht erlaubt")
        if first.endswith(".data"):
            raise PackageError(
                "Paket enthält Installationsdaten außerhalb von Python – bitte als Image einbauen"
            )
        if "/" not in name:
            if name.endswith(".pth"):
                raise PackageError("Startdateien (.pth) sind nicht erlaubt")
            if not name.endswith(".py"):
                raise _member_err(name, "liegt außerhalb eines Python-Pakets")
            tops.add(name[:-3])
        elif first.endswith(".dist-info"):
            dist_infos.add(first)
        else:
            tops.add(first)
        if _NATIVE_RE.search(name):
            raise _member_err(name, "native Bibliotheken sind nicht erlaubt (nur reines Python)")
    if dist_infos != {dist_info}:
        raise PackageError(f"Das Wheel muss genau eine dist-info enthalten („{_label(dist_info)}“)")

    def member(rel: str) -> bytes:
        read = reads.get(f"{dist_info}/{rel}")
        if read is None or read.data is None:
            raise PackageError(f"{rel} fehlt im Wheel")
        return read.data

    _check_record(member("RECORD"), reads, f"{dist_info}/RECORD")
    wheel_meta = email.parser.BytesParser().parsebytes(member("WHEEL"), headersonly=True)
    if (wheel_meta.get("Root-Is-Purelib") or "").strip().lower() != "true":
        raise pure
    meta = email.parser.BytesParser().parsebytes(member("METADATA"), headersonly=True)
    requires = tuple(meta.get_all("Requires-Dist") or ())
    if f"{dist_info}/entry_points.txt" not in reads:
        raise PackageError("Einstiegspunkt: entry_points.txt fehlt im Wheel")
    entry_value = _entry_value(member("entry_points.txt"), manifest)

    package = entry_value.split(":", 1)[0].split(".", 1)[0].strip()
    if package not in tops:
        raise PackageError(f"Einstiegspunkt „{entry_value}“ verweist auf kein Paket im Wheel")
    toml_member = f"{package}/{MANIFEST_NAME}"
    inner = reads.get(toml_member)
    if inner is None or inner.data is None:
        raise PackageError(f"{MANIFEST_NAME} fehlt im Python-Paket „{package}“ des Wheels")
    if inner.data != envelope_toml:
        raise PackageError(f"Die {MANIFEST_NAME} im Paket stimmt nicht mit der im Wheel überein")
    try:
        same = Version(manifest.version) == Version(ver) == Version(meta.get("Version", ""))
    except InvalidVersion:
        same = False
    if not same:
        raise PackageError(
            f"Die Version im Wheel ({ver}) stimmt nicht mit der Version im Manifest "
            f"({manifest.version}) überein"
        )

    kept: dict[str, bytes] = {}
    kept_bytes = 0
    truncated = False
    for name in names:
        if name.split("/", 1)[0].endswith(".dist-info") or not name.endswith(_KEPT_SUFFIXES):
            continue
        blob = reads[name].data or b""
        if kept_bytes + len(blob) > MAX_KEPT_FILES_BYTES:
            truncated = True
            continue
        kept[name] = blob
        kept_bytes += len(blob)
    return WheelInfo(
        filename=filename,
        dist_name=dist,
        version=ver,
        tag="py3-none-any",
        top_level=tuple(sorted(tops)),
        package=package,
        requires_dist=requires,
        entry_value=entry_value,
        files=MappingProxyType(kept),
        files_truncated=truncated,
    )


def _check_record(record: bytes, reads: Mapping[str, _Read], record_name: str) -> None:
    listed: dict[str, tuple[str, str]] = {}
    try:
        for row in csv.reader(io.StringIO(record.decode("utf-8"))):
            if len(row) != 3:
                raise ValueError
            listed[row[0]] = (row[1], row[2])
    except (UnicodeDecodeError, ValueError, csv.Error):
        raise PackageError("RECORD im Wheel ist beschädigt") from None
    for name, read in reads.items():
        if name == record_name:
            continue
        entry = listed.get(name)
        if entry is None:
            raise _member_err(name, "fehlt in RECORD")
        algo, _, value = entry[0].partition("=")
        if algo != "sha256" or value != _record_hash(read.sha256) or entry[1] != str(read.size):
            raise _member_err(name, "RECORD-Prüfsumme oder -Größe stimmt nicht")


def _entry_value(raw: bytes, manifest: PluginManifest) -> str:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        parser.read_string(raw.decode("utf-8"))
    except (configparser.Error, UnicodeDecodeError):
        raise PackageError("entry_points.txt im Wheel ist beschädigt") from None
    if not parser.has_section(ENTRY_POINT_GROUP):
        raise PackageError(f"Einstiegspunkt: Abschnitt [{ENTRY_POINT_GROUP}] fehlt im Wheel")
    items = dict(parser.items(ENTRY_POINT_GROUP))
    if len(items) != 1 or manifest.id not in items:
        raise PackageError(
            f"Einstiegspunkt: [{ENTRY_POINT_GROUP}] muss genau „{manifest.id}“ enthalten"
        )
    value = items[manifest.id].strip()
    if value != manifest.entry:
        raise PackageError(
            f"Einstiegspunkt „{value}“ stimmt nicht mit manifest.entry „{manifest.entry}“ überein"
        )
    return value


# --- unpacking -------------------------------------------------------------------------------


def unpack_wheel(wheel_bytes: bytes, target: Path) -> None:
    """Write a wheel's members below ``target`` (purelib layout), re-validating first.

    Nothing is written unless every member passes the D4 reader rules; each file is created
    with ``open(..., "xb")`` and mode 0o644 (zip permissions are never kept), directories
    get 0o755, and a resolved path outside ``target`` raises ``PackageError``. An
    ``INSTALLER`` file with ``tiqora`` is added to the ``.dist-info``.
    """
    infos = _open_zip(wheel_bytes, "Das Wheel")
    reads = _read_zip(
        wheel_bytes,
        infos,
        max_members=MAX_WHEEL_MEMBERS,
        keep=lambda _n: True,
        what="Das Wheel",
    )
    dist_infos: set[str] = set()
    for name in reads:
        first = name.split("/", 1)[0]
        if first.endswith(".egg-info") or first.endswith(".data"):
            raise _member_err(name, "nicht erlaubt")
        if "/" not in name and not name.endswith(".py"):
            raise _member_err(name, "liegt außerhalb eines Python-Pakets")
        if _NATIVE_RE.search(name):
            raise _member_err(name, "native Bibliotheken sind nicht erlaubt (nur reines Python)")
        if first.endswith(".dist-info"):
            dist_infos.add(first)
    if len(dist_infos) != 1:
        raise PackageError("Das Wheel muss genau eine dist-info enthalten")
    installer = f"{next(iter(dist_infos))}/INSTALLER"

    root = target.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def place(rel: str) -> Path:
        dest = (root / rel).resolve()
        if dest == root or not dest.is_relative_to(root):
            raise _member_err(rel, "unzulässiger Pfad")
        return dest

    def make_dirs(directory: Path) -> None:
        missing: list[Path] = []
        cur = directory
        while cur != root and not cur.exists():
            missing.append(cur)
            cur = cur.parent
        for d in reversed(missing):
            d.mkdir(mode=0o755)
            d.chmod(0o755)  # umask must not narrow it

    plan = [(place(name), name) for name in reads if name != installer]
    plan.append((place(installer), installer))
    for dest, name in plan:
        make_dirs(dest.parent)
        content = b"tiqora\n" if name == installer else (reads[name].data or b"")
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        dest.chmod(0o644)


# --- writing -------------------------------------------------------------------------------


def _zip_bytes(members: Mapping[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, members[name])
    return buf.getvalue()


def write_tqplugin(wheel: Path, out: Path, *, changelog: str | None = None) -> None:
    """Pack ``wheel`` into a deterministic .tqplugin (CHECKSUMS, no SIGNATURE)."""
    raw = wheel.read_bytes()
    infos = _open_zip(raw, "Das Wheel")
    tomls = [
        i.filename
        for i in infos
        if PurePosixPath(i.filename).name == MANIFEST_NAME and i.filename.count("/") == 1
    ]
    if len(tomls) != 1:
        raise PackageError(f"Das Wheel muss genau eine {MANIFEST_NAME} im Paketverzeichnis haben")
    toml = _read_member(raw, next(i for i in infos if i.filename == tomls[0]), True).data or b""
    members: dict[str, bytes] = {MANIFEST_NAME: toml, f"python/{wheel.name}": raw}
    if changelog is not None:
        members["CHANGELOG.md"] = changelog.encode("utf-8")
    members["CHECKSUMS"] = build_checksums(members)
    out.write_bytes(_zip_bytes(members))


def add_signature(package: Path, signature: bytes, out: Path) -> None:
    """Rewrite ``package`` with ``SIGNATURE`` (replacing an existing one)."""
    raw = package.read_bytes()
    infos = _open_zip(raw, "Die Datei")
    members = {
        i.filename: (_read_member(raw, i, True).data or b"") for i in infos if not i.is_dir()
    }
    members["SIGNATURE"] = signature
    out.write_bytes(_zip_bytes(members))
