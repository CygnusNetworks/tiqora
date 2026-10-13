"""Plugin migrations (D6): one Alembic environment per plugin, guarded statements.

A plugin ships ``<package>/migrations/*.py`` (normal Alembic revision files, first revision
``down_revision = None``). They run in Tiqora's shared environment
(``tiqora/plugins/alembic_env``) against the plugin's own version table
``tqp_<short_id>_alembic_version``; the core graph (``tiqora_alembic_version``) never sees them.

``StatementGuard`` is a protection against accidental changes (a careless migration touching a
Znuny table), not a security boundary: an admin who installs a plugin trusts its code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from tiqora.plugins.discovery import DiscoveredPlugin

_ENV_DIR = Path(__file__).parent / "alembic_env"
_MAX_REVISION_ID = 32  # width of the version_num column Alembic creates


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
    r"\binto\b|\b(?:setval|set_config|lo_import|lo_export|lo_unlink|load_file|"
    r"pg_terminate_backend|pg_cancel_backend)\b|\bdblink\w*"
)
_WITH_DENY = re.compile(r"\b(?:insert|update|delete|merge)\b")
_JOIN_SPLIT = re.compile(
    r"\s*,\s*|\s+(?:(?:inner|left|right|full|cross|natural|outer)\s+)*(?:straight_)?join\s+"
)
_REFERENCE_PATTERNS = (
    re.compile(rf"\breferences\s+({_NAME})"),
    re.compile(rf"\bwith table\s+({_NAME})"),
    re.compile(rf"\bpartition of\s+({_NAME})"),
    re.compile(rf"\battach partition\s+({_NAME})"),
    re.compile(rf"\blike\s+({_NAME})"),
)


def _normalise(sql: str, *, backslash_escapes: bool) -> str | None:
    """Strip comments and string contents, drop identifier quoting, lower-case.

    Returns ``None`` when the statement cannot be read safely (unterminated string/comment,
    executable ``/*! … */`` comment). Only comments both dialects agree on are removed: anything
    else stays visible, so the checks see a superset of what the server executes.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            i += 1
            while True:
                if i >= n:
                    return None
                if backslash_escapes and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            out.append("''")
        elif ch in ('"', "`"):
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
            out.append("".join(buf))
            i = j + 1
        elif sql.startswith("/*", i):
            if sql.startswith("/*!", i) or sql.startswith("/*M!", i):
                return None
            end = sql.find("*/", i + 2)
            if end < 0:
                return None
            out.append(" ")
            i = end + 2
        elif sql.startswith("--", i) and (i + 2 >= n or sql[i + 2].isspace()):
            end = sql.find("\n", i)
            out.append(" ")
            i = n if end < 0 else end + 1
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
        for backslash in (True, False):  # dialects disagree on ``\'``: both readings must pass
            norm = _normalise(statement, backslash_escapes=backslash)
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
        if not s or ";" in s or re.search(r"\$[a-z_]*\$", s):
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
        if re.match(r"set names\b", s):
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
            bare = re.search(rf"\brename (?!column\b|constraint\b|index\b|key\b)({_NAME})\s*$", s)
            return bare is None or self._ok(bare.group(1))
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
            return names is not None and self._all_ok(names)
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


def plugin_migration_head(plugin: DiscoveredPlugin) -> str | None:
    """The single head revision, ``None`` without migrations; more than one head raises."""
    if migrations_dir(plugin) is None:
        return None
    cfg = plugin_alembic_config(plugin, database_url="")
    heads = _script(cfg).get_heads()
    return heads[0] if heads else None


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
) -> None:
    cfg = plugin_alembic_config(plugin, database_url=database_url)
    script = _script(cfg)
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


def upgrade_plugin(plugin: DiscoveredPlugin, *, database_url: str) -> str | None:
    """Migrate the plugin to its head; returns the head (``None`` without migrations).

    Sync; must run in a thread without a running event loop (the environment uses
    ``asyncio.run``). Every failure is a ``PluginMigrationError`` with a German message.
    """
    head = plugin_migration_head(plugin)
    if head is None:
        return None
    _run(plugin, database_url, lambda cfg: command.upgrade(cfg, "head"))
    return head


def downgrade_plugin_to_base(plugin: DiscoveredPlugin, *, database_url: str) -> None:
    """Undo every plugin revision (CLI only; the UI drops the prefixed tables instead)."""
    if migrations_dir(plugin) is None:
        return
    _run(plugin, database_url, lambda cfg: command.downgrade(cfg, "base"), upward=False)
