"""Plugin migrations (D6): one Alembic environment per plugin, guarded statements.

A plugin ships ``<package>/migrations/*.py`` (normal Alembic revision files, first revision
``down_revision = None``). They run in Tiqora's shared environment
(``tiqora/plugins/alembic_env``) against the plugin's own version table
``tqp_<short_id>_alembic_version``; the core graph (``tiqora_alembic_version``) never sees them.

``StatementGuard`` is a protection against accidental changes (a careless migration touching a
Znuny table), not a security boundary: an admin who installs a plugin trusts its code.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tiqora.plugins.discovery import DiscoveredPlugin

_ENV_DIR = Path(__file__).parent / "alembic_env"
_MAX_REVISION_ID = 32  # width of the version_num column Alembic creates
_MAX_ERROR_LEN = 2000

MIGRATION_PENDING_MESSAGE: Final = (
    "Datenbank-Änderungen der Erweiterung stehen aus oder sind fehlgeschlagen"
)
SAFE_MODE_MIGRATIONS_MESSAGE: Final = (
    "Abgesicherter Modus: Datenbank-Änderungen der Erweiterungen übersprungen"
)


class PluginMigrationError(RuntimeError):
    """A plugin migration was refused or failed; the message is German and user facing."""


# --------------------------------------------------------------------------------------
# Statement guard
# --------------------------------------------------------------------------------------

_NAME = r"[a-z0-9_.$]+"
_SET_VARS = frozenset(
    {
        "names",
        "time zone",
        "time_zone",
        "sql_mode",
        "foreign_key_checks",
        "search_path",
        "lock_timeout",
        "statement_timeout",
    }
)
_SELECT_DENY = re.compile(
    r"\binto\b|\b(?:setval|nextval|get_lock|set_config|lo_import|lo_export|lo_unlink|load_file|"
    r"pg_terminate_backend|pg_cancel_backend)\b|\bdblink\w*"
)
_WITH_DENY = re.compile(r"\b(?:insert|update|delete|merge)\b")
_JOIN_SPLIT = re.compile(
    r"\s*,\s*|\s+(?:(?:inner|left|right|full|cross|natural|outer)\s+)*(?:straight_)?join\s+"
)
# GUARD-STRICT-LEXICON: tokens on which the MariaDB and PostgreSQL lexers disagree (comments,
# escapes, dollar quotes, line ends) are refused anywhere in the statement, also inside literals.
_FORBIDDEN = re.compile(r";|--|#|/\*|\*/|\\|\$[A-Za-z_]*\$|\r")
_PLAIN_CHARS = re.compile(r"[\x20-\x7e\t\n]*")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")
_REFERENCE_PATTERNS = (
    re.compile(rf"\breferences\s+({_NAME})"),
    re.compile(rf"\bwith table\s+({_NAME})"),
    re.compile(rf"\bpartition of\s+({_NAME})"),
    re.compile(rf"\battach partition\s+({_NAME})"),
    re.compile(rf"\blike\s+({_NAME})"),
)


def _strict_lexicon_ok(sql: str) -> bool:
    """True when the statement lexes the same on MariaDB and PostgreSQL.

    No comments, backslashes, dollar quotes or ``\\r`` anywhere; outside ``'...'`` only
    printable ASCII, space, tab and newline (non-ASCII text such as German defaults stays allowed
    inside single-quoted literals).
    """
    if _FORBIDDEN.search(sql):
        return False
    # blank only '...' literals; "..." and `...` stay and must be plain ('' / "" doubling just
    # reads as two adjacent quoted runs, which is the same for this check)
    outside = _QUOTED.sub(lambda m: "''" if m.group(0)[0] == "'" else m.group(0), sql)
    return _PLAIN_CHARS.fullmatch(outside) is not None


def _normalise(sql: str) -> str | None:
    """Blank string contents, drop identifier quoting, lower-case, collapse whitespace.

    Only called after ``_strict_lexicon_ok``: without comments and backslashes, ``'...'`` (with
    ``''`` doubling), ``"..."`` and backticks lex identically on both dialects, so this sees
    what the server parses. Returns ``None`` for an unterminated quote.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            buf: list[str] = []
            while True:
                if j >= n:
                    return None
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        buf.append(ch)
                        j += 2
                        continue
                    break
                buf.append(sql[j])
                j += 1
            out.append("''" if ch == "'" else "".join(buf))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return re.sub(r"\s+", " ", "".join(out).lower()).strip()


