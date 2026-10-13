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
        "SELECT 'it''s; fine' FROM ticket",
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
        "SELECT 1 -- trailing comment",
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
    ],
)
def test_guard_rejects(sql: str) -> None:
    with pytest.raises(PluginMigrationError, match="nicht erlaubt"):
        G.check(sql)


def test_guard_is_a_before_cursor_execute_hook() -> None:
    G(None, None, "SELECT 1", (), None, False)
    with pytest.raises(PluginMigrationError, match="nicht erlaubt: DROP TABLE ticket"):
        G(None, None, "DROP TABLE ticket", (), None, False)
