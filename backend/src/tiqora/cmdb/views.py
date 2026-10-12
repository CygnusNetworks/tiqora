"""The two add-on views the Znuny add-on (and other readers) select from.

``tiqora_v1_itsm_ci`` lists CIs, ``tiqora_v1_itsm_ci_links`` their links (CI to CI rows appear
twice, once per end). Plain SQL valid on MariaDB and PostgreSQL over ``tiqora_*`` tables only;
``{INT}`` is the dialect's integer cast target. The migration carries the same literals
(migrations import no app code) and a test compares them.

Later migrations that change a column of ``tiqora_cmdb_ci``, ``tiqora_cmdb_class``,
``tiqora_cmdb_catalog`` or ``tiqora_itsm_link`` must drop and recreate both views around it.

``CREATE VIEW`` needs a privilege the DB role may lack (MariaDB 1142, PostgreSQL ``42501``).
Every statement therefore runs in a SAVEPOINT; on a privilege error the savepoint is rolled
back, a clear message is logged and :func:`create_cmdb_views` returns ``False``.
"""

from __future__ import annotations

from typing import Final

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

VIEW_CI: Final = "tiqora_v1_itsm_ci"
VIEW_CI_LINKS: Final = "tiqora_v1_itsm_ci_links"

VIEW_DDL: Final[dict[str, str]] = {
    VIEW_CI: (
        "CREATE VIEW tiqora_v1_itsm_ci AS "
        "SELECT ci.id AS ci_id, ci.number AS number, ci.name AS name, "
        "cl.id AS class_id, cl.class_key AS class_key, cl.name AS class_name, "
        "cl.group_id AS class_group_id, "
        "dep.name AS deployment_state, dep.functionality AS deployment_functionality, "
        "inc.name AS incident_state, inc.functionality AS incident_functionality, "
        "ci.changed_at AS changed, cl.portal_visible AS class_portal_visible "
        "FROM tiqora_cmdb_ci ci "
        "JOIN tiqora_cmdb_class cl ON cl.id = ci.class_id "
        "JOIN tiqora_cmdb_catalog dep ON dep.id = ci.deployment_state_id "
        "JOIN tiqora_cmdb_catalog inc ON inc.id = ci.cur_incident_state_id"
    ),
    VIEW_CI_LINKS: (
        "CREATE VIEW tiqora_v1_itsm_ci_links AS "
        "SELECT CAST(l.source_key AS {INT}) AS ci_id, l.target_kind AS object_kind, "
        "l.target_key AS object_key, l.link_type AS link_type, 'source' AS direction, "
        "l.created_at AS created "
        "FROM tiqora_itsm_link l WHERE l.source_kind = 'ci' "
        "UNION ALL "
        "SELECT CAST(l.target_key AS {INT}), l.source_kind, l.source_key, l.link_type, "
        "'target', l.created_at "
        "FROM tiqora_itsm_link l WHERE l.target_kind = 'ci'"
    ),
}

_DROP_ORDER: Final = (VIEW_CI_LINKS, VIEW_CI)
_MARIADB_PRIVILEGE_CODES: Final = frozenset({1142, 1044, 1227})
_PG_PRIVILEGE_STATE: Final = "42501"


def _int_type(dialect_name: str) -> str:
    return "SIGNED" if dialect_name in ("mysql", "mariadb") else "INTEGER"


def view_statements(dialect_name: str) -> dict[str, str]:
    """The ``CREATE VIEW`` statements for one dialect (name -> SQL)."""
    return {n: sql.replace("{INT}", _int_type(dialect_name)) for n, sql in VIEW_DDL.items()}


def is_privilege_error(exc: BaseException) -> bool:
    """MariaDB 1142/1044/1227 or PostgreSQL ``42501`` (insufficient privilege)."""
    orig = getattr(exc, "orig", exc)
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int) and args[0] in _MARIADB_PRIVILEGE_CODES:
        return True
    return any(getattr(orig, a, None) == _PG_PRIVILEGE_STATE for a in ("sqlstate", "pgcode"))


def drop_cmdb_views(sync_conn: Connection) -> None:
    """``DROP VIEW IF EXISTS`` for both views (links view first). Raises on any error."""
    for name in _DROP_ORDER:
        sync_conn.execute(text(f"DROP VIEW IF EXISTS {name}"))


def create_cmdb_views(sync_conn: Connection) -> bool:
    """(Re)create both views; ``True`` when they exist afterwards.

    Idempotent (drops first). A privilege error rolls back to the savepoint, logs
    ``CREATE VIEW-Recht fehlt`` and returns ``False``; anything else is re-raised.
    """
    dialect_name = sync_conn.dialect.name
    statements = view_statements(dialect_name)
    # MariaDB commits implicitly on DDL, which destroys any savepoint (RELEASE would fail with
    # 1305); a failed DDL statement leaves no transaction state to roll back there anyway.
    use_savepoint = dialect_name not in ("mysql", "mariadb")
    for name in (VIEW_CI, VIEW_CI_LINKS):
        savepoint = sync_conn.begin_nested() if use_savepoint else None
        try:
            sync_conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            sync_conn.execute(text(statements[name]))
        except DBAPIError as exc:
            if savepoint is not None:
                savepoint.rollback()
            if not is_privilege_error(exc):
                raise
            logger.warning(
                "cmdb_views_skipped",
                view=name,
                message="CREATE VIEW-Recht fehlt: Add-on-Sichten wurden nicht angelegt",
            )
            return False
        else:
            if savepoint is not None:
                savepoint.commit()
    return True


async def views_present(session: AsyncSession) -> bool:
    """Both views exist and can be selected from."""
    for name in (VIEW_CI, VIEW_CI_LINKS):
        try:
            async with session.begin_nested():
                await session.execute(text(f"SELECT 1 FROM {name} LIMIT 1"))
        except DBAPIError:
            return False
    return True


__all__ = [
    "VIEW_CI",
    "VIEW_CI_LINKS",
    "VIEW_DDL",
    "create_cmdb_views",
    "drop_cmdb_views",
    "is_privilege_error",
    "view_statements",
    "views_present",
]
