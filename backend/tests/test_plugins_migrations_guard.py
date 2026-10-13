"""StatementGuard (D6): pure string checks, no database."""

from __future__ import annotations

import pytest

from tiqora.plugins.migrations import PluginMigrationError, StatementGuard

G = StatementGuard("tables")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE tqp_tables_note (id INTEGER NOT NULL, PRIMARY KEY (id))",
        'CREATE TABLE "tqp_tables_note" (id SERIAL)',
        "CREATE TABLE `tqp_tables_note` (id INT)",
        "CREATE TABLE public.tqp_tables_note (id INT)",
        "CREATE INDEX tqp_tables_note_body ON tqp_tables_note (body)",
        "DROP INDEX tqp_tables_note_body ON tqp_tables_note",
        "ALTER TABLE tqp_tables_note ADD COLUMN x INTEGER",
        "ALTER TABLE tqp_tables_note RENAME a TO b",
        "ALTER TABLE tqp_tables_note RENAME TO tqp_tables_memo",
        "INSERT INTO tqp_tables_alembic_version (version_num) VALUES ('a')",
        "UPDATE tqp_tables_alembic_version SET version_num='b'",
        "DELETE FROM tqp_tables_note",
        "DELETE FROM tqp_tables_note WHERE id IN (SELECT id FROM ticket)",
        "SELECT * FROM ticket",
        "SELECT 'it''s fine' FROM ticket",
        "SHOW CREATE TABLE ticket",
        "DESCRIBE tqp_tables_alembic_version",
        "SET NAMES utf8mb4",
        "SET SESSION sql_mode = ''",
        "SET search_path TO tqp_tables_x",
        "WITH c AS (SELECT 1) SELECT * FROM c",
        "CREATE TYPE tqp_tables_kind AS ENUM ('a')",
        "CREATE TABLE tqp_tables_b (a_id INTEGER REFERENCES tqp_tables_note (id))",
        "DROP TABLE IF EXISTS tqp_tables_note, tqp_tables_b",
        "COMMENT ON COLUMN tqp_tables_note.body IS 'x'",
        "SET NAMES 'utf8mb4'",
        # GUARD-STRICT-LEXICON: non-ASCII stays allowed inside '...' (German defaults)
        "ALTER TABLE tqp_tables_note ADD COLUMN size VARCHAR(20) DEFAULT 'Größe'",
        "INSERT INTO tqp_tables_note (body) VALUES ('Größe\tüber\nzwei Zeilen')",
        "UPDATE tqp_tables_note SET body = 'ÄÖÜ ß €' WHERE id = 1",
        "CREATE TABLE tqp_tables_note (\n\tid INTEGER NOT NULL,\n\tPRIMARY KEY (id)\n)",
        "SELECT pg_catalog.pg_class.relname FROM pg_catalog.pg_class WHERE relkind = $1::VARCHAR",
    ],
)
def test_guard_allows(sql: str) -> None:
    G.check(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE ticket_x (id INT)",
        "CREATE TABLE tiqora_x (id INT)",
        "ALTER TABLE ticket ADD COLUMN x INT",
        "DROP TABLE users",
        "DROP TABLE tqp_tables_note, ticket",
        "INSERT INTO ticket_history (id) VALUES (1)",
        "UPDATE ticket SET title='x'",
        "DELETE FROM article",
        "TRUNCATE ticket",
        "CREATE TABLE tqp_tables_c (t INTEGER REFERENCES ticket (id))",
        "RENAME TABLE tqp_tables_note TO ticket",
        "ALTER TABLE tqp_tables_note RENAME TO ticket",
        "CREATE INDEX tqp_tables_i ON ticket (tn)",
        "CREATE TABLE tqp_other_x (id INT)",
        "GRANT ALL ON ticket TO x",
        "CREATE PROCEDURE p() BEGIN END",
        "/* tqp_tables_ */ DROP TABLE ticket",
        # F6 cases
        "DESCRIBE ticket",
        "SELECT 1; DROP TABLE ticket",
        "DELETE FROM tqp_tables_note; DELETE FROM ticket",
        "WITH x AS (DELETE FROM ticket RETURNING id) SELECT * FROM x",
        "WITH x AS (SELECT 1) INSERT INTO ticket SELECT * FROM x",
        "SELECT id INTO OUTFILE '/tmp/x' FROM ticket",
        "SET ROLE postgres",
        "SET GLOBAL sql_mode = ''",
        "SET session_replication_role = replica",
        "SET sql_mode = '', GLOBAL max_connections = 1",
        "UPDATE tqp_tables_note, ticket SET tqp_tables_note.a = 1",
        "UPDATE tqp_tables_note JOIN ticket ON 1 = 1 SET ticket.title = 'x'",
        "DELETE tqp_tables_note, ticket FROM tqp_tables_note JOIN ticket",
        "DELETE FROM ticket USING ticket JOIN tqp_tables_note",
        "SELECT setval('ticket_id_seq', 1)",
        "/*! DROP TABLE ticket */",
        "SELECT 'a\\'; DROP TABLE ticket; --'",
        "SELECT $$ x $$",
        "REPLACE INTO ticket (id) VALUES (1)",
        "SELECT 'unterminated",
        "SELECT 1 # '\n; CREATE TABLE zz_hash (id INT); -- '",
        "SELECT 1 --'\n; DROP TABLE ticket; --'",
        "DELETE tqp_tables_note FROM ticket AS tqp_tables_note",
        "SET NAMES utf8mb4, GLOBAL sql_mode = ''",
        "SELECT nextval('ticket_id_seq')",
        # re-review 1, I3 (MariaDB): U+00A0 after ``--``; backslash inside "..."
        "SELECT 1 --\u00a0 FROM (SELECT 2 AS \u00a0) t; TRUNCATE zz_victim",
        'SELECT "a\\"\' " ; TRUNCATE zz_victim; -- \'',
        # re-review 1, I4 (PostgreSQL): ``--`` comment ends at \r; nested block comments
        "TRUNCATE tqp_tables_note -- x\r, zz_victim",
        "DROP TABLE tqp_tables_note -- x\r, zz_victim",
        "SELECT 1 -- x\r, setval('ticket_id_seq', 1)",
        "TRUNCATE tqp_tables_note /* /* */ ' */ , zz_victim -- '",
        # GUARD-STRICT-LEXICON: these tokens are refused anywhere, also inside literals
        "SELECT 'it''s; fine' FROM ticket",
        "SELECT 1 -- trailing comment",
        "SELECT 'a--b' FROM ticket",
        "SELECT '#' FROM ticket",
        "SELECT 1 /* c */ FROM ticket",
        "SELECT 'a */ b' FROM ticket",
        "INSERT INTO tqp_tables_note (body) VALUES ('C:\\temp')",
        "SELECT $q$ x $q$",
        "INSERT INTO tqp_tables_note (body) VALUES ('a\rb')",
        # ... and non-printable / non-ASCII characters outside '...'
        "SELECT 1\u00a0FROM ticket",
        'SELECT "Größe" FROM ticket',
        "CREATE TABLE tqp_tables_größe (id INT)",
        "SELECT 1\x0bFROM ticket",
        # M4: a bare rename followed by another clause
        "ALTER TABLE tqp_tables_note RENAME zz, ADD COLUMN y INT",
    ],
)
def test_guard_rejects(sql: str) -> None:
    with pytest.raises(PluginMigrationError, match="nicht erlaubt"):
        G.check(sql)


