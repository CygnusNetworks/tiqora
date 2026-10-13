"""Package sources (D8): configuration, environment sync, fetching, refresh, daemon tick, catalog.

The key ring is built from the publisher keys of verified indexes only. Fetching goes through
``pin_outbound_url`` (https) or a file root (env-only ``file:///``); an index is refused when it is
older than the stored one (rollback guard).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from urllib.parse import SplitResult, unquote, urlsplit

import httpx
import structlog
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginPackage, TiqoraPluginRepository
from tiqora.domain.schemas import UtcDateTime
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.repo_index import (
    MAX_INDEX_BYTES,
    IndexFormatError,
    IndexPlugin,
    IndexPublisher,
    IndexVersion,
    RepositoryIndex,
    newest_compatible,
    parse_index,
    publisher_keys,
    resolve_package_url,
    verify_index,
    version_compatible,
)
from tiqora.plugins.signing import (
    OFFICIAL_KEYS,
    KeyRing,
    PublisherKey,
    SignatureError,
    TrustLevel,
    local_keys,
    parse_public_key,
    publisher_verified_in,
)
from tiqora.security.outbound import HostResolver, OutboundURLError, pin_outbound_url

log = structlog.get_logger(__name__)


async def build_key_ring_named(
    session: AsyncSession, settings: Settings
) -> tuple[KeyRing, dict[int, str]]:
    """The key ring plus ``repository id -> name`` of the repositories that contributed keys.

    Publisher keys come only from enabled repositories whose last accepted index verified against
    the repository's own key (D7); an unverified index never gives a key any weight.
    """
    official = {}
    for kid, text in OFFICIAL_KEYS.items():
        try:
            official[kid] = parse_public_key(text)
        except SignatureError:  # pragma: no cover - the constant is ours
            log.warning("plugin_official_key_unreadable", key_id=kid)
    rows = (
        (
            await session.execute(
                select(TiqoraPluginRepository)
                .where(
                    TiqoraPluginRepository.enabled.is_(True),
                    TiqoraPluginRepository.index_verified.is_(True),
                    TiqoraPluginRepository.index_json.is_not(None),
                )
                .order_by(TiqoraPluginRepository.id)
            )
        )
        .scalars()
        .all()
    )
    publishers: list[PublisherKey] = []
    names: dict[int, str] = {}
    for row in rows:
        try:
            index = stored_index(row)
            keys = publisher_keys(index, row.id)
        except (IndexFormatError, SignatureError, ValueError) as exc:
            log.warning(
                "plugin_repository_index_unreadable", repository=row.id, error=str(exc)[:200]
            )
            continue
        publishers += keys
        names[row.id] = row.name
    ring = KeyRing(official=official, local=local_keys(settings), publishers=publishers)
    return ring, names


def ring_for_source(
    ring: KeyRing, names: dict[int, str], *, source: str, repository_id: int | None
) -> tuple[KeyRing, dict[int, str]]:
    """F10: a package that came from a repository may only be signed by that repository's keys.

    Other sources (upload) may use every verified repository's keys. A repository package whose
    repository is gone (``repository_id`` None) has no signing keys at all. The ring itself
    stays complete: two verified repositories claiming one publisher id with different keys
    make the id unverified, whichever of them the package came from.
    """
    if source != "repository":
        return ring, names
    allowed = frozenset() if repository_id is None else frozenset({repository_id})
    return (
        replace(ring, signer_repositories=allowed),
        {rid: name for rid, name in names.items() if rid == repository_id},
    )


async def build_key_ring(session: AsyncSession, settings: Settings) -> KeyRing:
    return (await build_key_ring_named(session, settings))[0]


def with_repository_signer(
    report: PackageReport, ring: KeyRing, names: dict[int, str]
) -> PackageReport:
    """F10: an upload signed with a publisher key names the repository that vouches for it."""
    if report.trust is not TrustLevel.VERIFIED_PUBLISHER or report.key_id is None:
        return report
    repo_ids = sorted(p.repository_id for p in ring.publishers if p.key_id == report.key_id)
    name = next((names[r] for r in repo_ids if r in names), None)
    if name is None or report.signer is None:
        return report
    return report.model_copy(update={"signer": f"{report.signer} (über Paketquelle {name})"})


MAX_URL_LENGTH: Final = 1000
MAX_NAME_LENGTH: Final = 200
ENV_NAME: Final = "TIQORA_PLUGIN_REPOSITORIES"


class RepositoryInputError(ValueError):
    """A repository entry that cannot be accepted; the message is German and shown to the admin."""


@dataclass(frozen=True)
class EnvRepository:
    name: str
    url: str
    public_key: str | None


def url_key(url: str) -> str:
    """The unique, indexable key of a repository URL (1000 characters cannot be indexed)."""
    return hashlib.sha256(url.encode()).hexdigest()


def _file_path_below_root(url: str, file_root: Path) -> None:
    path = Path(unquote(urlsplit(url).path))
    try:
        inside = path.resolve().is_relative_to(file_root.resolve())
    except (OSError, RuntimeError, ValueError):
        inside = False
    if not inside:
        raise RepositoryInputError(
            f"Die Adresse liegt nicht unterhalb von {file_root} "
            "(TIQORA_PLUGIN_REPOSITORY_FILE_ROOT)"
        )


def validate_repository_input(
    *,
    name: str,
    url: str,
    public_key: str | None,
    allow_file: bool = False,
    file_root: Path | None = None,
) -> tuple[str, str, str | None]:
    """Normalise one entry to ``(name, url, public_key)`` or raise ``RepositoryInputError``.

    Admin input allows ``https://`` only; the environment may also name ``file:///`` URLs below
    ``file_root`` (Assumption 9).
    """
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise RepositoryInputError(f"Der Name muss 1 bis {MAX_NAME_LENGTH} Zeichen lang sein")
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise RepositoryInputError(f"Die Adresse muss 1 bis {MAX_URL_LENGTH} Zeichen lang sein")
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise RepositoryInputError("Die Adresse darf keine Leer- oder Steuerzeichen enthalten")
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError for a bad port
    except ValueError:
        raise RepositoryInputError("Die Adresse ist ungültig") from None
    scheme = parts.scheme.lower()
    if scheme == "file" and allow_file:
        if parts.netloc not in ("", "localhost") or parts.query or "#" in url:
            raise RepositoryInputError("Die file-Adresse ist ungültig (file:///Pfad/index.json)")
        if file_root is None:  # pragma: no cover - callers pass it with allow_file
            raise RepositoryInputError("Dateiquellen sind hier nicht erlaubt")
        _file_path_below_root(url, file_root)
    elif scheme == "https":
        if not parts.hostname:
            raise RepositoryInputError("Die Adresse enthält keinen Servernamen")
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            raise RepositoryInputError(
                "Die Adresse darf keine Zugangsdaten enthalten (https://Benutzer:Passwort@…)"
            )
        if "#" in url:
            raise RepositoryInputError("Die Adresse darf kein Fragment (#…) enthalten")
    else:
        raise RepositoryInputError(
            "Es ist nur https:// erlaubt"
            + (" (oder file:/// unterhalb des Paketquellen-Verzeichnisses)" if allow_file else "")
        )
    key = (public_key or "").strip() or None
    if key is not None:
        try:
            parse_public_key(key)
        except SignatureError as exc:
            raise RepositoryInputError(str(exc)) from None
    return name, url, key


def parse_env_repositories(raw: str, *, file_root: Path) -> tuple[list[EnvRepository], list[str]]:
    """Read ``TIQORA_PLUGIN_REPOSITORIES``: valid entries plus German problems for skipped ones."""
    if not raw or not raw.strip():
        return [], []
    try:
        data = json.loads(raw)
    except ValueError:
        return [], [f"{ENV_NAME} ist kein gültiges JSON"]
    if not isinstance(data, list):
        return [], [f"{ENV_NAME} muss eine JSON-Liste sein"]
    repos: list[EnvRepository] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(data, start=1):
        label = (
            str(entry.get("name"))[:60]
            if isinstance(entry, dict) and entry.get("name")
            else f"#{i}"
        )
        if not isinstance(entry, dict):
            problems.append(f"Paketquelle {label}: Eintrag ist kein JSON-Objekt")
            continue
        try:
            fields = {k: entry.get(k) for k in ("name", "url", "public_key")}
            if any(v is not None and not isinstance(v, str) for v in fields.values()):
                raise RepositoryInputError("name, url und public_key müssen Zeichenketten sein")
            name, url, key = validate_repository_input(
                name=fields["name"] or "",
                url=fields["url"] or "",
                public_key=fields["public_key"],
                allow_file=True,
                file_root=file_root,
            )
        except RepositoryInputError as exc:
            problems.append(f"Paketquelle {label}: {exc}")
            continue
        if url in seen:
            problems.append(f"Paketquelle {label}: Adresse kommt mehrfach vor")
            continue
        seen.add(url)
        repos.append(EnvRepository(name=name, url=url, public_key=key))
    for problem in problems:
        log.warning("plugin_env_repository_skipped", problem=problem)
    return repos, problems


def env_value_unreadable(raw: str) -> bool:
    """True when the whole value is unreadable (bad JSON, not a list); empty is readable."""
    if not raw or not raw.strip():
        return False
    try:
        return not isinstance(json.loads(raw), list)
    except ValueError:
        return True


async def sync_env_repositories(
    session: AsyncSession, settings: Settings, now: datetime
) -> list[str]:
    """Upsert env entries by ``url_key``; env rows that left the env are disabled, never deleted.

    Admin rows are never touched. The caller commits. Returns the German problems.
    """
    repos, problems = parse_env_repositories(
        settings.plugin_repositories, file_root=Path(settings.plugin_repository_file_root)
    )
    now = now.replace(microsecond=0)  # MariaDB DATETIME has no fraction
    rows = (await session.execute(select(TiqoraPluginRepository))).scalars().all()
    by_key = {r.url_key: r for r in rows}
    wanted: set[str] = set()
    for repo in repos:
        key = url_key(repo.url)
        wanted.add(key)
        row = by_key.get(key)
        if row is None:
            session.add(
                TiqoraPluginRepository(
                    name=repo.name,
                    url=repo.url,
                    url_key=key,
                    public_key=repo.public_key,
                    enabled=True,
                    origin="env",
                    index_verified=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            continue
        if row.origin != "env":
            # the id, not the URL: a private index may carry a token in its query
            log.warning("plugin_env_repository_shadowed_by_admin", repository=row.id)
            continue
        changed = (row.name, row.public_key, row.enabled) != (repo.name, repo.public_key, True)
        if row.public_key != repo.public_key:
            row.index_verified = False  # the stored index was verified against another key
        row.name, row.public_key, row.enabled = repo.name, repo.public_key, True
        if changed:
            row.updated_at = now
    if env_value_unreadable(settings.plugin_repositories):
        # a typo in the whole value must not switch off every env source: rows have not left the env
        await session.flush()
        return problems
    for key, row in by_key.items():
        if row.origin == "env" and key not in wanted and row.enabled:
            row.enabled = False
            row.updated_at = now
    await session.flush()
    return problems


# ---------------------------------------------------------------- fetching

#: wall clock of one download (connect + body); httpx's per-phase timeout is shorter
TOTAL_FETCH_TIMEOUT: Final = 120.0
PHASE_TIMEOUT: Final = 30.0
MAX_SIGNATURE_BYTES: Final = 16 * 1024
STALE_PACKAGE_HOURS: Final = 24
#: the daemon tick stops starting new refreshes after this many seconds (tick timeout is 600 s)
TICK_BUDGET_SECONDS: Final = 480.0
MAX_ERROR_LENGTH: Final = 1000
#: an index may be dated this far ahead of our clock (clock skew); later is refused
FUTURE_TOLERANCE: Final = timedelta(days=1)


class FetchError(Exception):
    """A download that failed; the message is German and shown to the admin."""

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


def _megabytes(limit: int) -> str:
    return f"{max(1, -(-limit // (1024 * 1024)))} MB"


def _outbound_message(exc: OutboundURLError) -> str:
    text = str(exc)
    if "cannot resolve" in text or "no addresses" in text:
        return "Der Servername der Paketquelle konnte nicht aufgelöst werden"
    if "blocked address" in text:
        return "Adresse der Paketquelle ist nicht erlaubt (interne oder lokale Adresse)"
    return "Adresse der Paketquelle ist nicht erlaubt"


async def _fetch_https(
    url: str,
    *,
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None,
    resolver: HostResolver | None,
) -> bytes:
    try:
        # the resolver blocks on getaddrinfo: keep it off the event loop (R-ASYNC-RESOLVE)
        pinned = await asyncio.to_thread(
            pin_outbound_url,
            url,
            require_https=True,
            allow_private_networks=True,
            resolver=resolver,
        )
    except OutboundURLError as exc:
        raise FetchError(_outbound_message(exc)) from None
    # identity: the cap counts what arrives, not a decompressed bomb
    headers = pinned.request_headers({"Accept-Encoding": "identity"})
    body = bytearray()
    try:
        async with (
            asyncio.timeout(TOTAL_FETCH_TIMEOUT),
            httpx.AsyncClient(
                timeout=PHASE_TIMEOUT, follow_redirects=False, transport=transport
            ) as client,
            client.stream(
                "GET", pinned.request_url, headers=headers, extensions=pinned.request_extensions()
            ) as response,
        ):
            code = response.status_code
            if 300 <= code < 400:
                raise FetchError("Weiterleitungen werden nicht verfolgt")
            if code == 404:
                raise FetchError("Nicht gefunden (HTTP 404)", not_found=True)
            if code != 200:
                raise FetchError(f"Der Server antwortet mit HTTP {code}")
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise FetchError(f"Die Antwort ist zu groß (mehr als {_megabytes(max_bytes)})")
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FetchError(f"Die Antwort ist zu groß (mehr als {_megabytes(max_bytes)})")
    except TimeoutError:
        raise FetchError("Zeitüberschreitung beim Abruf der Paketquelle") from None
    except httpx.HTTPError as exc:
        raise FetchError(f"Paketquelle nicht erreichbar ({type(exc).__name__})") from None
    return bytes(body)


def _read_file_capped(url: str, file_root: Path, max_bytes: int) -> bytes:
    parts = urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise FetchError("Die file-Adresse ist ungültig")
    try:
        path = Path(unquote(parts.path)).resolve()
        root = file_root.resolve()
        if not path.is_relative_to(root):
            raise FetchError("Die Datei liegt außerhalb des Paketquellen-Verzeichnisses")
        if not path.is_file():
            raise FetchError("Datei nicht gefunden", not_found=True)
        if path.stat().st_size > max_bytes:
            raise FetchError(f"Die Datei ist zu groß (mehr als {_megabytes(max_bytes)})")
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except (OSError, RuntimeError, ValueError) as exc:
        raise FetchError(f"Datei nicht lesbar ({type(exc).__name__})") from None
    if len(data) > max_bytes:
        raise FetchError(f"Die Datei ist zu groß (mehr als {_megabytes(max_bytes)})")
    return data


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> bytes:
    """Download ``url`` (never more than ``max_bytes``); every failure is a German ``FetchError``.

    ``https://``: pinned to the resolved address (private networks allowed, loopback, link-local
    and metadata blocked), redirects are not followed. ``file://``: only below
    ``plugin_repository_file_root`` (env-defined sources). ``transport`` and ``resolver`` are
    for tests.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme == "https":
        return await _fetch_https(url, max_bytes=max_bytes, transport=transport, resolver=resolver)
    if scheme == "file":
        root = Path(settings.plugin_repository_file_root)
        return await asyncio.to_thread(_read_file_capped, url, root, max_bytes)
    raise FetchError("Es ist nur https:// (oder file:/// aus der Server-Konfiguration) erlaubt")


