"""Repository index format v1 (D7): parsing, validation, signature check, version selection.

Pure: no I/O. Fetching, storing and the refresh loop live elsewhere. Everything in an index
comes from a remote party, so every URL that an admin page may link is restricted to
``https://`` (``mailto:`` for support addresses) and every size is capped (F14).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Final, Literal
from urllib.parse import urljoin, urlsplit

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ValidationError, field_validator

from tiqora.plugins.manifest import PLUGIN_ID_RE, SHORT_ID_RE
from tiqora.plugins.signing import (
    PublisherKey,
    SignatureError,
    key_id,
    parse_public_key,
    verify_signature,
)

log = structlog.get_logger(__name__)

MAX_INDEX_BYTES: Final = 5 * 1024 * 1024
MAX_URL_LENGTH: Final = 500

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID_RE: Final = re.compile(r"^[0-9a-f]{16}$")
_EMAIL_RE: Final = re.compile(r"^[^\s@:/\\<>]{1,64}@[A-Za-z0-9.-]{1,255}$")


class IndexFormatError(ValueError):
    """The index (or its signature) is unusable; the message is German."""


def safe_url(value: object, *, mailto: bool = False) -> str | None:
    """``value`` when it is a link an admin page may render, else ``None`` (with a warning).

    Only ``https://`` URLs of at most 500 characters pass. With ``mailto`` a bare e-mail
    address or a ``mailto:`` link is accepted too and returned as ``mailto:`` link.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH:
        log.warning("plugin_index_url_dropped", reason="type_or_length")
        return None
    if any(ord(c) < 32 or c == "\x7f" or c.isspace() for c in value):
        log.warning("plugin_index_url_dropped", reason="control_characters")
        return None
    if mailto:
        address = value.removeprefix("mailto:") if value.startswith("mailto:") else value
        if _EMAIL_RE.fullmatch(address):
            return f"mailto:{address}"
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.netloc and "\\" not in value:
        return value
    log.warning("plugin_index_url_dropped", reason="scheme")
    return None


def _url_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [u for u in (safe_url(v) for v in value) if u is not None]


class IndexKey(BaseModel):
    key_id: str
    public_key: str
    revoked: bool = False


class IndexPublisher(BaseModel):
    id: str
    name: str
    homepage: str | None = None
    support: str | None = None
    keys: list[IndexKey] = []

    @field_validator("homepage", mode="before")
    @classmethod
    def _homepage(cls, value: object) -> str | None:
        return safe_url(value)

    @field_validator("support", mode="before")
    @classmethod
    def _support(cls, value: object) -> str | None:
        return safe_url(value, mailto=True)


class IndexVersion(BaseModel):
    version: str
    url: str
    sha256: str
    size: int
    sdk: str
    tiqora: str | None = None
    requires: dict[str, str] = {}
    released_at: datetime | None = None
    changelog: str | None = None
    yanked: bool = False


class IndexPlugin(BaseModel):
    id: str
    short_id: str
    name: str
    publisher_id: str | None = None
    description: str = ""
    categories: list[str] = []
    license: str | None = None
    homepage: str | None = None
    support: str | None = None
    icon_url: str | None = None
    screenshots: list[str] = []
    versions: list[IndexVersion]

    @field_validator("homepage", "icon_url", mode="before")
    @classmethod
    def _link(cls, value: object) -> str | None:
        return safe_url(value)

    @field_validator("support", mode="before")
    @classmethod
    def _support(cls, value: object) -> str | None:
        return safe_url(value, mailto=True)

    @field_validator("screenshots", mode="before")
    @classmethod
    def _screenshots(cls, value: object) -> list[str]:
        return _url_list(value)


class IndexRepository(BaseModel):
    id: str
    name: str
    homepage: str | None = None
    generated_at: datetime

    @field_validator("homepage", mode="before")
    @classmethod
    def _homepage(cls, value: object) -> str | None:
        return safe_url(value)


class RepositoryIndex(BaseModel):
    index_version: Literal[1]
    repository: IndexRepository
    publishers: list[IndexPublisher] = []
    plugins: list[IndexPlugin] = []


