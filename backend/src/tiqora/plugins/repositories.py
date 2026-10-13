"""Package sources: configuration, environment sync and the key ring from verified indexes (D8).

Fetching and refresh follow in later tasks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPluginRepository
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.repo_index import IndexFormatError, parse_index, publisher_keys
from tiqora.plugins.signing import (
    OFFICIAL_KEYS,
    KeyRing,
    PublisherKey,
    SignatureError,
    TrustLevel,
    local_keys,
    parse_public_key,
)

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
            index = parse_index((row.index_json or "").encode())
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
            log.warning("plugin_env_repository_shadowed_by_admin", url=repo.url)
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