def test_guard_is_a_before_cursor_execute_hook() -> None:
    G(None, None, "SELECT 1", (), None, False)
    with pytest.raises(PluginMigrationError, match="nicht erlaubt: DROP TABLE ticket"):
        G(None, None, "DROP TABLE ticket", (), None, False)


# --- roadmap merge: no plugin migration may touch a Tiqora table (incl. the wave-4 CMDB, SCIM,
# ITSM-import and plugin tables) or a table of the Znuny ITSM packages; only tqp_<short>_ ------


def _foreign_tables() -> list[str]:
    import tiqora.db.tiqora.models  # noqa: F401  (registers every tiqora model)
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.itsm.znuny_adapter.profiles import ZNUNY_ITSM_PACKAGE_TABLES

    return sorted({*TiqoraBase.metadata.tables, *ZNUNY_ITSM_PACKAGE_TABLES})


def test_foreign_tables_cover_the_wave4_tables() -> None:
    tables = set(_foreign_tables())
    for name in (
        "tiqora_cmdb_ci",
        "tiqora_cmdb_ci_version",
        "tiqora_scim_user",
        "tiqora_itsm_import_run",
        "tiqora_itsm_import_map",
        "tiqora_itsm_link",
        "tiqora_plugin_data",
        "tiqora_process",
        "general_catalog",
        "configitem",
        "cip_allocate",
    ):
        assert name in tables


_WRITES = (
    "CREATE TABLE {t} (id INT)",
    "CREATE INDEX tqp_tables_ix ON {t} (id)",
    "ALTER TABLE {t} ADD COLUMN x INT",
    "ALTER TABLE tqp_tables_note RENAME TO {t}",
    "INSERT INTO {t} (id) VALUES (1)",
    "UPDATE {t} SET id = 2",
    "DELETE FROM {t}",
    "DROP TABLE {t}",
    "TRUNCATE {t}",
    "CREATE TABLE tqp_tables_b (a_id INTEGER REFERENCES {t} (id))",
    "DESCRIBE {t}",
)


@pytest.mark.parametrize("table", _foreign_tables())
def test_guard_rejects_writes_to_tiqora_and_itsm_tables(table: str) -> None:
    for template in _WRITES:
        with pytest.raises(PluginMigrationError, match="nicht erlaubt"):
            G.check(template.format(t=table))
    G.check(f"SELECT * FROM {table}")  # reading stays allowed (D6)