class StatementGuard:
    """Allows only statements that stay inside ``tqp_<short_id>_`` (see D6)."""

    def __init__(self, short_id: str) -> None:
        self.short_id = short_id
        self.prefix = f"tqp_{short_id}_"

    # SQLAlchemy ``before_cursor_execute`` signature
    def __call__(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.check(statement)

    def check(self, statement: str) -> None:
        """Raise ``PluginMigrationError`` unless the statement is allowed."""
        if not _strict_lexicon_ok(statement):
            raise self._refuse(statement)
        norm = _normalise(statement)
        if norm is None or not self._allowed(norm):
            raise self._refuse(statement)

    def _refuse(self, statement: str) -> PluginMigrationError:
        shown = re.sub(r"\s+", " ", statement).strip()[:80]
        return PluginMigrationError(
            f"Anweisung in Datenbank-Änderung der Erweiterung nicht erlaubt: {shown}"
        )

    # ---- helpers -----------------------------------------------------------------
    def _ok(self, token: str | None) -> bool:
        if not token:
            return False
        return token.split(".")[-1].startswith(self.prefix)

    def _all_ok(self, tokens: list[str]) -> bool:
        return bool(tokens) and all(self._ok(t) for t in tokens)

    def _table_list(self, segment: str) -> list[str] | None:
        """First identifier of every table reference in a FROM/UPDATE list (None = unparsable)."""
        tokens: list[str] = []
        for piece in _JOIN_SPLIT.split(segment.strip()):
            piece = re.sub(r"^only\s+", "", piece.strip())
            m = re.match(_NAME, piece)
            if m is None:
                return None
            tokens.append(m.group(0))
        return tokens

    def _name_list(self, segment: str) -> list[str] | None:
        tokens = []
        for piece in segment.split(","):
            m = re.match(rf"\s*({_NAME})", piece)
            if m is None:
                return None
            tokens.append(m.group(1))
        return tokens

    # ---- the grammar -------------------------------------------------------------
    def _allowed(self, s: str) -> bool:  # noqa: C901, PLR0911, PLR0912 - flat keyword table
        if not s:
            return False
        word = s.split(" ", 1)[0]
        if word == "show":
            return True
        if word == "select":
            return _SELECT_DENY.search(s) is None
        if word == "with":
            return (
                " select " in f" {s} "
                and _WITH_DENY.search(s) is None
                and _SELECT_DENY.search(s) is None
            )
        if word in ("describe", "desc"):
            m = re.fullmatch(rf"(?:describe|desc) ({_NAME})(?: [a-z0-9_%]+)?", s)
            return m is not None and self._ok(m.group(1))
        if word == "set":
            return self._set_allowed(s)
        if not self._references_ok(s):
            return False
        return self._ddl_dml_allowed(s)

    def _references_ok(self, s: str) -> bool:
        for pattern in _REFERENCE_PATTERNS:
            if not all(self._ok(t) for t in pattern.findall(s)):
                return False
        for body in re.findall(r"\binherits\s*\(([^)]*)\)", s):
            names = self._name_list(body)
            if names is None or not self._all_ok(names):
                return False
        return True

    def _set_allowed(self, s: str) -> bool:
        if re.fullmatch(r"set names (?:[a-z0-9_]+|'')(?: collate (?:[a-z0-9_]+|''))?", s):
            return True
        if re.match(r"set (?:session |local )?time zone\b", s):
            return True
        body = s[4:]
        for part in body.split(","):
            m = re.match(r"\s*(?:session |local )?([a-z_]+)\s*(?:=|:=| to )", part)
            if m is None or m.group(1) not in _SET_VARS:
                return False
        return True

    def _ddl_dml_allowed(self, s: str) -> bool:  # noqa: C901, PLR0911, PLR0912
        if m := re.match(
            rf"create (?:(?:unique|fulltext|spatial) )?index (?:concurrently )?"
            rf"(?:if not exists )?({_NAME}) on (?:only )?({_NAME})",
            s,
        ):
            return self._ok(m.group(1)) and self._ok(m.group(2))
        if m := re.match(r"drop index (?:concurrently )?(?:if exists )?(.+)$", s):
            names_part, _, table_part = m.group(1).partition(" on ")
            names = self._name_list(names_part)
            if names is None or not self._all_ok(names):
                return False
            if table_part:
                table = re.match(_NAME, table_part.strip())
                return table is not None and self._ok(table.group(0))
            return True
        if m := re.match(
            rf"create (?:(?:temporary|unlogged|global temporary) )?table "
            rf"(?:if not exists )?({_NAME})",
            s,
        ):
            return self._ok(m.group(1))
        if m := re.match(rf"alter table (?:if exists )?(?:only )?({_NAME})", s):
            if not self._ok(m.group(1)):
                return False
            for target in re.findall(rf"\brename (?:to|as) ({_NAME})", s):
                if not self._ok(target):
                    return False
            bare = re.findall(
                rf"\brename (?!(?:column|constraint|index|key|to|as)\b)({_NAME})(?= ?(?:,|$))", s
            )
            return all(self._ok(target) for target in bare)
        if m := re.match(r"drop (?:temporary )?table (?:if exists )?(.+)$", s):
            body = re.sub(r"\s+(?:cascade|restrict)$", "", m.group(1))
            names = self._name_list(body)
            return names is not None and self._all_ok(names)
        if m := re.match(r"rename table (.+)$", s):
            names = re.findall(rf"({_NAME}) to ({_NAME})", m.group(1))
            flat = [t for pair in names for t in pair]
            return self._all_ok(flat) and len(names) == m.group(1).count(" to ")
        if m := re.match(rf"comment on (table|column) ({_NAME}) is\b", s):
            parts = m.group(2).split(".")
            if m.group(1) == "column":
                return len(parts) >= 2 and self._ok(parts[-2])
            return self._ok(parts[-1])
        if m := re.match(r"truncate (?:table )?(?:only )?(.+)$", s):
            body = re.split(r"\s+(?:restart|continue) identity|\s+cascade|\s+restrict", m.group(1))[
                0
            ]
            names = self._name_list(body)
            return names is not None and self._all_ok(names)
        if m := re.match(
            rf"insert (?:(?:low_priority|delayed|high_priority|ignore) )*into ({_NAME})", s
        ):
            return self._ok(m.group(1))
        if s.startswith("update "):
            m = re.match(r"update (?:(?:low_priority|ignore) )*(.*?) set\b", s)
            if m is None:
                return False
            tables = self._table_list(m.group(1))
            return tables is not None and self._all_ok(tables)
        if s.startswith("delete "):
            return self._delete_allowed(s)
        if m := re.match(rf"create (?:type|sequence) (?:if not exists )?({_NAME})", s):
            return self._ok(m.group(1))
        if m := re.match(r"drop (?:type|sequence) (?:if exists )?(.+)$", s):
            body = re.sub(r"\s+(?:cascade|restrict)$", "", m.group(1))
            names = self._name_list(body)
            return names is not None and self._all_ok(names)
        return False

    def _delete_allowed(self, s: str) -> bool:
        m = re.match(r"delete (?:(?:low_priority|quick|ignore) )*(.*?)\bfrom (.*)$", s)
        if m is None:
            return False
        targets, rest = m.group(1).strip(), m.group(2)
        if targets:  # MariaDB multi-table form: ``delete t1, t2 from …``
            names = self._name_list(re.sub(r"\.\*", "", targets))
            if names is None or not self._all_ok(names):
                return False  # targets may be aliases, so the FROM list is checked too
        head = re.split(r"\s+(?:using|where|returning|order by|limit)\b", rest)[0]
        tables = self._table_list(head)
        return tables is not None and self._all_ok(tables)


# --------------------------------------------------------------------------------------
# Environment, configuration
# --------------------------------------------------------------------------------------


def migrations_dir(plugin: DiscoveredPlugin) -> Path | None:
    """``<package>/migrations`` when it holds at least one revision file."""
    if plugin.package_dir is None:
        return None
    path = plugin.package_dir / "migrations"
    if path.is_dir() and any(p.suffix == ".py" and p.name != "__init__.py" for p in path.iterdir()):
        return path
    return None


def _short_id(plugin: DiscoveredPlugin) -> str:
    if plugin.manifest is None:
        raise PluginMigrationError("Erweiterung ohne gültiges Manifest kann nicht migriert werden")
    return plugin.manifest.short_id


def plugin_alembic_config(plugin: DiscoveredPlugin, *, database_url: str) -> Config:
    """Alembic ``Config`` (no ini file) for one plugin, using the shared environment."""
    path = migrations_dir(plugin)
    if path is None:
        raise PluginMigrationError("Die Erweiterung hat keine Datenbank-Änderungen")
    short_id = _short_id(plugin)
    cfg = Config()
    cfg.set_main_option("script_location", str(_ENV_DIR))
    cfg.set_main_option("path_separator", ";")
    cfg.set_main_option("version_locations", str(path).replace("%", "%%"))
    cfg.attributes.update(
        {
            "short_id": short_id,
            "plugin_id": plugin.plugin_id,
            "database_url": database_url,
            "version_table": f"tqp_{short_id}_alembic_version",
        }
    )
    return cfg


def _script(cfg: Config) -> ScriptDirectory:
    try:
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        revisions = list(script.walk_revisions())
    except Exception as exc:  # noqa: BLE001 - Alembic raises many types for broken files
        raise PluginMigrationError(
            f"Datenbank-Änderungen der Erweiterung sind ungültig: {type(exc).__name__}: {exc}"[
                :2000
            ]
        ) from exc
    if len(heads) > 1:
        raise PluginMigrationError(
            "Datenbank-Änderungen der Erweiterung haben mehrere Enden: " + ", ".join(sorted(heads))
        )
    for rev in revisions:
        if len(rev.revision) > _MAX_REVISION_ID:
            raise PluginMigrationError(
                f"Revisionskennung „{rev.revision}“ ist länger als {_MAX_REVISION_ID} Zeichen"
            )
    return script


def _head(script: ScriptDirectory) -> str | None:
    heads = script.get_heads()
    return heads[0] if heads else None


def plugin_migration_head(plugin: DiscoveredPlugin) -> str | None:
    """The single head revision, ``None`` without migrations; more than one head raises."""
    if migrations_dir(plugin) is None:
        return None
    return _head(_script(plugin_alembic_config(plugin, database_url="")))


def _failing_revision(script: ScriptDirectory, cfg: Config, *, upward: bool) -> str:
    """Best guess which revision a failed run was applying (from the env's progress notes)."""
    order = [r.revision for r in reversed(list(script.walk_revisions()))]
    done = cfg.attributes.get("last_applied") if cfg.attributes.get("steps_done") else None
    current = cfg.attributes.get("current")
    if not upward:  # downgrade undoes the revision the database is at right now
        return str(done or current or "?")
    at = done if done is not None else current
    if at is None:
        return order[0] if order else "?"
    pos = order.index(at) + 1 if at in order else len(order)
    return order[pos] if pos < len(order) else order[-1]


def _run(
    plugin: DiscoveredPlugin,
    database_url: str,
    step: Callable[[Config], None],
    *,
    upward: bool = True,
) -> str | None:
    """Run one Alembic command; returns the script's head (parsed once per run)."""
    cfg = plugin_alembic_config(plugin, database_url=database_url)
    script = _script(cfg)
    head = _head(script)
    if head is None:
        return None
    try:
        step(cfg)
    except Exception as exc:
        failing = _failing_revision(script, cfg, upward=upward)
        cause: BaseException = exc
        while not isinstance(cause, PluginMigrationError) and cause.__cause__ is not None:
            cause = cause.__cause__
        detail = (
            str(cause)
            if isinstance(cause, PluginMigrationError)
            else f"{type(exc).__name__}: {exc}"
        )
        raise PluginMigrationError(
            f"Datenbank-Änderung „{failing}“ fehlgeschlagen: {detail}"[:2000]
        ) from exc
    return head


def upgrade_plugin(plugin: DiscoveredPlugin, *, database_url: str) -> str | None:
    """Migrate the plugin to its head; returns the head (``None`` without migrations).

    Sync; must run in a thread without a running event loop (the environment uses
    ``asyncio.run``). Every failure is a ``PluginMigrationError`` with a German message.
    """
    if migrations_dir(plugin) is None:
        return None
    return _run(plugin, database_url, lambda cfg: command.upgrade(cfg, "head"))


def downgrade_plugin_to_base(plugin: DiscoveredPlugin, *, database_url: str) -> None:
    """Undo every plugin revision (CLI only; the UI drops the prefixed tables instead)."""
    if migrations_dir(plugin) is None:
        return
    _run(plugin, database_url, lambda cfg: command.downgrade(cfg, "base"), upward=False)


# --------------------------------------------------------------------------------------
# Bookkeeping and boot run (D6 "Boot", F3)
# --------------------------------------------------------------------------------------


@asynccontextmanager
async def plugin_session_factory(
    database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory on a throw-away engine (CLI code runs several ``asyncio.run`` calls)."""
    from tiqora.db.engine import async_url_and_connect_args

    url, connect_args = async_url_and_connect_args(database_url)
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def record_migration(
    factory: async_sessionmaker[AsyncSession],
    plugin_id: str,
    *,
    version: str,
    head: str | None,
    error: str | None,
    now: datetime,
    error_only_for_version: bool = False,
) -> None:
    """Store the outcome on ``tiqora_plugin``.

    ``error_only_for_version``: record a failure only on a row whose ``version`` is the one
    attempted (an install job migrating a not yet installed update must not mark the
    still-installed older version as failed).

    Success sets ``migrated_version``/``migrated_head`` and clears the error; a failure keeps
    ``migrated_version`` (the version that last migrated fully) and only sets the error.
    """
    from tiqora.db.tiqora.models import TiqoraPlugin

    if error is None:
        values: dict[str, Any] = {
            "migrated_version": version[:64],
            "migrated_head": head[:64] if head else None,
            "migrated_at": now,
            "migration_error": None,
        }
    else:
        values = {"migration_error": error[:_MAX_ERROR_LEN]}
    stmt = update(TiqoraPlugin).where(TiqoraPlugin.id == plugin_id)
    if error is not None and error_only_for_version:
        stmt = stmt.where(TiqoraPlugin.version == version[:64])
    async with factory() as session:
        await session.execute(stmt.values(values))
        await session.commit()


def _child_env(database_url: str) -> dict[str, str]:
    """Environment of a migration subprocess: same import path, same database."""
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    # runtime plugin site directories are on this process's sys.path, not in PYTHONPATH
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return env


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run_one_in_subprocess(plugin_id: str, *, database_url: str, timeout: int) -> str | None:
    """``python -m tiqora.main plugins migrate <id>``; ``None`` = ok, else the German error."""
    cmd = [sys.executable, "-m", "tiqora.main", "plugins", "migrate", plugin_id]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, plugin id is not shell-interpreted
            cmd,
            env=_child_env(database_url),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Datenbank-Änderung dauerte länger als {timeout} Sekunden und wurde abgebrochen"
    except OSError as exc:
        return f"Datenbank-Änderung konnte nicht gestartet werden: {exc}"
    if done.returncode == 0:
        return None
    detail = _last_line(done.stderr) or _last_line(done.stdout)
    # the CLI prints its one-line German error last; anything else (traceback, sys.exit text) is
    # not that error and gets the generic prefix
    if done.returncode == 1 and detail.startswith(("Datenbank-Änderung", "Fehler:")):
        return detail[:_MAX_ERROR_LEN]
    return (
        f"Datenbank-Änderung abgebrochen (Exit-Code {done.returncode})"
        + (f": {detail}" if detail else "")
    )[:_MAX_ERROR_LEN]


def run_boot_migrations(
    *,
    database_url: str,
    plugins: Sequence[DiscoveredPlugin],
    rows: Mapping[str, tuple[str | None, str | None]],
) -> dict[str, str | None]:
    """Migrate every plugin that needs it; returns ``plugin_id -> error`` (``None`` = ok).

    Sync, never raises. ``rows`` maps ``plugin_id -> (migrated_version, migration_error)``; an
    error present here belongs to the current version and is not retried (only the install
    job or "Erneut versuchen" does). Skipped entirely in safe mode and for ids in
    ``TIQORA_PLUGINS_DISABLED``; incompatible or invalid plugins are not migrated. Each plugin
    runs in its own subprocess with a timeout, so ``SystemExit``, a crash or a hang becomes a
    recorded error and never stops the caller.
    """
    from tiqora.config import get_settings
    from tiqora.plugins.compat import check_compat, release_core_version

    settings = get_settings()
    if settings.plugins_safe_mode:
        return {}
    core = release_core_version()
    results: dict[str, str | None] = {}
    for plugin in plugins:
        try:
            manifest = plugin.manifest
            if manifest is None or plugin.problem is not None:
                continue
            if plugin.plugin_id in settings.plugins_disabled_ids:
                continue
            if migrations_dir(plugin) is None or check_compat(manifest, core=core) is not None:
                continue
            migrated, error = rows.get(plugin.plugin_id, (None, None))
            if error or migrated == manifest.version:
                continue
            results[plugin.plugin_id] = _run_one_in_subprocess(
                plugin.plugin_id,
                database_url=database_url,
                timeout=settings.plugins_migrate_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - one plugin must never stop the others
            results[plugin.plugin_id] = f"Datenbank-Änderung nicht ausgeführt: {exc}"[
                :_MAX_ERROR_LEN
            ]
    return results