def package_url_problem(index_url: str, package_url: str, *, verified: bool) -> str | None:
    """F20: an index without a verified key may only name package URLs on its own host.

    Returns a German problem or None. ``package_url`` is the already resolved absolute URL.
    """
    if verified:
        return None
    base, given = urlsplit(index_url), urlsplit(package_url)
    if base.scheme == "file":
        return None  # resolve_package_url keeps file repositories below their directory

    def origin(parts: SplitResult) -> tuple[str, str, int]:
        return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or 443

    if origin(base) != origin(given):
        return (
            "Eine Paketquelle ohne Schlüssel darf nur auf Pakete unter ihrer eigenen Adresse "
            "verweisen. Bitte tragen Sie den Schlüssel der Paketquelle ein."
        )
    return None


# ---------------------------------------------------------------- refresh


@dataclass(frozen=True)
class RefreshResult:
    ok: bool
    plugins: int
    error: str | None


def _sig_url(url: str) -> str:
    parts = urlsplit(url)
    return parts._replace(path=parts.path + ".sig").geturl()


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)  # MariaDB DATETIME has no fraction


async def _record_error(
    session: AsyncSession, repo_id: int, message: str, now: datetime
) -> RefreshResult:
    """Keep the last good index; remember why the newest fetch was refused."""
    row = await session.get(TiqoraPluginRepository, repo_id)
    if row is not None:
        row.last_error = message[:MAX_ERROR_LENGTH]
        row.last_checked_at = now
    log.warning("plugin_repository_refresh_failed", repository=repo_id, error=message[:200])
    return RefreshResult(False, 0, message[:MAX_ERROR_LENGTH])


