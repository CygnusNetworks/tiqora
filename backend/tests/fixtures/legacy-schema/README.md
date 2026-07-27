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

## Profiles in this tree (release anchors)

| Directory | Upstream source |
|-----------|-----------------|
| `otrs-znuny-6.0` | Znuny 6.0.45 `scripts/database/otrs-*` |
| `znuny-6.3` | Znuny 6.3.4 `scripts/database/otrs-*` |
| `znuny-6.5` | Znuny 6.5.22 `scripts/database/*` |
| `znuny-7.0` | Znuny 7.0.19 (GitHub `rel-7_0_19`) |
| `znuny-7.3` | Znuny 7.3.5 (GitHub `rel-7_3_5`) |

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
