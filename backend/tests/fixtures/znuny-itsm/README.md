# Znuny ITSM schema fixtures

Generated database layout and package defaults of the official Znuny ITSM packages, one folder per
schema profile. They let Tiqora's ITSM tests build a Znuny database **with** ITSM tables (Mode A,
the importer, the Mode-B conformance checks) without a running Znuny.

| Profile | Znuny | Packages (peer `znuny-6.5-itsm` / `znuny-7.3-itsm`) |
|---|---|---|
| `itsm-6` | 6.5.x | `ITSM` bundle 6.5.3: GeneralCatalog 6.5.3, ITSMCore 6.5.5, ITSMIncidentProblemManagement 6.5.2, ITSMConfigurationManagement 6.5.4, ImportExport 6.5.4, ITSMChangeManagement 6.5.2, ITSMServiceLevelManagement 6.5.1 |
| `itsm-7` | 7.3.x | GeneralCatalog 7.3.1, ITSMCore 7.3.3, ITSMIncidentProblemManagement 7.3.2, ITSMConfigurationManagement 7.3.5, ImportExport 7.3.3, ITSMChangeManagement 7.3.4, ITSMServiceLevelManagement 7.3.1 |

The two profiles differ in exactly one ITSM table: in `itsm-7` `general_catalog_preferences` has an
auto-increment primary key `id` (6.5 has none). Details in `docs/itsm-import.md`.

| File | Role |
|---|---|
| `schema.mysql.sql`, `schema.postgresql.sql` | `CREATE TABLE` / `ALTER TABLE service|sla ADD ...` of all packages, in install order |
| `schema-post.mysql.sql`, `schema-post.postgresql.sql` | foreign keys (and PostgreSQL indexes) |
| `initial_insert.sql` | rows the packages leave behind, dialect neutral (explicit column lists, install time stamps normalised to `2000-01-01 00:00:00`) |
| `sysconfig.json` | `{name: effective value}` of the ITSM related `sysconfig_default` rows (`ITSM*`, `LinkObject::*`, ...) |
| `packages.json` | package names, versions, framework, created tables, columns added to core tables |
| `tables.md` | column and index listing per table (source of the tables in `docs/itsm-import.md`) |

**Load order** on top of the Znuny core fixtures (`../znuny-schema/`, or the target's own core
schema): core `schema` -> core `initial_insert` -> core `schema-post` -> ITSM `schema` -> ITSM
`initial_insert` -> ITSM `schema-post`. Like Znuny's own `initial_insert`, the file inserts explicit
ids and does not advance PostgreSQL sequences. The index names `<table>_U_<n>` of unique indexes are
random per generation (Znuny's XML2SQL), do not rely on them.

## Regeneration

Never edit by hand. Needs a fresh golden ITSM peer (installed packages, seed NOT run):

```sh
just golden-itsm-fetch znuny-6.5-itsm
GOLDEN_PEER=znuny-6.5-itsm just golden-up
cd backend && uv run python ../tests/golden/itsm_survey.py znuny-6.5-itsm \
    --out tests/fixtures/znuny-itsm/itsm-6/ --verify
```

`--verify` loads the core schema of the peer plus these files into throw-away MariaDB 10.11 and
PostgreSQL 16 containers and checks that every package table exists. Same for `znuny-7.3-itsm` and
`itsm-7`. Only made-up data may end up here: the packages ship no customer data; the test
`test_fixtures_have_no_real_data` guards that.

## Behaviour vectors (`behaviour/`)

`behaviour/<profile>.json` pins how the packages *behave* (priority matrix lookup, service incident
state, link side effects, permissions, definition rules, ...): ten answers per profile with
`source` (file:line), `answer`, `expected` and `consequence`, documented in `docs/itsm-import.md`
("Behaviour"). The `questions` part is maintained by hand from the sources; the `peer` part (the
results of `tests/golden/itsm_behaviour.pl` on the real peer) is generated and never edited:

```sh
just golden-itsm-fetch znuny-6.5-itsm
GOLDEN_PEER=znuny-6.5-itsm just golden-up          # a FRESH peer (no seed): the scenarios run once
cd backend && GOLDEN_PEER=znuny-6.5-itsm uv run python ../tests/golden/itsm_survey.py znuny-6.5-itsm \
    --behaviour --out tests/fixtures/znuny-itsm/behaviour/
```

Same for `znuny-7.3-itsm`. `test_itsm_behaviour_vectors.py` compares both halves.

## Origin and licence (NOTICE)

- **Upstream:** the Znuny ITSM packages (`https://download.znuny.org/releases/itsm/`), installed on a
  Znuny peer; DDL produced by Znuny's own `Dev::Tools::Database::XML2SQL` from the packages'
  `DatabaseInstall` sections, rows read back from the installed peer database.
- **Copyright:** (c) Znuny GmbH (https://znuny.com/); portions OTRS AG.
- **Licence:** GNU General Public License v3.0 (the packages' `License` tag). These generated files
  remain GPL-3.0 and are included as test fixtures (mere aggregation in the sense of GPL-3.0
  section 5); the AGPL-3.0 licence of the rest of Tiqora does not apply to them.