async def refresh_repository(
    session: AsyncSession,
    repo: TiqoraPluginRepository,
    *,
    settings: Settings,
    now: datetime,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> RefreshResult:
    """Fetch, verify and store one repository's index. The caller commits.

    Errors never raise: the last good index stays, ``last_error`` says why (German). The session's
    transaction is rolled back before the fetch, so nothing is held open during the network
    access; pass a session without pending changes. An index dated more than a day ahead is
    refused (a stored one would block every later index). The store step runs under a row lock
    and re-checks the rollback guard against the newest stored value (F20: a daemon tick and a
    refresh job may run for one repository at the same time).
    """
    repo_id, url, key, url_key_at_start = repo.id, repo.url, repo.public_key, repo.url_key
    now = now.replace(microsecond=0)
    # no transaction stays open during the network access (up to 2 x TOTAL_FETCH_TIMEOUT)
    await session.rollback()
    try:
        raw = await fetch_bytes(
            url,
            max_bytes=MAX_INDEX_BYTES,
            settings=settings,
            transport=transport,
            resolver=resolver,
        )
        raw_sig: bytes | None = None
        if key:  # without a key the index cannot be verified: no reason to ask for a signature
            try:
                raw_sig = await fetch_bytes(
                    _sig_url(url),
                    max_bytes=MAX_SIGNATURE_BYTES,
                    settings=settings,
                    transport=transport,
                    resolver=resolver,
                )
            except FetchError as exc:
                if not exc.not_found:
                    raise
        verified = verify_index(raw, raw_sig, key)
        index = parse_index(raw)
    except FetchError as exc:
        return await _record_error(session, repo_id, str(exc), now)
    except IndexFormatError as exc:
        return await _record_error(session, repo_id, str(exc), now)
    generated = _naive_utc(index.repository.generated_at)
    if generated > now + FUTURE_TOLERANCE:
        # stored, it would make the rollback guard refuse every later index for good
        return await _record_error(
            session, repo_id, "Paketquelle meldet einen Stand aus der Zukunft – abgewiesen", now
        )

    locked = await session.get(
        TiqoraPluginRepository, repo_id, with_for_update=True, populate_existing=True
    )
    if locked is None:
        return RefreshResult(False, 0, "Paketquelle nicht mehr vorhanden")
    if locked.url_key != url_key_at_start or locked.public_key != key:
        return await _record_error(
            session, repo_id, "Die Paketquelle wurde während des Abrufs geändert", now
        )
    stored = locked.index_generated_at
    if stored is not None and stored <= now + FUTURE_TOLERANCE and generated < stored:
        return await _record_error(
            session,
            repo_id,
            "Paketquelle liefert einen älteren Stand als zuletzt – abgewiesen",
            now,
        )
    locked.index_json = raw.decode("utf-8")
    locked.index_sha256 = hashlib.sha256(raw).hexdigest()
    locked.index_generated_at = generated
    locked.index_verified = verified
    locked.last_checked_at = now
    locked.last_ok_at = now
    locked.last_error = None
    await session.flush()
    log.info(
        "plugin_repository_refreshed",
        repository=repo_id,
        plugins=len(index.plugins),
        verified=verified,
    )
    return RefreshResult(True, len(index.plugins), None)


# ---------------------------------------------------------------- catalog


class CatalogVersionOut(BaseModel):
    version: str
    released_at: UtcDateTime | None
    changelog: str | None
    compatible: bool
    yanked: bool


class CatalogEntryOut(BaseModel):
    repository_id: int
    repository_name: str
    plugin_id: str
    name: str
    publisher: str | None
    publisher_verified: bool
    description: str
    categories: list[str]
    license: str | None
    homepage: str | None
    icon_url: str | None
    latest_version: str
    latest_compatible: str | None
    installed_version: str | None
    update_available: bool
    image_plugin: bool
    versions: list[CatalogVersionOut]


class CatalogOut(BaseModel):
    categories: list[str]
    entries: list[CatalogEntryOut]


#: parsed stored indexes by ``index_sha256`` (an index is up to 5 MB; the admin list, the
#: catalog and every upload/install read all of them). Parsed objects are never mutated.
_PARSED: dict[str, RepositoryIndex] = {}
_PARSED_MAX: Final = 32


def stored_index(repo: TiqoraPluginRepository) -> RepositoryIndex:
    """The repository's stored index, parsed once per content hash. Raises IndexFormatError."""
    raw = repo.index_json or ""
    sha = repo.index_sha256
    if sha and sha in _PARSED:
        return _PARSED[sha]
    index = parse_index(raw.encode("utf-8"))
    if sha and hashlib.sha256(raw.encode("utf-8")).hexdigest() == sha:
        if len(_PARSED) >= _PARSED_MAX:
            _PARSED.pop(next(iter(_PARSED)))
        _PARSED[sha] = index
    return index


def _parse_or_none(repo: TiqoraPluginRepository) -> RepositoryIndex | None:
    if not repo.index_json:
        return None
    try:
        return stored_index(repo)
    except IndexFormatError as exc:
        log.warning("plugin_repository_index_unreadable", repository=repo.id, error=str(exc)[:200])
        return None


def _version_key(ver: IndexVersion) -> Version:
    try:
        return Version(ver.version)
    except InvalidVersion:  # parse_index validated it; defensive
        return Version("0")


def update_binding_ok(
    installed: TiqoraPlugin, repo: TiqoraPluginRepository, verified_key_ids: set[str]
) -> bool:
    """F10: an update comes from the plugin's own repository, or from another one only when it
    vouches (verified index) for the key that signed the installed version."""
    if installed.source == "image":
        return True  # information only ("bitte das Image neu bauen")
    if installed.repository_id is not None and installed.repository_id == repo.id:
        return True
    return bool(installed.key_id and installed.key_id in verified_key_ids)


async def installed_publishers(
    session: AsyncSession, installed: dict[str, TiqoraPlugin]
) -> dict[str, str | None]:
    """``publisher_id`` of the installed manifests; a plugin missing here has an unknown one.

    Runtime plugins: from the stored package report. Image plugins: from the discovered manifest.
    The package lookup loads the keys first and uses an IN list (no Znuny join involved).
    """
    from tiqora.plugins.discovery import discovered_plugins

    out: dict[str, str | None] = {}
    shas = sorted({r.package_sha256 for r in installed.values() if r.package_sha256})
    if shas:
        rows = (
            await session.execute(
                select(TiqoraPluginPackage.sha256, TiqoraPluginPackage.report).where(
                    TiqoraPluginPackage.sha256.in_(shas)
                )
            )
        ).all()
        reports: dict[str, str | None] = {}
        for sha, raw in rows:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and "publisher_id" in data:
                value = data["publisher_id"]
                reports[sha] = value if isinstance(value, str) else None
        for plugin_id, row in installed.items():
            if row.package_sha256 in reports:
                out[plugin_id] = reports[row.package_sha256]
    for found in discovered_plugins():
        baked = installed.get(found.plugin_id)
        if baked is not None and baked.source == "image" and found.manifest is not None:
            out.setdefault(found.plugin_id, found.manifest.publisher_id)
    return out


def update_target(
    plugin: IndexPlugin,
    repo: TiqoraPluginRepository,
    installed: TiqoraPlugin,
    publishers_of: dict[str, str | None],
    verified_key_ids: set[str],
    core: Version | None,
    sdk: str,
) -> IndexVersion | None:
    """The version of ``plugin`` that counts as an update for ``installed``, or None (F10).

    The index entry must name the publisher of the installed manifest (when that is known), the
    repository must be the plugin's own or vouch for the key that signed the installed version,
    and the release must be compatible, not yanked and newer.
    """
    if installed.id in publishers_of and plugin.publisher_id != publishers_of[installed.id]:
        return None
    if not update_binding_ok(installed, repo, verified_key_ids):
        return None
    best = newest_compatible(plugin, core=core, sdk=sdk)
    if best is None:
        return None
    try:
        newer = Version(best.version) > Version(installed.version)
    except InvalidVersion:
        return None
    return best if newer else None


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    repository_id: int
    repository_name: str
    #: baked into the image: information only ("bitte das Image neu bauen")
    image_plugin: bool


async def available_updates(session: AsyncSession, *, settings: Settings) -> dict[str, UpdateInfo]:
    """Installed plugin id -> the newest update the enabled repositories offer (no network)."""
    from tiqora.plugins import store
    from tiqora.plugins.compat import release_core_version
    from tiqora.plugins.runtime import image_plugin_ids
    from tiqora_sdk import SDK_VERSION

    installed = await store.list_plugin_rows(session)
    if not installed:
        return {}
    repos = (
        (
            await session.execute(
                select(TiqoraPluginRepository)
                .where(
                    TiqoraPluginRepository.enabled.is_(True),
                    TiqoraPluginRepository.index_json.is_not(None),
                )
                .order_by(TiqoraPluginRepository.id)
            )
        )
        .scalars()
        .all()
    )
    images = image_plugin_ids(settings)
    publishers_of = await installed_publishers(session, installed)
    core = release_core_version()
    best: dict[str, tuple[Version, UpdateInfo]] = {}
    for repo in repos:
        index = _parse_or_none(repo)
        if index is None:
            continue
        key_ids = (
            {k.key_id for p in index.publishers for k in p.keys if not k.revoked}
            if repo.index_verified
            else set()
        )
        for plugin in index.plugins:
            row = installed.get(plugin.id)
            if row is None:
                continue
            target = update_target(plugin, repo, row, publishers_of, key_ids, core, SDK_VERSION)
            if target is None:
                continue
            parsed = Version(target.version)
            known = best.get(plugin.id)
            if known is None or parsed > known[0]:
                info = UpdateInfo(
                    version=target.version,
                    repository_id=repo.id,
                    repository_name=repo.name,
                    image_plugin=row.source == "image" or plugin.id in images,
                )
                best[plugin.id] = (parsed, info)
    return {plugin_id: info for plugin_id, (_, info) in best.items()}


# ---------------------------------------------------------------- download


@dataclass(frozen=True)
class DownloadResult:
    package_sha256: str
    installable: bool


def find_index_version(
    repo: TiqoraPluginRepository, plugin_id: str, version: str
) -> IndexVersion | None:
    """The release ``plugin_id`` ``version`` in the repository's stored index, or None."""
    index = _parse_or_none(repo)
    if index is None:
        return None
    for plugin in index.plugins:
        if plugin.id == plugin_id:
            return next((v for v in plugin.versions if v.version == version), None)
    return None


NOT_IN_REPOSITORY: Final = "Version nicht in der Paketquelle"
YANKED_MESSAGE: Final = "Diese Version wurde von der Paketquelle zurückgezogen"
CHECKSUM_MESSAGE: Final = "Prüfsumme stimmt nicht mit der Paketquelle überein"
SIZE_MESSAGE: Final = "Größe stimmt nicht mit der Paketquelle überein"
ENTRY_MESSAGE: Final = (
    "Das Paket passt nicht zum Eintrag der Paketquelle (Erweiterung oder Version)"
)


async def download_package(
    factory: async_sessionmaker[AsyncSession],
    *,
    repository_id: int,
    plugin_id: str,
    version: str,
    settings: Settings,
    uploaded_by: int | None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> DownloadResult:
    """Fetch one release of the repository's stored index and store it as a package (D8).

    Only the source repository's publisher keys count (F10). The download must match the index
    entry's sha256 and size exactly; nothing is stored otherwise. Every refusal is a German
    ``FetchError``. No transaction is open during the network access.
    """
    from tiqora.plugins import packages_store
    from tiqora.plugins.inspect import inspect_read_package, installed_state, short_id_owners
    from tiqora.plugins.package import PackageError, read_tqplugin
    from tiqora.plugins.runtime import runtime_dir_problem

    async with factory() as session:
        repo = await session.get(TiqoraPluginRepository, repository_id)
        if repo is None:
            raise FetchError("Paketquelle nicht gefunden", not_found=True)
        if not repo.enabled:
            raise FetchError("Die Paketquelle ist abgeschaltet")
        entry = find_index_version(repo, plugin_id, version)
        if entry is None:
            raise FetchError(NOT_IN_REPOSITORY, not_found=True)
        if entry.yanked:
            raise FetchError(YANKED_MESSAGE)
        repo_name, repo_url, verified = repo.name, repo.url, repo.index_verified
        try:
            package_url = resolve_package_url(repo_url, entry.url)
        except IndexFormatError as exc:
            raise FetchError(str(exc)) from None
        problem = package_url_problem(repo_url, package_url, verified=verified)
        if problem is not None:
            raise FetchError(problem)
        ring, names = await build_key_ring_named(session, settings)
        # F10: a repository download is judged by its own publisher keys only
        ring, names = ring_for_source(ring, names, source="repository", repository_id=repository_id)
        installed, images = await installed_state(session, settings)
        owners = await short_id_owners(session)
        await session.rollback()
    limit = settings.plugins_max_package_bytes
    if entry.size > limit:
        raise FetchError(f"Das Paket ist größer als {_megabytes(limit)}")
    data = await fetch_bytes(
        package_url, max_bytes=limit, settings=settings, transport=transport, resolver=resolver
    )
    if hashlib.sha256(data).hexdigest() != entry.sha256.lower():
        raise FetchError(CHECKSUM_MESSAGE)
    if len(data) != entry.size:
        raise FetchError(SIZE_MESSAGE)

    def inspect() -> tuple[PackageReport, str]:
        pkg = read_tqplugin(data, max_bytes=limit)
        report = inspect_read_package(
            pkg,
            settings=settings,
            ring=ring,
            installed=installed,
            image_plugin_ids=images,
            short_id_owners=owners,
        )
        return report, pkg.manifest_text

    try:
        report, manifest_text = await asyncio.to_thread(inspect)
    except PackageError as exc:
        raise FetchError(str(exc)) from None
    if report.plugin_id != plugin_id or report.version != version:
        raise FetchError(ENTRY_MESSAGE)
    report = with_repository_signer(report, ring, names)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    async with factory() as session:
        try:
            await packages_store.store_package(
                session,
                data=data,
                report=report,
                manifest_text=manifest_text,
                source="repository",
                repository_id=repository_id,
                uploaded_by=uploaded_by,
                now=now,
            )
            await session.commit()
        except packages_store.PackageTooLargeForDatabase as exc:
            await session.rollback()
            raise FetchError(str(exc)) from None
    log.info(
        "plugin_package_downloaded",
        repository=repository_id,
        repository_name=repo_name,
        plugin=plugin_id,
        version=version,
        trust=report.trust.value,
    )
    return DownloadResult(
        package_sha256=report.sha256,
        installable=report.installable and runtime_dir_problem(settings) is None,
    )


async def catalog(
    session: AsyncSession,
    *,
    settings: Settings,
    q: str | None = None,
    category: str | None = None,
    repository_id: int | None = None,
) -> CatalogOut:
    """The plugins offered by all enabled repositories, from the stored indexes (no network)."""
    from tiqora.plugins import store
    from tiqora.plugins.compat import release_core_version
    from tiqora.plugins.runtime import image_plugin_ids

    stmt = select(TiqoraPluginRepository).where(
        TiqoraPluginRepository.enabled.is_(True), TiqoraPluginRepository.index_json.is_not(None)
    )
    if repository_id is not None:
        stmt = stmt.where(TiqoraPluginRepository.id == repository_id)
    repos = (await session.execute(stmt.order_by(TiqoraPluginRepository.id))).scalars().all()
    installed = await store.list_plugin_rows(session)
    images = image_plugin_ids(settings)
    publishers_of = await installed_publishers(session, installed)
    # the whole ring (not only the filtered repository): F10 conflicts span repositories
    ring = await build_key_ring(session, settings)
    core = release_core_version()
    from tiqora_sdk import SDK_VERSION

    needle = (q or "").strip().casefold()
    wanted_category = (category or "").strip().casefold()
    categories: dict[str, str] = {}
    entries: list[CatalogEntryOut] = []
    for repo in repos:
        index = _parse_or_none(repo)
        if index is None:
            continue
        publishers = {p.id: p for p in index.publishers}
        key_ids = {k.key_id for p in index.publishers for k in p.keys if not k.revoked}
        for plugin in index.plugins:
            entry = _entry(
                repo,
                plugin,
                publishers,
                installed.get(plugin.id),
                publishers_of,
                plugin.id in images,
                key_ids if repo.index_verified else set(),
                core,
                SDK_VERSION,
                ring,
            )
            if entry is None:
                continue
            for cat in entry.categories:
                categories.setdefault(cat.casefold(), cat)
            if wanted_category and wanted_category not in {c.casefold() for c in entry.categories}:
                continue
            if (
                needle
                and needle
                not in " ".join(
                    [entry.plugin_id, entry.name, entry.description, entry.publisher or ""]
                ).casefold()
            ):
                continue
            entries.append(entry)
    entries.sort(key=lambda e: (e.name.casefold(), e.repository_id, e.plugin_id))
    return CatalogOut(categories=sorted(categories.values(), key=str.casefold), entries=entries)


def _entry(
    repo: TiqoraPluginRepository,
    plugin: IndexPlugin,
    publishers: dict[str, IndexPublisher],
    installed: TiqoraPlugin | None,
    publishers_of: dict[str, str | None],
    image_plugin: bool,
    verified_key_ids: set[str],
    core: Version | None,
    sdk: str,
    ring: KeyRing,
) -> CatalogEntryOut | None:
    ordered = sorted(plugin.versions, key=_version_key, reverse=True)
    if not ordered:
        return None
    latest = next((v for v in ordered if not v.yanked), ordered[0])
    best = newest_compatible(plugin, core=core, sdk=sdk)
    publisher = publishers.get(plugin.publisher_id) if plugin.publisher_id else None
    # the same rules as the install path (revoked keys, F10 conflicts): resolve_trust
    publisher_verified = bool(
        repo.index_verified
        and publisher is not None
        and plugin.id.startswith(publisher.id + ".")
        and publisher_verified_in(ring, publisher.id, repo.id)
    )
    installed_version = installed.version if installed is not None else None
    update = (
        installed is not None
        and update_target(plugin, repo, installed, publishers_of, verified_key_ids, core, sdk)
        is not None
    )
    return CatalogEntryOut(
        repository_id=repo.id,
        repository_name=repo.name,
        plugin_id=plugin.id,
        name=plugin.name,
        publisher=publisher.name if publisher is not None else None,
        publisher_verified=publisher_verified,
        description=plugin.description,
        categories=list(plugin.categories),
        license=plugin.license,
        homepage=plugin.homepage,
        icon_url=None,  # F14: icons are not loaded in this phase
        latest_version=latest.version,
        latest_compatible=best.version if best is not None else None,
        installed_version=installed_version,
        update_available=update,
        image_plugin=image_plugin,
        versions=[
            CatalogVersionOut(
                version=v.version,
                released_at=v.released_at,
                changelog=v.changelog,
                compatible=version_compatible(v, core=core, sdk=sdk),
                yanked=v.yanked,
            )
            for v in ordered
        ],
    )


# ---------------------------------------------------------------- daemon tick


async def run_repositories_tick(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> dict[str, int]:
    """Daemon ``plugin_repositories``: env sync, refresh every enabled repository, purge old
    packages. No enabled repository means no network access at all.

    Runtime directories are NOT collected here (F8): only a booting process knows which ones
    other processes still use.
    """
    from tiqora.plugins import packages_store

    started = time.monotonic()
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    async with factory() as session:
        await sync_env_repositories(session, settings, now)
        await session.commit()
        ids = list(
            (
                await session.execute(
                    select(TiqoraPluginRepository.id)
                    .where(TiqoraPluginRepository.enabled.is_(True))
                    .order_by(TiqoraPluginRepository.id)
                )
            ).scalars()
        )
    failed = 0
    for repo_id in ids:
        if time.monotonic() - started > TICK_BUDGET_SECONDS:
            log.warning("plugin_repositories_tick_budget_spent", skipped_from=repo_id)
            failed += 1
            continue
        try:
            async with factory() as session:
                repo = await session.get(TiqoraPluginRepository, repo_id)
                if repo is None or not repo.enabled:
                    continue
                result = await refresh_repository(
                    session,
                    repo,
                    settings=settings,
                    now=now,
                    transport=transport,
                    resolver=resolver,
                )
                await session.commit()
            if not result.ok:
                failed += 1
        except Exception:
            log.exception("plugin_repository_refresh_crashed", repository=repo_id)
            failed += 1
    async with factory() as session:
        await packages_store.purge_stale_packages(
            session, older_than=now - timedelta(hours=STALE_PACKAGE_HOURS)
        )
        await session.commit()
        offered = await available_updates(session, settings=settings)
        await session.rollback()
    updates = len(offered)
    return {"repositories": len(ids), "failed": failed, "updates_available": updates}