def _format_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else str(err["msg"]))
    return "; ".join(parts)


def _check_specifier(value: str, where: str) -> None:
    try:
        SpecifierSet(value)
    except InvalidSpecifier:
        raise IndexFormatError(f"{where}: ungültige Versionsangabe „{value[:60]}“") from None


def _check_version(value: str, where: str) -> None:
    try:
        Version(value)
    except InvalidVersion:
        raise IndexFormatError(f"{where}: ungültige Version „{value[:60]}“") from None


def _check_publishers(index: RepositoryIndex) -> set[str]:
    ids: set[str] = set()
    for pub in index.publishers:
        if len(pub.id) > 100 or not PLUGIN_ID_RE.fullmatch(pub.id):
            raise IndexFormatError(f"Herausgeber-Kennung „{pub.id[:60]}“ ist ungültig")
        if pub.id in ids:
            raise IndexFormatError(f"Herausgeber „{pub.id}“ ist doppelt aufgeführt")
        ids.add(pub.id)
        seen_keys: set[str] = set()
        for key in pub.keys:
            if not _KEY_ID_RE.fullmatch(key.key_id):
                raise IndexFormatError(f"key_id „{key.key_id[:30]}“ von {pub.id} ist ungültig")
            try:
                real = key_id(parse_public_key(key.public_key))
            except SignatureError as exc:
                raise IndexFormatError(f"Schlüssel „{key.key_id}“ von {pub.id}: {exc}") from None
            if real != key.key_id:
                raise IndexFormatError(
                    f"key_id „{key.key_id}“ von {pub.id} passt nicht zum öffentlichen Schlüssel"
                )
            if key.key_id in seen_keys:
                raise IndexFormatError(f"key_id „{key.key_id}“ von {pub.id} ist doppelt")
            seen_keys.add(key.key_id)
    return ids


def _check_plugin(plugin: IndexPlugin, publisher_ids: set[str]) -> None:
    if len(plugin.id) > 100 or not PLUGIN_ID_RE.fullmatch(plugin.id):
        raise IndexFormatError(f"Kennung „{plugin.id[:60]}“ ist ungültig")
    if not SHORT_ID_RE.fullmatch(plugin.short_id):
        raise IndexFormatError(f"Kurzname „{plugin.short_id[:30]}“ von {plugin.id} ist ungültig")
    if plugin.publisher_id is not None and plugin.publisher_id not in publisher_ids:
        raise IndexFormatError(
            f"Herausgeber „{plugin.publisher_id[:60]}“ von {plugin.id} fehlt in der Paketquelle"
        )
    seen: set[Version] = set()
    for ver in plugin.versions:
        where = f"{plugin.id} {ver.version[:30]}"
        _check_version(ver.version, where)
        if Version(ver.version) in seen:
            raise IndexFormatError(f"Version {ver.version} von {plugin.id} ist doppelt")
        seen.add(Version(ver.version))
        if not _SHA256_RE.fullmatch(ver.sha256):
            raise IndexFormatError(f"{where}: sha256 muss aus 64 Hex-Zeichen bestehen")
        if ver.size < 0:
            raise IndexFormatError(f"{where}: size darf nicht negativ sein")
        if len(ver.url) > MAX_URL_LENGTH or not ver.url:
            raise IndexFormatError(f"{where}: Paketadresse ist leer oder zu lang")
        _check_specifier(ver.sdk, f"{where} sdk")
        if ver.tiqora is not None:
            _check_specifier(ver.tiqora, f"{where} tiqora")
        for req_id, spec in ver.requires.items():
            if not PLUGIN_ID_RE.fullmatch(req_id):
                raise IndexFormatError(f"{where}: ungültige Kennung „{req_id[:60]}“ in requires")
            _check_specifier(spec, f"{where} requires.{req_id}")


