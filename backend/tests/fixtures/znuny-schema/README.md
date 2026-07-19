# Znuny 6.5 schema fixtures

These files are **generated DDL/data** from Znuny 6.5.22
(`scripts/database/`), copied here so Tiqora’s schema conformance and
integration tests can load a real Znuny database layout without depending on
the developer-only reference tree (`znuny-6.5.22/`, gitignored).

| File | Role |
|---|---|
| `schema.mysql.sql` | MySQL/MariaDB table DDL (no FKs) |
| `initial_insert.mysql.sql` | Seed data (valid, users, states, …) |
| `schema-post.mysql.sql` | MySQL indexes / FK follow-up |
| `schema.postgresql.sql` | PostgreSQL table DDL (no FKs) |
| `initial_insert.postgresql.sql` | Seed data (PostgreSQL dialect) |
| `schema-post.postgresql.sql` | PostgreSQL indexes / FK follow-up |

**Installer order (required):** `schema` → `initial_insert` → `schema-post`.
Foreign keys (including circular `users`↔`valid`) are only added after seed
data exists. Test fixtures load files in this order.

## Origin and licence

- **Upstream:** Znuny 6.5.x (`https://www.znuny.org/`)
- **Licence:** GNU General Public License v3 (same as Znuny/OTRS core)
- **Source path:** `znuny-6.5.22/scripts/database/`

These are plain schema/data artefacts that define the **database interface**
Tiqora must remain compatible with during parallel operation. They are not
executable Znuny application code.

When upgrading the supported Znuny baseline, refresh these files from the
matching release’s `scripts/database/` directory and re-run the conformance
suite.
