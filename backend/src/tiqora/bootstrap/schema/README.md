# Znuny 6.5 base schema (shipped package data)

These files are **generated DDL/data** from Znuny 6.5.22
(`scripts/database/`), shipped with the Tiqora package so
`tiqora bootstrap` can initialise an empty database on greenfield installs.
The same files are used by integration tests (via
`tiqora.bootstrap.schema_loader`).

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
data exists.

## Origin and licence (NOTICE)

- **Upstream:** Znuny 6.5.22 (`https://www.znuny.org/`), taken verbatim from
  the release tarball path `scripts/database/`.
- **Copyright:** © 2021–2026 Znuny GmbH (https://znuny.com/); portions
  © 2001–2021 OTRS AG (https://otrs.com/).
- **Licence:** GNU General Public License v3.0 — these six files remain
  licensed under **GPL-3.0** as published by their upstream authors. They are
  included in this repository unmodified, as schema/data artefacts (mere
  aggregation in the sense of GPL-3.0 §5); the AGPL-3.0 licence of the rest
  of the Tiqora project does not apply to them. The full GPL-3.0 text is
  available at https://www.gnu.org/licenses/gpl-3.0.txt.

These are plain schema/data artefacts that define the **database interface**
Tiqora must remain compatible with. They are not executable Znuny application
code.

When upgrading the supported Znuny baseline, refresh these files from the
matching release’s `scripts/database/` directory and re-run the conformance
suite (and keep `backend/tests/fixtures/znuny-schema/` in sync).