def parse_index(raw: bytes) -> RepositoryIndex:
    """Parse and validate ``index.json``; unknown fields are ignored (forward compatibility)."""
    if len(raw) > MAX_INDEX_BYTES:
        raise IndexFormatError("Die Paketquelle ist zu groß (mehr als 5 MB)")
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise IndexFormatError("Die Paketquelle ist kein gültiges JSON") from None
    if not isinstance(data, dict):
        raise IndexFormatError("Die Paketquelle muss ein JSON-Objekt sein")
    try:
        index = RepositoryIndex.model_validate(data)
    except ValidationError as exc:
        raise IndexFormatError(f"Paketquelle ungültig: {_format_errors(exc)}") from None
    publisher_ids = _check_publishers(index)
    plugin_ids: set[str] = set()
    for plugin in index.plugins:
        _check_plugin(plugin, publisher_ids)
        if plugin.id in plugin_ids:
            raise IndexFormatError(f"Kennung „{plugin.id}“ ist doppelt aufgeführt")
        plugin_ids.add(plugin.id)
    return index


def verify_index(raw: bytes, raw_sig: bytes | None, public_key: str | None) -> bool:
    """True = verified with the repository's configured key.

    No key configured: False (the index is usable, but its publisher keys are not trusted,
    Assumption 3). A key is configured but the signature is missing or wrong: refused.
    """
    if not public_key:
        return False
    refused = IndexFormatError("Signatur der Paketquelle ungültig")
    if raw_sig is None:
        raise refused
    try:
        key = parse_public_key(public_key)
        verify_signature(raw_sig, raw, key)
    except SignatureError:
        raise refused from None
    return True


def resolve_package_url(index_url: str, package_url: str) -> str:
    """Absolute package URL; ``https://`` repositories take relative or ``https://`` URLs,
    ``file://`` repositories only relative ones below the index's directory."""
    if (
        not package_url
        or len(package_url) > MAX_URL_LENGTH
        or "\\" in package_url
        or any(ord(c) < 32 or c == "\x7f" or c.isspace() for c in package_url)
    ):
        raise IndexFormatError("Ungültige Paketadresse in der Paketquelle")
    base = urlsplit(index_url)
    given = urlsplit(package_url)
    absolute = bool(given.scheme or given.netloc or package_url.startswith("/"))
    if absolute:
        if base.scheme == "file":
            raise IndexFormatError(
                "Paketquellen aus Dateien dürfen nur relative Paketadressen enthalten"
            )
        if given.scheme != "https" or not given.netloc:
            raise IndexFormatError("Paketadressen müssen mit https:// beginnen")
        return package_url
    resolved = urljoin(index_url, package_url)
    if base.scheme == "file":
        directory = base.path.rsplit("/", 1)[0] + "/"
        if not urlsplit(resolved).path.startswith(directory):
            raise IndexFormatError("Paketadresse liegt außerhalb der Paketquelle")
    return resolved


def newest_compatible(
    plugin: IndexPlugin, *, core: Version | None, sdk: str
) -> IndexVersion | None:
    """Highest non-yanked version whose ``sdk`` / ``tiqora`` ranges contain the running versions."""
    best: tuple[Version, IndexVersion] | None = None
    sdk_version = Version(sdk)
    for ver in plugin.versions:
        if ver.yanked:
            continue
        try:
            if not SpecifierSet(ver.sdk).contains(sdk_version, prereleases=True):
                continue
            if (
                ver.tiqora is not None
                and core is not None
                and not SpecifierSet(ver.tiqora).contains(core, prereleases=True)
            ):
                continue
            parsed = Version(ver.version)
        except (InvalidSpecifier, InvalidVersion):
            continue
        if best is None or parsed > best[0]:
            best = (parsed, ver)
    return best[1] if best else None


def publisher_keys(index: RepositoryIndex, repository_id: int) -> list[PublisherKey]:
    """Publisher keys of a VERIFIED index (never call this for an unverified one)."""
    out: list[PublisherKey] = []
    for pub in index.publishers:
        for key in pub.keys:
            out.append(
                PublisherKey(
                    key_id=key.key_id,
                    public_key=parse_public_key(key.public_key),
                    publisher_id=pub.id,
                    publisher_name=pub.name,
                    repository_id=repository_id,
                    revoked=key.revoked,
                )
            )
    return out
