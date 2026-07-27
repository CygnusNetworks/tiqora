# Multi-version OTRS/Znuny schema fixtures (Layer A)

Unmodified upstream fresh-install DDL + seed for **release schema-matrix**
tests. Each directory is a :class:`~tiqora.db.legacy.profile.SchemaProfileId`
value and contains the six dialect files Tiqora loaders expect:

| File | Role |
|------|------|
| `schema.{mysql,postgresql}.sql` | Table DDL (no FKs) |
| `initial_insert.{mysql,postgresql}.sql` | Seed data |
| `schema-post.{mysql,postgresql}.sql` | Indexes / FK follow-up |

**Installer order (required):** `schema` → `initial_insert` → `schema-post`.

## Profiles in this tree

| Directory | Upstream source | Matrix |
|-----------|-----------------|--------|
| `otrs-znuny-6.0` | Znuny 6.0.45 `otrs-*` | release |
| `znuny-6.1` | Znuny 6.1.2 `otrs-*` | full |
| `znuny-6.2` | Znuny 6.2.2 `otrs-*` | full |
| `znuny-6.3` | Znuny 6.3.4 `otrs-*` | release |
| `znuny-6.4` | Znuny 6.4.5 | full (detected as `znuny-6.5`) |
| `znuny-6.5` | Znuny 6.5.22 | release |
| `znuny-7.0` | GitHub `rel-7_0_19` | release |
| `znuny-7.1` | GitHub `rel-7_1_7` | full |
| `znuny-7.2` | GitHub `rel-7_2_3` | full |
| `znuny-7.3` | GitHub `rel-7_3_5` | release |

Files are renamed to the Tiqora convention (`schema.mysql.sql` etc.) without
content changes. Older Znuny trees used an `otrs-` prefix on the same files.

## Origin and licence (NOTICE)

- **Upstream:** Znuny / OTRS release trees, taken verbatim from
  `scripts/database/`.
- **Copyright:** © Znuny GmbH / © OTRS AG (respective years).
- **Licence:** GNU General Public License v3.0 — these fixtures remain
  **GPL-3.0** as published by their upstream authors. Included unmodified as
  test fixtures (mere aggregation under GPL-3.0 §5); the AGPL-3.0 licence of
  the rest of Tiqora does not apply to them.

See also the default bootstrap schema under
`backend/src/tiqora/bootstrap/schema/` (Znuny 6.5, used by the day-to-day
`db` suite).

## Running the matrix

```sh
cd backend
SCHEMA_MATRIX=1 uv run pytest -q -m schema_matrix
```

Requires Docker (testcontainers) and ``SCHEMA_MATRIX=1`` (skipped in default
``pytest -q`` / PR CI). Release tags, nightly, and ``workflow_dispatch`` set
the flag in `.github/workflows/schema-matrix.yml` — see `docs/testing.md`.
