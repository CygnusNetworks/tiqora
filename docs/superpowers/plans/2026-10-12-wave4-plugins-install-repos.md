# Wave 4 — Plugin System Phase 2b (plugin migrations, runtime install, signatures, repositories + updates) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admins install, update and remove Tiqora plugins from the web UI — from an uploaded signed `.tqplugin` file or from their own repositories — without building an image; plugins bring their own database tables through their own Alembic migrations; every package is checked (signature and trust level, pure-Python, dependencies already in the image, compatibility, no name collisions) before anything runs; the processes restart one after another on their own; sites with a read-only root keep the baked-image path from phase 1.

**Architecture:** New backend modules in `tiqora.plugins`: `signing` (ed25519, key ids, trust levels), `package` (the `.tqplugin` envelope + wheel inspection and unpacking — no pip), `inspect` (install checks → one `PackageReport`), `runtime` (package store in the database, materialising into `TIQORA_PLUGINS_DIR`, `sys.path` activation before discovery), `migrations` + `alembic_env/` (one Alembic environment per plugin with its own version table `tqp_<short_id>_alembic_version` and a statement guard), `repo_index` + `repositories` (index format v1, fetch, refresh, update detection), `install_jobs` (install, uninstall, fetch, refresh as `core.*` jobs in a one-slot `plugins` lane) and `restart` (process registry + coordinated restart). The database holds the truth (`tiqora_plugin_package` keeps every installed package as a blob); the volume is a cache each process fills at boot, so no process depends on another having written files. Admin API in two new routers; the existing "Erweiterungen" page gets tabs "Installiert", "Verfügbar", "Paketquellen", an upload/install dialog with the declared capabilities and the trust level, update badges, an uninstall dialog ("Daten behalten" / "Daten löschen") and a restart progress panel.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic (programmatic `Config`), `cryptography` (Ed25519; already a core dependency), `packaging`, `zipfile`, `httpx` with `tiqora.security.outbound.pin_outbound_url`, Pydantic v2; React 19, TanStack Query, Vitest + Testing Library, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-plugin-system-design.md` — phase-2 row of "Phases", the parts *plugin migrations, runtime install, signatures, repository + updates* (§2 package, §5 database, §6 install and run, §7 trust and signatures, §8 repository, §8a marketplace-ready index). The other phase-2 parts (remaining backend extension points, sync hooks) are the parallel plan **plugins2a**. Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (table "Waves", row 4: "Plugin system 2"; "Definition of done"). Builds on phase 1: plan `docs/superpowers/plans/2026-10-10-wave2-plugin-system.md` (Design P0–P15, Deviations 1–15) and its rulings `<repo>/.superpowers/sdd/2026-10-10-wave2-plugin-system/rulings.md` — every ruling stays binding (R-BOOT-ANYERR, R-SCOPE-PLUGINS, R-BAKE-SUPPLY, R-STATUS-ROLES, R-PLUGINS-SAFEMODE-STATUS, R-TRUST-WORDING, R-SHORTID-RESERVED, R-TS-REQUIRED-DEFAULTS, R-GERMAN-SIE, R-MIGRATION-SERIAL, R-TESTS …). Product decisions (binding): plugins come from **custom repositories only**; the index format is marketplace-ready (§8a); nothing phones home and no repository is preconfigured.

## Open questions for the product owner

Each item is decided conservatively in this plan (marked **Assumption:** where it is used) and can be flipped later without a redesign.

1. **There is no official Tiqora signing key yet.** `OFFICIAL_KEYS` ships empty, so the trust level "offiziell" cannot occur until the project generates a key pair, keeps the private key offline and publishes the public key in the code. (D3)
2. **Unsigned packages are refused** unless `TIQORA_PLUGINS_ALLOW_UNSIGNED=1` — for uploads and for repository downloads alike; a valid signature by an unknown key counts as unsigned. Image-baked plugins are exempt (the operator built the image). (D3)
3. **A repository without a configured public key** can be browsed and installed from, but its publisher keys are ignored: its packages can only be "offiziell", "lokal signiert" or (with the env switch) "unsigniert" — never "verifizierter Herausgeber". (D3, D7)
4. **"Verifizierter Herausgeber" needs a matching id:** the plugin id must start with the publisher id from the signed index (`org.example.` for publisher `org.example`) and `publisher_id` in the manifest must equal it; otherwise the key counts as unknown. (D3)
5. **The image wins.** A plugin id that is baked into the image cannot be installed or updated through the UI; if a later image adds an id that was installed through the UI, the image copy is used and the uploaded package is set aside. (D5)
6. **Installing through the UI switches the plugin on by default** (a checkbox in the dialog), independent of `TIQORA_PLUGINS_AUTO_ENABLE`, which keeps governing image plugins. (D9)
7. **No downgrade through the UI.** An older version than the installed one is refused ("Ältere Version: bitte erst deinstallieren (Daten behalten) und dann installieren"). (D9)
8. **Coordinated restart is on by default** and stops the sequence (instead of continuing) when a process does not come back within 3 minutes. Sites whose containers do not restart by themselves (`restart: unless-stopped`/`always`, Kubernetes) must set `TIQORA_PLUGINS_COORDINATED_RESTART=0`; the UI then asks for a manual restart. (D10)
9. **Repository servers on private networks are allowed** (`allow_private_networks=True`, like the AI provider URLs admins enter); loopback, link-local and metadata addresses stay blocked. `file://` repositories are only possible via `TIQORA_PLUGIN_REPOSITORIES` and only below `TIQORA_PLUGIN_REPOSITORY_FILE_ROOT`. (D8)
10. **Limits:** a package is at most `TIQORA_PLUGINS_MAX_PACKAGE_MB` = 20 MB (hard ceiling 25, the global upload limit); uploaded but not installed packages are deleted after 24 h; the previous installed version stays on disk (not offered as "rollback"). (D1, D5)
11. **Repositories are checked every 6 h**, and only when at least one is configured. No project repository URL is preconfigured; first-party plugins come in the image or from a repository the site adds itself. (D8)
12. **Plugin migrations must be backward compatible** (the old version keeps running against the new tables until the restart). Documented for plugin authors, not enforced. (D6)
13. **"Daten löschen" exports first** (JSON lines, gzip, as a job file only the requesting admin can download, kept like every job file) and refuses to delete when the export would exceed 200 MiB ("bitte ein Datenbank-Backup machen"). There is no "force" switch in this plan. (D9)
14. **Migrations of switched-off plugins still run at boot**, so the tables always match the installed code. (D6)
15. **The AI worker is restarted only when it hosts plugins** (`PLUGIN_ROLES`); phase 1 says it does not — plugins2a may change that. (D10, Cross-plan contracts)
16. **Signing and index tooling is minimal:** `tiqora plugins keygen|pack|sign|inspect|index` are in this plan because nothing could be signed without them; `tiqora plugin new|dev` and the test kit stay in phase 3. (D14)
17. **Start order for plugin migrations of image plugins:** only the api container migrates (entrypoint), so worker/ai-worker/mcp must start after the api is healthy (compose examples get `condition: service_healthy`); otherwise they mark such plugins "Datenbank-Änderungen stehen aus" until their next restart — no automatic reload in this plan. (D6, Task 25)

## Global Constraints

- **Where:** a new worktree off `roadmap-wave1` **after plugins2a is merged** (path and branch name from the controller). Never touch `<repo>` (main); never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. zsh has `noclobber`: overwrite files with `>|` or the Write tool.
- **Only made-up example data:** `example.org`, `example.com`, `.test`; plugin ids `org.example.*`; publisher `Example GmbH`; names like "Erika Muster". Never real customer data, real hostnames or real keys in tests, docs, fixtures or demo mocks. Test keys are generated per test run (`Ed25519PrivateKey.generate()`), never committed.
- **Tables:** this plan writes only `tiqora_*` tables and the plugin's own `tqp_<short_id>_*` tables. Plugin migrations may never create, alter, drop or write any other table (D6 statement guard). No Znuny table is written by this plan; any Znuny write in plugin code goes through the existing write services (plugins2a's SDK domain services). Never change Znuny DDL.
- **No JOIN between `tiqora_*` and Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` → error 1267): load keys, then query with an IN-list. (This plan has no reason to touch Znuny tables at all.)
- **Transactions:** SQLAlchemy 2 autobegins on the first read — never `async with session.begin()` after any read on the same session; do pre-checks in their own block and `commit()`/`rollback()` before a write block, or keep every read inside the write transaction (`tiqora-route-read-blocks-session-begin`). One full-POST test per new router endpoint against a real DB session.
- **No long synchronous endpoints:** prod nginx has `proxy_read_timeout 90s` on `/api/`; synchronous endpoints that can take more than ~60 s are forbidden. Downloads, unpacking into the volume, import checks, migrations, exports and repository refreshes run as background jobs (`tiqora.jobs`, `enqueue_job`) and the UI polls them (`useJob`). Only the upload endpoint inspects synchronously (≤ 25 MB in memory, hashing + zip reading only, no network, no import).
- **Both dialects:** every DB test runs on MariaDB AND PostgreSQL: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern: `backend/tests/test_admin_api.py`), `TiqoraBase.metadata.create_all` for new tables. `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest` — memory "uv run pytest broken"). New DB test modules clean up their rows themselves (`tests._row_cleanup.snapshot_marks` / `delete_above_marks`, or explicit deletes in a module-scoped autouse fixture) and drop every `tqp_*` table they created; never add anything to `backend/tests/db_leak_baseline.txt`.
- **Lint gates before every backend commit:** `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk` (all three; `ruff format --check` is what turns CI red).
- **Seed ids** only inside this plan's band **62000–62399** (sub-bands per task); confirm free with `grep -rnE "\b62[0-3][0-9]{2}\b" backend/tests` before use — if another wave-4 plan already took it, move the whole band to the next free 400-wide block and say so in the first commit. Seeded logins/names contain `plg62`.
- **Migrations:** one new migration in `backend/alembic/versions_tiqora`, named with the **next free number at implementation time** (today: files run up to `20261010_0076`, so the next free number is `0077`) and `down_revision` = the **current primary head at implementation time** (today `20261010_0073` — the head is NOT the highest file number: 0073 was re-pointed onto 0076 at merge). Determine both with `cd backend && uv run alembic heads` (authoritative) and `ls backend/alembic/versions_tiqora` (numbers only); never trust ids written here — itsm1, cmdb, scim and plugins2a add migrations in the same wave (R-MIGRATION-SERIAL: whoever merges later re-points `down_revision`). Follow skill `alembic-two-chain-new-head-forks-graph` (never rebase the gated owned chain). Update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`. ORM in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL (`backend/tests/test_tiqora_model_ddl.py`). Plugin migrations (D6) never enter the core graph.
- **Timestamps** written by this plan are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- **SysConfig/YAML booleans** are Perl-truthy strings: read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)` (this plan reads none; env booleans are pydantic fields).
- **OpenAPI:** after any API model/route change regenerate per skill `tiqora-openapi-regen-or-ci-wipes-schema`: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` starts `{"components"` and the diff is tens/hundreds of lines. Never hand-edit `schema.d.ts`. A response field WITH a default becomes required in TS — fixtures and demo mocks include it (R-TS-REQUIRED-DEFAULTS).
- **Frontend (`frontend/`):** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc). Never run prettier/biome on `frontend/` (memory "Frontend ohne Formatter"). New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user with **"Sie/Ihr"** (R-GERMAN-SIE). New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`; `VITE_DEMO=1`) and are checked in demo mode with Playwright (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `themeColor()` (`frontend/tailwind.config.js`, `frontend/src/themes/tokens.css`). API wrappers are hand-written in `frontend/src/lib/pluginsApi.ts` (`api.request<T>(method, path, {query, body, signal})`, `Schemas["…"]` types); error text via `apiErrorMessage`. Nothing this plan adds is called by the agent/admin shells on every page, so `frontend/e2e/fixtures/mock-api.ts` needs no new route — keep it that way (wave-1 ruling R-E2E).
- **UI copy:** German first, plain words, named by what admins recognise: "Erweiterung", "Paket", "Paketquelle", "Herausgeber", "Signatur", "Aktualisierung verfügbar", "Installieren", "Deinstallieren", "Daten behalten", "Daten löschen", "Tiqora wird neu gestartet". No "Plugin", "Wheel", "Migration", "Alembic", "Lane", "Worker", "ed25519", "SHA-256" in UI text (allowed inside "Details" disclosures and in docs). Use "Hintergrunddienst" for the worker, "KI-Schnittstelle (MCP)" for mcp, "Weboberfläche und Schnittstelle" for the api (phase-1 labels).
- **Plugin surface:** `tiqora_sdk.__all__` and `@tiqora/plugin-sdk` are owned by plugins2a in this wave; this plan adds **no** SDK symbol and does not bump `SDK_VERSION` (the migration contract is file-based, D6). The SDK surface snapshot tests must stay green unchanged.
- **Golden tests:** this plan adds no Znuny-compatible surface, so the Znuny golden peers (6.5, 7.3 under `backend/tests/golden`) are not involved; the existing golden suite must still pass in Task 26.
- **Security baseline:** package bytes are untrusted until the checks in D2–D4 pass; unpacking happens only into a fresh temp dir below `TIQORA_PLUGINS_DIR` and is renamed into place afterwards; nothing from a package is imported in the api process before a restart; the import check runs in a subprocess. Error messages never echo package file contents.

## Review Focus

1. **A crafted package** — zip-slip member (`../x`, `/abs`, `C:\x`), symlink member, duplicate member names, a member missing from `CHECKSUMS`, a `CHECKSUMS` line without member, a wheel whose `RECORD` hash does not match, a native `.so`/`.pyd`, a non-`py3-none-any` tag, a `.data/scripts` dir, a top-level package `tiqora`/`fastapi`/`json`, 6 000 members or a 1 GB uncompressed member (zip bomb) → refused with one German reason before any file is written outside the temp dir. Pinned in Task 3 (`test_read_rejects_[…]` parametrized) and Task 5 (`test_top_level_collision_refused`).
2. **A plugin migration that reaches outside its namespace** — `CREATE TABLE ticket_x`, `ALTER TABLE ticket`, `DROP TABLE users`, `INSERT INTO ticket_history`, a foreign key `REFERENCES ticket(id)`, an unknown statement (`GRANT …`) → refused **before** the statement runs; PostgreSQL rolls the whole run back, MariaDB keeps the earlier `tqp_` tables and reports the failure; the plugin is not loaded and core is untouched. Pinned in Task 8 (`test_guard_rejects[…]` both dialects, `test_failed_run_rolls_back_on_postgres`, `test_failed_run_reported_on_mariadb`).
3. **A removed, broken or half-installed plugin never blocks core** — plugin uninstalled with "Daten behalten", files missing on the volume, package blob missing, failed plugin migration, runtime plugin crashing every process (→ safe mode) → `tiqora migrate upgrade` still reaches the core head, other plugins load, and in safe mode the admin page still lists the runtime plugin and can uninstall it. Pinned in Task 9 (`test_core_upgrade_ignores_plugin_version_tables`, `test_failed_plugin_migration_does_not_stop_others`), Task 11 (`test_missing_blob_marks_invalid_core_boots`, `test_safe_mode_lists_runtime_plugins_without_import`) and Task 16 (`test_uninstall_in_safe_mode`).
4. **The coordinated restart never takes the whole site down** — a process that does not come back stops the sequence and the api stays up with a German error; two api instances restart one at a time; `TIQORA_PLUGINS_COORDINATED_RESTART=0` never exits; a second restart request while one runs is refused. Pinned in Task 12 (`test_decide_[…]` table tests, `test_timeout_stops_sequence`) and Task 13 (`test_restart_request_conflict`).
5. **Trust cannot be upgraded by a third party** — a valid signature by a key that is unknown, revoked, listed in an index whose own signature is wrong, or listed for another publisher id; a repository download whose sha256 differs from the index; an index replaced by an older one (lower `generated_at`) → package treated as unsigned (refused without the env switch) or download refused. Pinned in Task 2 (`test_verify_[…]`), Task 6 (`test_trust_[…]`), Task 18 (`test_index_rollback_refused`) and Task 19 (`test_download_hash_mismatch_refused`).

Also pinned (not top five): an upload over the size cap is cut off after the cap (Task 14 `test_upload_too_large_413`); a plugin id baked into the image cannot be installed through the UI (Task 6 `test_image_plugin_not_installable`); "Daten löschen" exports before it drops and drops only `tqp_<short_id>_*` (Task 16 `test_delete_data_exports_then_drops_only_prefix`); re-enabling semantics of phase 1 (R-REENABLE-BACKLOG) are unchanged by install/update (Task 15 `test_update_keeps_enabled_and_hook_cursors`).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Phase-1 names are used exactly as they exist in code today (verified 2026-10-12 in `<repo>`): `DiscoveredPlugin`, `discover_plugins`, `discovered_plugins`, `clear_discovery_cache`, `plugin_asset_path`, `ENTRY_POINT_GROUP`, `MANIFEST_NAME` (`tiqora.plugins.discovery`); `PluginManifest`, `Capabilities`, `parse_manifest`, `ManifestError`, `PLUGIN_ID_RE`, `SHORT_ID_RE` (`tiqora.plugins.manifest`); `check_compat`, `release_core_version` (`tiqora.plugins.compat`); `init_registry`, `retry_plugin_boot`, `is_missing_table_error`, `_load_plugins` (`tiqora.plugins.bootstrap`); `PluginRole`, `PLUGIN_ROLES`, `RuntimeState`, `summarize_status`, `QUARANTINE_AFTER` (`tiqora.plugins.state`); `run_in_daemon_thread`, `PluginLoadTimeout` (`tiqora.plugins.loader`); `sync_discovered`, `prepare_boot`, `set_enabled`, `reset_plugin`, `list_plugin_rows`, `list_runtime_rows`, `load_hooks`, `storable` (`tiqora.plugins.store`); `bake`, `core_constraints`, `verify_installed_plugins`, `BakeError` (`tiqora.plugins.bake`); `add_plugins_subparser` (`tiqora.cli.plugins`); `build_alembic_config`, `TIQORA_LOCATION`, `OWNED_LOCATION`, `_resolve_ownership` (`tiqora.cli.migrate`); `PluginAdminOut`, `PluginAdminListOut`, `PluginCapabilityOut`, `_build_all`, `_build_one`, `RESTART_MESSAGE` (`tiqora.api.v1.admin.plugins`); `TiqoraPlugin`, `TiqoraPluginRuntime`, `TiqoraPluginHook`, `JOB_FILE_BLOB`, `MEDIUM_TEXT_VARIANT` (`tiqora.db.tiqora.models`); `JobHandler`, `JOB_HANDLERS`, `LANE_SLOTS` (`tiqora.jobs.registry`); `JobContext` (`.progress`, `.checkpoint`, `.check_cancelled`, `.add_file`), `JobFailed` (`tiqora.jobs.context`); `enqueue_job`, `find_active_job`, `utc_now` (`tiqora.jobs.service`); `JobAccepted` (`tiqora.jobs.schemas`); `register_core` (`tiqora.extensions.core`); `DaemonService`, `DAEMON_SERVICES` (`tiqora.worker.services`); `_interval_loop` (`tiqora.worker.__main__`); `read_upload_within_limit`, `MAX_ATTACHMENT_BYTES` (`tiqora.api.uploads`); `pin_outbound_url`, `OutboundURLError` (`tiqora.security.outbound`); `AdminUser` (`tiqora.api.v1.admin.deps`), `DbSession`, `AppSettings` (`tiqora.api.deps`); frontend `pluginsApi`, `PluginsPage`, `SoftChip`, `Tabs`, `ConfirmDialog`/`useConfirm`, `JobProgress`, `useJob`, `humanizeResult`, `pluginFixtures` helpers. Fixture plugin of phase 1: `org.example.hello` (package `tq_fixture_hello`, short id `hello`).

### D0. File map

Backend (new unless "mod"):

| Path | Responsibility |
|---|---|
| `backend/alembic/versions_tiqora/<next free>_plugin_install.py` | D1 tables and columns |
| mod `backend/src/tiqora/db/tiqora/models.py` | `TiqoraPluginPackage`, `TiqoraPluginRepository`, `TiqoraProcess`, `TiqoraRestart`; new `TiqoraPlugin` columns |
| mod `backend/src/tiqora/config.py` | D1 settings |
| `backend/src/tiqora/plugins/signing.py` | D2/D3: keys, key ids, `sign_bytes`, `verify_signature`, `TrustLevel`, `KeyRing`, `OFFICIAL_KEYS`, `local_keys` |
| `backend/src/tiqora/plugins/package.py` | D2/D4: `.tqplugin` + wheel reading (`read_tqplugin`), `write_tqplugin`, `PackageError`, `TqPackage`, `WheelInfo`, `unpack_wheel` |
| `backend/src/tiqora/plugins/install_checks.py` | D4: `requirement_problems`, `collision_problems`, `static_warnings`, `migration_tables` |
| `backend/src/tiqora/plugins/repo_index.py` | D7: index models v1, `parse_index`, `verify_index`, `resolve_package_url`, `newest_compatible` |
| `backend/src/tiqora/plugins/inspect.py` | D3/D4: `PackageReport`, `inspect_package` (one entry for upload, repository download and the install job) |
| `backend/src/tiqora/plugins/runtime.py` | D5: `plugins_dir_status`, `materialize`, `version_dir`, `activate_runtime_plugins`, `gc_runtime_dirs` |
| `backend/src/tiqora/plugins/packages_store.py` | D1/D5: DB access for `tiqora_plugin_package` and the install columns of `tiqora_plugin` |
| `backend/src/tiqora/plugins/migrations.py` | D6: `plugin_alembic_config`, `upgrade_plugin`, `downgrade_plugin_to_base`, `plugin_migration_head`, `record_migration`, `drop_plugin_data`, `export_plugin_data`, `StatementGuard`, `PluginMigrationError` |
| `backend/src/tiqora/plugins/alembic_env/env.py`, `script.py.mako` | D6: the shared Alembic environment for plugin migrations (inside the package, so it ships in the wheel and the image without a Dockerfile COPY) |
| `backend/src/tiqora/plugins/repositories.py` | D8: env sync, `fetch_index`, `refresh_repository`, `catalog`, `available_updates`, `download_package` |
| `backend/src/tiqora/plugins/install_jobs.py` | D9: job handlers `core.plugin_install`, `core.plugin_uninstall`, `core.plugin_fetch`, `core.plugin_repository_refresh`; payload models |
| `backend/src/tiqora/plugins/restart.py` | D10: `register_process`, `restart_watch_loop`, `decide`, `request_restart`, `restart_status`, `RESTART_EXIT_CODE`, `restart_requested` |
| `backend/src/tiqora/api/v1/admin/plugin_packages.py` | D11: packages, install, uninstall, restart endpoints |
| `backend/src/tiqora/api/v1/admin/plugin_repositories.py` | D11: repositories CRUD, refresh, catalog, download |
| mod `backend/src/tiqora/api/v1/admin/plugins.py` | D11: new fields on `PluginAdminOut` / `PluginAdminListOut`; runtime activation before listing |
| mod `backend/src/tiqora/api/v1/admin/__init__.py` | register the two routers (append-only) |
| mod `backend/src/tiqora/plugins/bootstrap.py` | activation before discovery; migration gate (D6) |
| mod `backend/src/tiqora/plugins/store.py` | `sync_discovered` fills `short_id`; `fail_active_rows` unchanged |
| mod `backend/src/tiqora/plugins/bake.py` | `verify_installed_plugins(..., extra_paths, only)` for the subprocess import check |
| mod `backend/src/tiqora/cli/plugins.py` | D14 subcommands |
| mod `backend/src/tiqora/cli/migrate.py` | plugin migrations after core (D6) |
| mod `backend/src/tiqora/extensions/core.py` | register the four job handlers |
| mod `backend/src/tiqora/jobs/registry.py` | `LANE_SLOTS["plugins"] = 1` (documentation; 1 is the default) |
| mod `backend/src/tiqora/api/app.py`, `worker/__main__.py`, `mcp_server/server.py`, `ai/worker.py`, `main.py` | D10 process registration, watch loop, exit code |
| mod `backend/src/tiqora/worker/services.py`, `worker/__main__.py` | daemon `plugin_repositories` (D8) |
| `backend/tests/plugins/package_builder.py` | test seam: `build_wheel`, `build_tqplugin`, `make_keypair` (pure Python, no build backend) |
| `backend/tests/plugins/fixtures/tq_fixture_tables/…` | fixture plugin `org.example.tables` (short id `tables`) with `migrations/` creating `tqp_tables_note`, one route reading it, `i18n/en.json` |
| `backend/tests/test_plugins_*.py` | one module per task (names in the tasks) |

Frontend / infra / docs (new unless "mod"):

| Path | Responsibility |
|---|---|
| mod `frontend/src/lib/pluginsApi.ts` | wrappers for every D11 endpoint, types |
| mod `frontend/src/test/pluginFixtures.ts` | `packageReport()`, `repository()`, `catalogEntry()`, `restartStatus()` factories with every defaulted field |
| mod `frontend/src/routes/admin/PluginsPage.tsx` | tabs, upload button, restart panel |
| `frontend/src/routes/admin/plugins/InstallDialog.tsx` | report → confirm → job progress |
| `frontend/src/routes/admin/plugins/UninstallDialog.tsx` | keep/delete data, export download |
| `frontend/src/routes/admin/plugins/RestartPanel.tsx` | restart status polling |
| `frontend/src/routes/admin/plugins/CatalogTab.tsx` | "Verfügbar" |
| `frontend/src/routes/admin/plugins/RepositoriesTab.tsx` | "Paketquellen" |
| `frontend/src/routes/admin/plugins/trust.ts` | trust/source → label key + colour |
| mod `frontend/src/demo/mockData.ts` | demo answers for all D11 endpoints |
| mod i18n `en.json`, `de.json` (+ propagation) | D13 keys |
| mod `Dockerfile` | `mkdir -p /var/lib/tiqora/plugins` owned by `tiqora` |
| mod `docker-compose.example.yml`, `docker-compose.quickstart.yml`, `docker-compose.local.yml` | volume `tiqora_plugins` on api, worker, ai-worker, mcp |
| mod `docs/plugins.md`, `docs/deployment.md`, `docs/architecture.md`, `docs/superpowers/specs/2026-10-08-plugin-system-design.md` (status line) | D15 |

### D1. Migration, tables, settings (Task 1)

One migration. All ids/keys `VARCHAR(190)` or shorter (utf8mb4 index limit). Blobs use the existing `JOB_FILE_BLOB` variant (`LONGBLOB` on MariaDB, `BYTEA` on PostgreSQL); long text uses `MEDIUM_TEXT_VARIANT`.

```
tiqora_plugin  (existing; new columns, all NULL-able or with server_default)
  short_id          VARCHAR(16)  NULL       -- filled by sync_discovered and install; needed to drop data after removal
  package_sha256    CHAR(64)     NULL       -- installed package (source "upload"/"repository"); NULL = image
  repository_id     INTEGER      NULL       -- source "repository": where it came from (soft reference)
  signer            VARCHAR(200) NULL       -- display: "Example GmbH (Schlüssel 1a2b…)" / NULL
  installed_by      INTEGER      NULL       -- agent who installed the current package
  migrated_version  VARCHAR(64)  NULL       -- plugin version whose migrations ran last (D6)
  migrated_head     VARCHAR(64)  NULL       -- Alembic head reached for that version
  migrated_at       DATETIME     NULL
  migration_error   TEXT         NULL       -- German, ≤ 2000 chars; NULL = fine
  (existing source VARCHAR(20): "image" | "upload" | "repository"; signature_status VARCHAR(20): TrustLevel value)

tiqora_plugin_package                       -- every uploaded/downloaded package; installed ones are kept
  sha256          CHAR(64) PK               -- of the whole .tqplugin file
  plugin_id       VARCHAR(190) NOT NULL, INDEX
  version         VARCHAR(64)  NOT NULL
  size_bytes      BIGINT       NOT NULL
  data            LONGBLOB/BYTEA NOT NULL   -- deferred() in the ORM
  manifest        TEXT         NOT NULL     -- tiqora-plugin.toml text
  report          MEDIUMTEXT   NOT NULL     -- PackageReport JSON at upload time (D4)
  signature_status VARCHAR(20) NOT NULL
  key_id          VARCHAR(16)  NULL
  source          VARCHAR(20)  NOT NULL     -- "upload" | "repository"
  repository_id   INTEGER      NULL
  uploaded_by     INTEGER      NULL
  uploaded_at     DATETIME     NOT NULL
  installed_at    DATETIME     NULL         -- set when an install job used it; NULL = never installed

tiqora_plugin_repository
  id              INTEGER PK autoincrement
  name            VARCHAR(200) NOT NULL
  url             VARCHAR(1000) NOT NULL    -- https://…/index.json  (env only: file:///…/index.json)
  url_key         CHAR(64) NOT NULL, UNIQUE -- sha256(url): unique index on a short column (1000 chars cannot be indexed on MariaDB)
  public_key      VARCHAR(100) NULL         -- "ed25519:<base64>"; NULL = index not verifiable (Assumption 3)
  enabled         BOOLEAN NOT NULL
  origin          VARCHAR(10) NOT NULL      -- "admin" | "env"
  index_json      MEDIUMTEXT NULL           -- last accepted index (exact bytes as UTF-8)
  index_sha256    CHAR(64) NULL
  index_generated_at DATETIME NULL          -- from the index; a lower value later is refused (rollback guard)
  index_verified  BOOLEAN NOT NULL DEFAULT false
  last_checked_at DATETIME NULL
  last_ok_at      DATETIME NULL
  last_error      TEXT NULL
  created_at      DATETIME NOT NULL
  created_by      INTEGER NULL
  updated_at      DATETIME NOT NULL

tiqora_process                              -- one row per running process (D10)
  instance_id     VARCHAR(100) PK           -- f"{role}:{hostname}:{pid}:{uuid4().hex[:8]}"
  role            VARCHAR(20) NOT NULL, INDEX  -- "api" | "worker" | "mcp" | "ai-worker"
  hostname        VARCHAR(200) NOT NULL
  pid             INTEGER NOT NULL
  boot_generation BIGINT NOT NULL           -- restart generation current at boot (0 = none)
  started_at      DATETIME NOT NULL
  heartbeat_at    DATETIME NOT NULL
  exiting_at      DATETIME NULL             -- set right before a coordinated exit

tiqora_restart                              -- coordinated restart requests (D10)
  generation      BIGINT PK autoincrement
  requested_at    DATETIME NOT NULL
  requested_by    INTEGER NULL
  reason          VARCHAR(200) NOT NULL     -- German, shown in the panel
  roles           VARCHAR(200) NOT NULL     -- comma list of roles that had live processes at request time, in restart order
  finished_at     DATETIME NULL
  failed_message  TEXT NULL                 -- German; set when the sequence stopped
```

Settings (`backend/src/tiqora/config.py`, append after the phase-1 plugin settings):

```python
plugins_dir: str = Field(default="/var/lib/tiqora/plugins", validation_alias="TIQORA_PLUGINS_DIR")
plugins_runtime_install: bool = Field(default=True, validation_alias="TIQORA_PLUGINS_RUNTIME_INSTALL")
plugins_allow_unsigned: bool = Field(default=False, validation_alias="TIQORA_PLUGINS_ALLOW_UNSIGNED")
plugins_trusted_keys: str = Field(default="", validation_alias="TIQORA_PLUGINS_TRUSTED_KEYS")   # comma list "ed25519:<b64>"
plugins_max_package_mb: int = Field(default=20, ge=1, le=25, validation_alias="TIQORA_PLUGINS_MAX_PACKAGE_MB")
plugins_coordinated_restart: bool = Field(default=True, validation_alias="TIQORA_PLUGINS_COORDINATED_RESTART")
plugins_restart_timeout_seconds: int = Field(default=180, ge=30, le=1800,
                                             validation_alias="TIQORA_PLUGINS_RESTART_TIMEOUT_SECONDS")
plugin_repositories: str = Field(default="", validation_alias="TIQORA_PLUGIN_REPOSITORIES")      # JSON list (D8)
plugin_repository_file_root: str = Field(default="/var/lib/tiqora/plugin-repository",
                                         validation_alias="TIQORA_PLUGIN_REPOSITORY_FILE_ROOT")
plugin_repositories_interval_seconds: int = Field(default=21600, ge=900, le=604800,
                                                  validation_alias="TIQORA_PLUGIN_REPOSITORIES_INTERVAL_SECONDS")
@property
def plugins_max_package_bytes(self) -> int    # plugins_max_package_mb * 1024 * 1024
```

`le=25` keeps every accepted package under the global `body_size_limit_middleware` cap (`MAX_ATTACHMENT_BYTES` 25 MiB + 1 MiB framing).

### D2. Package format `.tqplugin`, keys and signatures (Tasks 2, 3)

A `.tqplugin` file is a zip (`ZIP_DEFLATED`) with exactly these members (forward compatibility: other members are allowed only if listed in `CHECKSUMS`; they are ignored):

| Member | Required | Content |
|---|---|---|
| `tiqora-plugin.toml` | yes | byte-identical copy of the manifest inside the wheel (pre-install inspection without unzipping the wheel) |
| `python/<dist>-<ver>-py3-none-any.whl` | yes, exactly one | the plugin wheel; `web/`, `i18n/` and `migrations/` live **inside** the wheel's package dir (as in phase 1), so baked and runtime plugins are the same thing |
| `CHANGELOG.md` | no | shown in the install dialog (≤ 64 KB, plain text) |
| `CHECKSUMS` | yes | see below |
| `SIGNATURE` | no | see below |

`CHECKSUMS` (UTF-8, LF): first line exactly `tqplugin-checksums 1`, then one line per other member except `CHECKSUMS` and `SIGNATURE`: `<64 lowercase hex sha256>  <member name>` (two spaces), sorted by member name, no duplicates, every member listed, every listed member present.

`SIGNATURE` (UTF-8 JSON): `{"format": 1, "alg": "ed25519", "key_id": "<16 hex>", "signature": "<standard base64 of 64 bytes>"}` — the Ed25519 signature over the **exact bytes of `CHECKSUMS`** (zip bytes are not stable across tools; the checksums file is).

Keys: public key text form `ed25519:<standard base64 of the 32 raw bytes>`; `key_id = sha256(raw public key).hexdigest()[:16]`. Private keys are PKCS#8 PEM files written by `tiqora plugins keygen` with mode 0600 (never in the database, never in an image).

Zip reading rules (`read_tqplugin`, all violations → `PackageError` with a German message, nothing written to disk):
- the file is ≤ `plugins_max_package_bytes`; ≤ 64 members in the envelope; ≤ 5 000 members in the wheel; sum of uncompressed sizes ≤ 200 MiB and any single member ≤ 50 MiB (checked from the central directory *and* enforced while reading with a counting reader, because headers can lie); compression ratio of a member ≤ 200;
- member names: no absolute paths, no drive letters, no backslashes, no `..` segment, no empty segment, no NUL; no duplicates (case-insensitively, `zipfile` keeps duplicates); no symlinks (`(external_attr >> 16) & 0o170000 == 0o120000`) and no directory entries with content; envelope names exactly per the table;
- `CHECKSUMS` format and coverage as above; every hash verified while reading.

```python
# backend/src/tiqora/plugins/signing.py
class SignatureError(ValueError): ...                    # German message
class TrustLevel(StrEnum):
    OFFICIAL = "official"; VERIFIED_PUBLISHER = "verified_publisher"; LOCAL = "local"
    UNSIGNED = "unsigned"; INVALID = "invalid"
KEY_PREFIX: Final = "ed25519:"
OFFICIAL_KEYS: Final[Mapping[str, str]] = MappingProxyType({})   # key_id -> "ed25519:…" (Assumption 1)
def parse_public_key(text: str) -> Ed25519PublicKey                 # SignatureError on bad prefix/base64/length
def public_key_text(key: Ed25519PublicKey) -> str
def key_id(key: Ed25519PublicKey) -> str
def generate_keypair() -> tuple[bytes, str]                         # (PKCS#8 PEM private, "ed25519:…" public)
def load_private_key(pem: bytes) -> Ed25519PrivateKey
def sign_bytes(private: Ed25519PrivateKey, data: bytes) -> bytes    # the SIGNATURE / index.json.sig JSON bytes
def parse_signature(raw: bytes) -> tuple[str, bytes]                # (key_id, signature); SignatureError
def verify_signature(raw_sig: bytes, data: bytes, key: Ed25519PublicKey) -> None   # SignatureError("Signatur ungültig")
def local_keys(settings: Settings) -> dict[str, Ed25519PublicKey]   # from plugins_trusted_keys; bad entries logged + skipped
@dataclass(frozen=True, slots=True)
class PublisherKey:
    key_id: str; public_key: Ed25519PublicKey; publisher_id: str; publisher_name: str
    repository_id: int; revoked: bool
@dataclass(frozen=True, slots=True)
class KeyRing:
    official: Mapping[str, Ed25519PublicKey]
    local: Mapping[str, Ed25519PublicKey]
    publishers: Sequence[PublisherKey]           # only from VERIFIED indexes (D7)
@dataclass(frozen=True, slots=True)
class TrustResult:
    level: TrustLevel; key_id: str | None; signer: str | None   # signer: display text, German
    problem: str | None                                         # German; set for INVALID and for "unknown key"
def resolve_trust(signature: bytes | None, checksums: bytes, *, plugin_id: str,
                  manifest_publisher_id: str | None, ring: KeyRing) -> TrustResult
```

### D3. Trust levels (Tasks 2, 6)

`resolve_trust` rules, first match wins:

1. No `SIGNATURE` → `UNSIGNED`, problem `None`.
2. `SIGNATURE` unparsable → `INVALID` ("Signatur ist beschädigt").
3. `key_id` in `ring.official` → verify → `OFFICIAL` (signer "Tiqora"), or `INVALID` ("Signatur ungültig") when verification fails.
4. `key_id` in `ring.local` → verify → `LOCAL` (signer "Eigener Schlüssel (…)"), or `INVALID`.
5. `key_id` matches a `PublisherKey`: verify first (fail → `INVALID`); then if `revoked` → `INVALID` ("Schlüssel des Herausgebers wurde zurückgezogen"); else if `manifest_publisher_id == key.publisher_id` and `plugin_id.startswith(key.publisher_id + ".")` → `VERIFIED_PUBLISHER` (signer = publisher name) — **Assumption 4**; else → `UNSIGNED` with problem "Signiert mit einem Schlüssel, der nicht zu diesem Herausgeber gehört".
6. Unknown key → verify impossible → `UNSIGNED` with problem "Signiert mit einem unbekannten Schlüssel" (**Assumption 2**).

Installability: `INVALID` → never. `UNSIGNED` → only with `plugins_allow_unsigned` (**Assumption 2**). Image plugins have no package; their `signature_status` stays `'unsigned'` in the row, and the UI shows the source "Im Image eingebaut" instead of a trust chip.

Admin texts (D13): official "Offiziell", verified_publisher "Verifizierter Herausgeber", local "Lokal signiert", unsigned "Unsigniert", invalid "Signatur ungültig".

### D4. Wheel checks, install checks and the package report (Tasks 3, 5, 6)

Wheel rules (`read_tqplugin` → `WheelInfo`):
- filename `<dist>-<ver>(-<build>)?-<python tag>-none-any.whl` with python tag `py3` or `py2.py3`; `<dist>-<ver>.dist-info/WHEEL` has `Root-Is-Purelib: true`; no member below `<dist>-<ver>.data/` other than `.data/purelib/` (rejected too in this version — **simplest safe rule:** any `.data/` dir → "Paket enthält Installationsdaten außerhalb von Python – bitte als Image einbauen"); no member ending in `.so`, `.pyd`, `.dll`, `.dylib`, `.exe`; `RECORD` lists every member (except itself) with `sha256=<urlsafe b64 nopad>` and size, and every hash matches.
- top-level names = first path segment of every member that is not `*.dist-info/`; `*.pth` files at top level are rejected ("Startdateien (.pth) sind nicht erlaubt").
- the wheel's dist-info `entry_points.txt` has exactly one `tiqora.plugins` entry whose name equals the manifest id; the package dir holding `tiqora-plugin.toml` is a top-level package; that toml is byte-identical to the envelope's; manifest `version` == wheel version (packaging `Version` equality).

Install checks (`install_checks.py`, Task 5):

```python
def requirement_problems(requires_dist: Sequence[str],
                         installed: Mapping[str, str] | None = None) -> list[str]
    # installed: canonical name -> version (default: importlib.metadata.distributions() of THIS process).
    # Each Requires-Dist line: markers evaluated for this interpreter with extra="" (lines that only apply to
    # an extra are skipped); tiqora / tiqora-sdk -> "verlangt „tiqora“ als Abhängigkeit; Tiqora bringt das selbst mit";
    # missing -> "benötigt das Python-Paket „x“, das in diesem Tiqora nicht enthalten ist";
    # version outside the specifier -> "benötigt „x“ in Version …, enthalten ist …".
    # Non-empty -> the report adds the hint "Bitte als eigenes Image einbauen (tiqora plugins bake)."
def collision_problems(top_level: Sequence[str],
                       taken: Mapping[str, Sequence[str]] | None = None) -> list[str]
    # taken: importlib.metadata.packages_distributions() of this process (minus the plugin's own runtime
    # copy when updating); also sys.stdlib_module_names; "tqp" prefix is fine. Collision ->
    # "Python-Modul „x“ gibt es in Tiqora schon (Paket y)".
def migration_tables(files: Mapping[str, bytes]) -> list[str]
    # AST scan of migrations/*.py: first str argument of op.create_table(...) calls -> sorted unique names
def static_warnings(files: Mapping[str, bytes], *, short_id: str) -> list[str]
    # AST scan of every .py of the plugin package (≤ 2 MB total scanned, rest skipped with one warning):
    #  - string literals containing SQL keywords (INSERT INTO|UPDATE|DELETE FROM|ALTER TABLE|DROP TABLE|CREATE TABLE)
    #    followed by a name that is a known Znuny table (tiqora.db.znuny table names) -> "Greift direkt per SQL
    #    auf die Znuny-Tabelle „ticket“ zu" (warning, not blocking: phase-1 Deviation 11 resolved here)
    #  - assignments to attributes of imported tiqora.* / tiqora_sdk modules (monkeypatching) and setattr(tiqora…, …)
    #    -> "Verändert Tiqora-Code zur Laufzeit (tiqora.x.y)" (warning)
    #  - op.create_table names not starting with f"tqp_{short_id}_" -> BLOCKING problem
    #    "Tabelle „x“ hat nicht das Präfix tqp_<Kurzname>_" (the migration guard of D6 enforces it again at run time)
```

The report (`inspect.py`, Task 6) — the single source for the install dialog, the upload response, the install job and `tiqora plugins inspect`:

```python
class CapabilityLine(BaseModel):
    kind: Literal["extension_point", "outbound_host", "frontend_slot", "table"]; value: str
class PackageReport(BaseModel):
    sha256: str; size_bytes: int
    plugin_id: str; short_id: str; name: str; version: str; publisher: str; publisher_id: str | None
    description: str; license: str | None; homepage: str | None; support: str | None; categories: list[str]
    changelog: str | None
    trust: TrustLevel; key_id: str | None; signer: str | None
    capabilities: list[CapabilityLine]          # manifest capabilities + one "table" line per migration table
    problems: list[str]                          # German, blocking (any -> installable False)
    warnings: list[str]                          # German, informative (static_warnings, unknown-key note when allowed)
    installed_version: str | None                # currently installed (any source), None if not installed
    action: Literal["install", "update", "reinstall", "downgrade", "blocked_image"]
    installable: bool
def inspect_package(data: bytes, *, settings: Settings, ring: KeyRing,
                    installed: Mapping[str, InstalledPlugin], image_plugin_ids: frozenset[str]) -> PackageReport
    # never raises for package content: unreadable zip -> report-less PackageError (the API maps it to 422);
    # order of problems: envelope/wheel (PackageError aborts) -> compat (check_compat with release_core_version())
    # -> requirements -> collisions -> blocking static checks -> trust -> image id -> downgrade.
    # action: id in image_plugin_ids -> "blocked_image" + problem "ist im Image eingebaut; Aktualisierung über
    # ein neues Image" (Assumption 5); same version -> "reinstall"; higher -> "update"; lower -> "downgrade" +
    # problem (Assumption 7); not installed -> "install".
@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    plugin_id: str; version: str; source: str; package_sha256: str | None; top_level: tuple[str, ...]
```

`collision_problems` ignores names owned by the plugin's own currently installed runtime copy (an update ships the same top-level package).

### D5. Runtime directory, materialising, activation, read-only fallback (Tasks 4, 10, 11)

Layout below `settings.plugins_dir` (`/var/lib/tiqora/plugins` in the image, owned by `tiqora`):

```
<plugins_dir>/<plugin_id>/<version>-<sha256[:12]>/site/…      # the unpacked wheel (purelib layout incl. .dist-info)
<plugins_dir>/<plugin_id>/<version>-<sha256[:12]>/COMPLETE     # written last; content = full sha256
<plugins_dir>/.tmp-<uuid>/                                     # extraction in progress (renamed into place)
<plugins_dir>/.probe-<uuid>                                    # write probe (created + deleted)
```

```python
# backend/src/tiqora/plugins/runtime.py
@dataclass(frozen=True, slots=True)
class DirStatus:
    usable: bool; writable: bool; reason: str | None          # reason German, e.g. "Verzeichnis … ist schreibgeschützt"
def plugins_dir_status(settings: Settings) -> DirStatus
    # usable = plugins_runtime_install and dir exists and is a directory; writable = a probe file can be created
    # and deleted. Cached per process for 60 s (the admin list calls it on every load).
def version_dir(settings: Settings, plugin_id: str, version: str, sha256: str) -> Path
def materialize(settings: Settings, plugin_id: str, version: str, sha256: str, data: bytes) -> Path
    # idempotent: COMPLETE with the same sha -> return site dir; else read_tqplugin(data) again (never trust
    # the disk), unpack_wheel() into .tmp-<uuid>/site, write COMPLETE, os.replace(tmp, final);
    # a concurrent winner (final exists with COMPLETE) -> delete own tmp, return final. Returns the site dir.
def activate_paths(site_dirs: Iterable[Path]) -> list[Path]
    # sys.path.APPEND (never insert: a plugin can never shadow a core module), skip already present,
    # importlib.invalidate_caches(), clear_discovery_cache()
async def activate_runtime_plugins(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> RuntimeActivation
    # rows = tiqora_plugin with source in ("upload","repository") and package_sha256 not NULL.
    # image ids = entry_points(group=ENTRY_POINT_GROUP) names BEFORE any path is added.
    # per row: id in image ids -> set source="image", package_sha256=NULL, repository_id=NULL (commit), log
    #   "plugin_runtime_package_shadowed" (Assumption 5), skip;
    #   COMPLETE present with the right sha -> path; else if writable -> load blob (packages_store.get_package_data),
    #   materialize; blob missing or unreadable -> problem "Paketdateien fehlen – bitte erneut installieren";
    #   not writable and not present -> problem "Paketdateien fehlen im Verzeichnis … (schreibgeschützt)".
    # missing tables (is_missing_table_error) -> empty activation, no error (worker before api migrated).
    # finally activate_paths(); never imports plugin code.
@dataclass(frozen=True, slots=True)
class RuntimeActivation:
    site_dirs: dict[str, Path]          # plugin_id -> active site dir
    problems: dict[str, str]            # plugin_id -> German problem (shown as INVALID on the admin page)
def gc_runtime_dirs(settings: Settings, keep: Mapping[str, Sequence[str]]) -> int
    # keep: plugin_id -> sha256s to keep (current + previous installed package); removes other version dirs,
    # dirs of ids without a row, and .tmp-* older than 1 h; only when writable; returns removed count
```

Bootstrap integration (Task 11): `_load_plugins` calls `activate_runtime_plugins` **before** `discovered_plugins()`; a plugin with an activation problem is recorded `INVALID` with that problem (it is not discovered at all otherwise, so `_load_plugins` adds a synthetic `DiscoveredPlugin(plugin_id, entry="", dist_name="", dist_version=row.version, package_dir=None, manifest=None, web_manifest=None, locales=(), problem=…)` for it). Safe mode stays DB-free in `init_registry`; the admin list (`_build_all`) and the uninstall job call `activate_runtime_plugins` themselves (paths only, no import), so a runtime plugin that crashes every process can be found and removed in safe mode. `tiqora plugins list|verify` and `tiqora migrate upgrade` activate too (best effort; without DB they see image plugins only).

Read-only root fallback: when `plugins_dir_status().usable` is false the admin page says "Installation über die Oberfläche ist hier nicht möglich: <reason>. Erweiterungen kommen über ein eigenes Image (tiqora plugins bake)." and every install/fetch endpoint answers 409 with that text; image plugins work as in phase 1. When usable but this process cannot write, already materialised packages still load.

Unpacking (`package.unpack_wheel(wheel_bytes, target: Path)`): re-validates (D4 rules) and writes members with `open(…, "xb")` below `target` only (resolved path must be relative to `target`), file mode 0o644, dirs 0o755, never preserving zip permissions; `.dist-info/RECORD` is kept; an `INSTALLER` file with `tiqora` is added.

### D6. Plugin migrations (Tasks 8, 9, 16)

**One Alembic environment per plugin, not a branch in the core graph** (Deviation 4). The core version table `tiqora_alembic_version` never sees a plugin revision, so removing or losing a plugin can never break `tiqora migrate upgrade` (Alembic fails on any revision in its version table it cannot locate).

- Plugin package layout: `<package>/migrations/*.py` (normal Alembic revision files; `from alembic import op`, `import sqlalchemy as sa`), first revision `down_revision = None`; revision ids are free strings ≤ 32 chars (the column limit). No `env.py` in the plugin: Tiqora's shared environment is used.
- Version table: `tqp_<short_id>_alembic_version` — inside the plugin's own prefix, so "Daten löschen" removes it with the rest.
- Table names: `tqp_<short_id>_<name>`, ≤ 63 chars (PostgreSQL identifier limit); index/constraint/PostgreSQL type names also start with `tqp_<short_id>_` (MariaDB index names are per table, but PostgreSQL's are per schema).
- Compatibility with the core schema is expressed through the manifest `tiqora`/`sdk` ranges (there is no cross-graph `down_revision`).

```python
# backend/src/tiqora/plugins/migrations.py
class PluginMigrationError(RuntimeError): ...       # German message
def migrations_dir(plugin: DiscoveredPlugin) -> Path | None     # package_dir / "migrations" if it has *.py
def plugin_alembic_config(plugin: DiscoveredPlugin, *, database_url: str) -> Config
    # Config() without ini; script_location = tiqora/plugins/alembic_env; version_locations = migrations dir;
    # attributes: {"short_id", "plugin_id", "database_url", "version_table"}
def plugin_migration_head(plugin: DiscoveredPlugin) -> str | None     # ScriptDirectory heads; >1 head -> PluginMigrationError
def upgrade_plugin(plugin: DiscoveredPlugin, *, database_url: str) -> str | None
    # sync; MUST be called with no running event loop in the thread (env.py uses asyncio.run, see
    # tiqora.cli.migrate._resolve_ownership); returns the head reached (None without migrations)
def downgrade_plugin_to_base(plugin: DiscoveredPlugin, *, database_url: str) -> None   # not used by the UI; CLI only
async def record_migration(factory, plugin_id: str, *, version: str, head: str | None,
                           error: str | None, now: datetime) -> None     # tiqora_plugin.migrated_* / migration_error
async def plugin_tables(session: AsyncSession, short_id: str) -> list[str]   # inspector, names starting f"tqp_{short_id}_"
async def export_plugin_data(session: AsyncSession, short_id: str, *, max_bytes: int) -> bytes   # gzip JSONL
async def drop_plugin_data(session: AsyncSession, short_id: str) -> list[str]
    # reflect only the prefixed tables, MetaData.drop_all in FK order; PostgreSQL: DROP TYPE for pg_type names
    # with the prefix; returns dropped names; never touches anything else (asserted against the prefix again)
```

Shared environment `tiqora/plugins/alembic_env/env.py`: like `backend/alembic/env.py` (async engine from `attributes["database_url"]` normalised with `_normalize_url`, `NullPool`), `context.configure(connection, target_metadata=None, version_table=attributes["version_table"], transaction_per_migration=False)`, and **before** `run_migrations()`:

1. `names_before = inspect(connection).get_table_names()`.
2. `event.listen(connection, "before_cursor_execute", StatementGuard(short_id))` — `StatementGuard.__call__(conn, cursor, statement, parameters, context, executemany)` normalises the statement (strip comments, collapse whitespace, remove `` ` `` and `"` quoting, lower-case) and allows exactly:
   - `select …`, `show …`, `set …` (MariaDB session settings), `with … select …`;
   - `create [unique] index <name> on <table>` / `drop index [if exists] <name> [on <table>]` with prefixed name and table;
   - `create table [if not exists] <t>`, `alter table <t> …`, `drop table [if exists] <t>[, <t>…]`, `rename table <a> to <b>` (MariaDB), `alter table <a> rename to <b>`, `comment on (table|column) <t>…`, `truncate [table] <t>`, `insert into <t>`, `update <t>`, `delete from <t>`, PostgreSQL `create type <t>` / `drop type [if exists] <t>` / `create sequence <t>` / `drop sequence <t>` — every named table/type/sequence must start with `tqp_<short_id>_`;
   - any `references <t>` inside the statement must name a prefixed table (no foreign key into Znuny or `tiqora_*`);
   - everything else raises `PluginMigrationError("Anweisung in Datenbank-Änderung der Erweiterung nicht erlaubt: <first 80 chars>")` before the statement is sent.
3. After `run_migrations()`: `names_after - names_before` must all carry the prefix, else `PluginMigrationError` (defence in depth against a statement form the guard misparsed).

Transactions: on PostgreSQL the whole run is one transaction and a guard error rolls everything back. On MariaDB DDL commits implicitly, so tables created before the failing statement stay; the error is recorded and the plugin is not loaded until a fixed version migrates (documented: "unter MariaDB kann eine abgebrochene Änderung halbe Tabellen hinterlassen").

When migrations run:
- **Boot (image and runtime plugins):** `tiqora migrate upgrade` (the api entrypoint) first runs the core upgrade exactly as today, then — new — `asyncio.run(activate_runtime_plugins(...))`, then for every discovered plugin without `problem`, with `check_compat(...) is None`, with a `migrations/` dir, and whose `tiqora_plugin.migrated_version` differs from its version or has `migration_error`: `upgrade_plugin` in a `try/except Exception` → `record_migration(...)` (success: error NULL; failure: German text ≤ 2000). A failing plugin never fails the command (exit 0), and other plugins still migrate (**Assumption 14**: disabled plugins included).
- **Runtime install/update:** the install job runs `python -m tiqora.main plugins migrate <plugin_id> --path <site_dir>` as a subprocess (timeout 900 s) — a job handler runs inside the worker's event loop, where `asyncio.run` in env.py is impossible, and the subprocess also isolates plugin migration code.
- **Load gate (bootstrap, Task 9):** before importing a plugin that has a `migrations/` dir, `_load_plugins` requires `row.migrated_version == manifest.version and row.migration_error is None`; otherwise `FAILED` with "Datenbank-Änderungen der Erweiterung stehen aus oder sind fehlgeschlagen: <migration_error or 'noch nicht ausgeführt'>" (the boot reads the row it already loaded in `prepare_boot` — `BootInfo` gains `migrated_version: str | None` and `migration_error: str | None`; no Alembic call at boot). Only the api container migrates, so worker/ai-worker/mcp must start after the api is healthy (compose `condition: service_healthy`, Task 25, **Assumption 17**); a process that booted earlier keeps such a plugin FAILED until its next restart (the coordinated restart after an install covers runtime installs).

### D7. Repository index format v1 (Task 6; fetching is D8)

A repository is a URL of an `index.json`; next to it `index.json.sig` (same JSON format as `SIGNATURE`, over the exact bytes of `index.json`). Package URLs inside are relative to the index URL or absolute (`https://` for https repositories; for `file://` repositories only relative URLs, resolved below the index's directory). Marketplace-ready per spec §8a: everything a marketplace needs is in the format; unused fields may be empty; unknown fields are ignored (forward compatibility).

```json
{
  "index_version": 1,
  "repository": {"id": "org.example.plugins", "name": "Example Erweiterungen",
                 "homepage": "https://plugins.example.org", "generated_at": "2026-10-12T08:00:00Z"},
  "publishers": [
    {"id": "org.example", "name": "Example GmbH", "homepage": "https://example.org",
     "support": "support@example.org",
     "keys": [{"key_id": "1a2b3c4d5e6f7a8b", "public_key": "ed25519:…", "revoked": false}]}
  ],
  "plugins": [
    {"id": "org.example.hello", "short_id": "hello", "name": "Hallo-Erweiterung", "publisher_id": "org.example",
     "description": "Zeigt eine Begrüßung in der Ticketansicht.", "categories": ["tickets"],
     "license": "MIT", "homepage": "https://example.org/hello", "support": "support@example.org",
     "icon_url": null, "screenshots": [],
     "versions": [
       {"version": "1.1.0", "url": "packages/org.example.hello-1.1.0.tqplugin",
        "sha256": "<64 hex>", "size": 48213, "sdk": ">=0.2,<0.3", "tiqora": ">=0.19",
        "requires": {}, "released_at": "2026-10-01T00:00:00Z", "changelog": "Neu: …", "yanked": false}
     ]}
  ]
}
```

```python
# backend/src/tiqora/plugins/repo_index.py   (pydantic, extra="ignore" everywhere)
class IndexKey(BaseModel): key_id: str; public_key: str; revoked: bool = False
class IndexPublisher(BaseModel): id: str; name: str; homepage: str | None = None; support: str | None = None; keys: list[IndexKey] = []
class IndexVersion(BaseModel):
    version: str; url: str; sha256: str; size: int; sdk: str; tiqora: str | None = None
    requires: dict[str, str] = {}; released_at: datetime | None = None; changelog: str | None = None; yanked: bool = False
class IndexPlugin(BaseModel):
    id: str; short_id: str; name: str; publisher_id: str | None = None; description: str = ""
    categories: list[str] = []; license: str | None = None; homepage: str | None = None
    support: str | None = None; icon_url: str | None = None; screenshots: list[str] = []; versions: list[IndexVersion]
class IndexRepository(BaseModel): id: str; name: str; homepage: str | None = None; generated_at: datetime
class RepositoryIndex(BaseModel):
    index_version: Literal[1]; repository: IndexRepository
    publishers: list[IndexPublisher] = []; plugins: list[IndexPlugin] = []
class IndexFormatError(ValueError): ...   # German message (not "IndexError": that shadows the builtin)
MAX_INDEX_BYTES: Final = 5 * 1024 * 1024
def parse_index(raw: bytes) -> RepositoryIndex
    # size cap, UTF-8 JSON, model validation; ids via PLUGIN_ID_RE, short ids via SHORT_ID_RE, versions parse,
    # specifiers parse, sha256 64 hex, key_id 16 hex and == key_id(parse_public_key(public_key)),
    # every plugin.publisher_id exists in publishers, duplicate plugin ids/versions rejected
def verify_index(raw: bytes, raw_sig: bytes | None, public_key: str | None) -> bool
    # True = verified with the repository's configured key; no key configured -> False (Assumption 3);
    # key configured but sig missing/invalid -> IndexFormatError("Signatur der Paketquelle ungültig") (refused)
def resolve_package_url(index_url: str, package_url: str) -> str     # urljoin; scheme rules above; ".." escaping file root refused later
def newest_compatible(plugin: IndexPlugin, *, core: Version | None, sdk: str) -> IndexVersion | None
    # highest non-yanked version whose sdk/tiqora specifiers contain the running versions
def publisher_keys(index: RepositoryIndex, repository_id: int) -> list[PublisherKey]   # only call for verified indexes
```

### D8. Repositories: configuration, fetching, refresh, updates (Tasks 17–19)

- **Configured by admins** in the UI (`origin = "admin"`, `https://` only) or by env for air-gapped sites (`origin = "env"`, read-only in the UI): `TIQORA_PLUGIN_REPOSITORIES='[{"name": "Intern", "url": "https://plugins.example.org/index.json", "public_key": "ed25519:…"}, {"name": "Offline", "url": "file:///var/lib/tiqora/plugin-repository/index.json"}]'`. `file://` URLs are accepted only from env and only when the resolved path is below `plugin_repository_file_root` (**Assumption 9**). Nothing is preconfigured (**Assumption 11**).
- `sync_env_repositories(session, settings, now)` upserts env entries by `url_key`, disables (never deletes) env rows that left the env, never touches admin rows; called by every refresh tick and at the start of the repositories admin list.
- **Fetching** (`fetch_bytes(url, *, max_bytes, timeout=30)`): `https://` via `pin_outbound_url(url, require_https=True, allow_private_networks=True)` (the AI-provider pattern for admin-entered URLs; loopback/link-local/metadata stay blocked — **Assumption 9**), `httpx.AsyncClient` against `pinned.request_url` with `pinned.request_headers()` and `pinned.request_extensions()`, `follow_redirects=False` (a 3xx is an error "Weiterleitungen werden nicht verfolgt"), streamed with a byte cap; DNS resolution through `asyncio.to_thread` (R-ASYNC-RESOLVE). `file://` via `Path.resolve()` + `is_relative_to(file_root)` + size cap, read in a thread.
- **Refresh** (`refresh_repository(session, repo, *, settings, now)`): fetch `index.json` (≤ `MAX_INDEX_BYTES`) and `index.json.sig` (404 → `None`), `verify_index`, `parse_index`; refuse an index whose `repository.generated_at` is older than the stored `index_generated_at` ("Paketquelle liefert einen älteren Stand als zuletzt – abgewiesen") — rollback guard; store `index_json`, `index_sha256`, `index_generated_at`, `index_verified`, `last_ok_at`; on any error keep the last good index and store `last_error` (German) + `last_checked_at`.
- **Daemon** `plugin_repositories` (worker loop, `DaemonService(slug="plugin_repositories", enabled_key=None, default_enabled=True, toggleable=False, schedule_kind="interval", interval_settings_attr="plugin_repositories_interval_seconds", tick_timeout_seconds=600)`): `sync_env_repositories`; no enabled repositories → result `{"repositories": 0}` without any network access; else refresh each enabled repository (errors per repository, never fatal); then `packages_store.purge_stale_packages(older_than=24 h)` (never installed → deleted; **Assumption 10**) and `gc_runtime_dirs` when writable; result `{"repositories": n, "failed": k, "updates_available": u}`.
- **Key ring** (`build_key_ring(session, settings) -> KeyRing`): official + `local_keys(settings)` + publisher keys of every enabled repository with `index_verified = true`.
- **Catalog** (`catalog(session, *, settings, q, category, repository_id) -> CatalogOut`): from stored indexes only (no network): one entry per (repository, plugin id), `latest_version`, `latest_compatible` (via `newest_compatible` with `release_core_version()` and `SDK_VERSION`), `installed_version`, `update_available`, `image_plugin` (id baked into the image), `publisher_name`, `publisher_verified` (index verified and plugin id under the publisher id), categories union for the filter; `q` matches id, name, description, publisher name case-insensitively.
- **Updates** (`available_updates(session, *, settings) -> dict[str, UpdateInfo]`): for each installed plugin (any source) the highest compatible non-yanked version greater than the installed one in any enabled repository **whose index entry has the same `publisher_id` as the installed manifest** (a different publisher claiming the same id is ignored); `UpdateInfo(version, repository_id, repository_name, image_plugin: bool)`. Image plugins get `image_plugin = True` (info only: "Neue Version in der Paketquelle – bitte das Image neu bauen").
- **Download** (job `core.plugin_fetch`, D9): resolve the version in the stored index, `fetch_bytes(package_url, max_bytes=plugins_max_package_bytes)`, sha256 must equal the index entry ("Prüfsumme stimmt nicht mit der Paketquelle überein" → job fails, nothing stored), `size` must match, then the upload path (`inspect_package` + `packages_store.store_package(source="repository", repository_id=…)`).

### D9. Jobs: install, uninstall, fetch, refresh (Tasks 15, 16, 18, 19)

All four handlers: lane `"plugins"` (one slot — strictly one plugin operation at a time, in id order), `max_attempts=1`, `requeue_if_interrupted=False`, `result_private=False` (results hold no scoped data; the export file is creator-only anyway), registered in `register_core`. Title keys `jobs.kinds.core.plugin_install` etc. Payload models:

```python
class PluginInstallPayload(BaseModel):
    package_sha256: str; enable: bool = True; restart: bool = True
class PluginUninstallPayload(BaseModel):
    plugin_id: str; delete_data: bool = False; restart: bool = True
class PluginFetchPayload(BaseModel):
    repository_id: int; plugin_id: str; version: str
class PluginRepositoryRefreshPayload(BaseModel):
    repository_id: int
```

`core.plugin_install` (timeout 1500 s; dedupe key `plugin:<plugin_id>`):
1. Load the package row (missing → `JobFailed("Paket nicht mehr vorhanden – bitte erneut hochladen")`). Re-run `inspect_package` on the stored bytes with the current key ring and installed state (trust may have changed since upload); not `installable` → `JobFailed(problems joined)`. `plugins_dir_status` not usable/writable in this process → `JobFailed(reason)`.
2. `progress(1, 4, "Paket wird ausgepackt")` → `materialize(...)` → site dir.
3. `progress(2, 4, "Paket wird geprüft")` → subprocess `sys.executable -m tiqora.main plugins verify --import --strict --path <site_dir> --only <plugin_id>` (timeout 120 s, `TIQORA_PLUGINS_SAFE_MODE` unset in the child env); exit ≠ 0 → `JobFailed("Prüfung fehlgeschlagen: <last 20 stderr lines, ≤ 2000 chars>")`.
4. `checkpoint("migrate")` → `progress(3, 4, "Datenbank wird angepasst")` → subprocess `… plugins migrate <plugin_id> --path <site_dir>` (timeout 900 s; the child records `migrated_*`/`migration_error` itself); exit ≠ 0 → `JobFailed("Datenbank-Änderungen fehlgeschlagen: …")` — the previously installed version stays active and keeps running (**Assumption 12** for the schema it now sees).
5. One write transaction: `tiqora_plugin` upsert (`name`, `version`, `short_id`, `source` = package source, `package_sha256`, `repository_id`, `signature_status`, `signer`, `installed_by` = job creator, `updated_at`; `installed_at` only when new; `enabled = payload.enable` for a new row — **Assumption 6** — and unchanged for an update); `tiqora_plugin_package.installed_at = now`; `set_enabled` is not called for updates, so hook cursors stay (R-REENABLE-BACKLOG unaffected). Commit.
6. `progress(4, 4, "Fertig")`; `payload.restart` and `plugins_coordinated_restart` → `request_restart(session, by=creator, reason="Erweiterung „<name>“ <version> installiert")` (an already running restart is fine: the new generation supersedes it, D10); result `{"plugin_id", "version", "action", "restart_generation": int | None, "migrated_head": str | None}`.

`core.plugin_uninstall` (timeout 1500 s; dedupe `plugin:<plugin_id>`):
- Image plugin still present in the image → `JobFailed("Im Image eingebaut – bitte aus plugins.txt entfernen und das Image neu bauen")`; image plugin no longer in the image (row only, `GONE_MESSAGE`) → allowed (removes the row and, with `delete_data`, the data).
- `delete_data`: `short_id` from the row (or manifest) — missing → `JobFailed("Kurzname unbekannt – Daten können nicht zugeordnet werden")`; `export_plugin_data(max_bytes=200 MiB)` → over the cap → `JobFailed("Die Daten der Erweiterung sind größer als 200 MB. Bitte sichern Sie die Datenbank und entfernen Sie die Tabellen tqp_<Kurzname>_* von Hand.")` (**Assumption 13**); else `ctx.add_file(f"{plugin_id}-daten-{date}.jsonl.gz", "application/gzip", data)`, `checkpoint("drop")`, `drop_plugin_data`, delete `tiqora_settings` rows with key prefix `plugins.<id>.` and the plugin's `tiqora_plugin_hook` rows.
- Always: delete `tiqora_plugin_runtime` rows and the `tiqora_plugin` row; mark the plugin's package rows uninstalled (`installed_at = NULL`, purged by the daemon later). Files on disk are removed by `gc_runtime_dirs` at the next boot of each process (running processes may still have the code loaded until they restart).
- Request restart like install; result `{"plugin_id", "deleted_tables": [...], "export_file_id": int | None, "restart_generation": int | None}`.

`core.plugin_fetch` (timeout 600 s): D8 download → result `{"package_sha256": "...", "installable": bool}`.
`core.plugin_repository_refresh` (timeout 300 s; dedupe `repo:<id>`): `refresh_repository` → result `{"ok": bool, "plugins": n, "error": str | None}`.

### D10. Coordinated restart (Tasks 12, 13)

```python
# backend/src/tiqora/plugins/restart.py
ProcessRole = Literal["api", "worker", "mcp", "ai-worker"]
RESTART_ORDER: Final[tuple[ProcessRole, ...]] = ("worker", "ai-worker", "mcp", "api")   # api last: it serves the progress UI
RESTART_EXIT_CODE: Final = 75             # EX_TEMPFAIL: also restarted under `restart: on-failure`
HEARTBEAT_SECONDS: Final = 5
LIVE_WITHIN_SECONDS: Final = 30           # a row with an older heartbeat counts as gone
def restart_roles() -> tuple[ProcessRole, ...]
    # RESTART_ORDER filtered to roles that host plugins: tiqora.plugins.state.PLUGIN_ROLES (+ "ai-worker" only if
    # plugins2a adds it there) — Assumption 15
async def register_process(factory, role: ProcessRole, *, now: datetime) -> str      # instance_id; boot_generation = max(generation) or 0
async def heartbeat(factory, instance_id: str, *, now: datetime) -> None
async def request_restart(session, *, by: int | None, reason: str, now: datetime) -> int
    # new tiqora_restart row; roles = restart_roles() that have a live process now; flush only; returns generation.
    # A newer generation supersedes an unfinished older one (older rows get finished_at = now, no message).
@dataclass(frozen=True, slots=True)
class ProcessRow:
    instance_id: str; role: str; boot_generation: int; heartbeat_at: datetime; exiting_at: datetime | None
@dataclass(frozen=True, slots=True)
class Decision:
    action: Literal["stay", "exit", "finish", "fail"]; message: str | None = None
def decide(me: ProcessRow, target: RestartRow | None, processes: Sequence[ProcessRow], *,
           now: datetime, timeout_seconds: int, enabled: bool) -> Decision
    # pure. "stay" when: not enabled; target None/finished/failed; me.boot_generation >= target.generation;
    # my role not in target.roles.
    # For my role at position i in target.roles: every earlier role r must be DONE = at least one live process of r
    # with boot_generation >= target and no live process of r with boot_generation < target.
    #   If an earlier role is not DONE and (now - max(target.requested_at, last exiting_at of r)) > timeout ->
    #   "fail" with message f"{ROLE_LABEL[r]} ist nach dem Neustart nicht zurückgekommen. Bitte prüfen Sie die
    #   Container; die übrigen Dienste laufen weiter." ; else "stay".
    # Same role: "exit" only if no other live process of my role has exiting_at within LIVE_WITHIN_SECONDS + timeout
    # and has not come back, and my instance_id is the smallest among live old processes of my role.
    # "finish" (only computed by role "api" processes with boot_generation >= target): every role in target.roles DONE.
async def restart_watch_loop(factory, role: ProcessRole, instance_id: str, *, settings: Settings,
                             on_exit: Callable[[], None], stop: asyncio.Event | None = None) -> None
    # every HEARTBEAT_SECONDS: heartbeat; load latest tiqora_restart + live tiqora_process rows; decide();
    # "exit": set exiting_at, commit, log "plugin_restart_exit", set the module flag _REQUESTED (restart_requested() -> True),
    # call on_exit() once and return; "fail": set failed_message (first writer wins), log; "finish": set finished_at.
    # Any DB error: log + continue (never kills the process).
def restart_requested() -> bool
async def restart_status(session, *, now: datetime) -> RestartStatusOut | None
async def unregister_process(factory, instance_id: str) -> None   # best effort at clean shutdown
async def purge_dead_processes(session, *, older_than: datetime) -> int   # rows without heartbeat for 1 h (called by the restart loop of api)
```

Wiring (Task 13): `on_exit` = `os.kill(os.getpid(), signal.SIGTERM)` in api and mcp (uvicorn's graceful shutdown with `timeout_graceful_shutdown`; `tiqora.main` and `run_mcp_server` call `sys.exit(RESTART_EXIT_CODE)` after `uvicorn.run` returns when `restart_requested()`); `stop.set()` in worker and ai-worker (their own graceful paths), then `run_worker`/`run_ai_worker` exit with `RESTART_EXIT_CODE` when `restart_requested()`. With `plugins_coordinated_restart = False` processes register and heartbeat but `decide` always returns "stay" and the status says "Bitte starten Sie Tiqora neu" (**Assumption 8**).

### D11. API surface (Tasks 14–20)

All admin routes: `AdminUser`, CSRF-protected (`/api/v1/admin` already is), German error details, every enqueue endpoint returns `JobAccepted` (202) and the UI polls `/api/v1/jobs/{id}`.

`backend/src/tiqora/api/v1/admin/plugin_packages.py` (`APIRouter(prefix="/plugins", tags=["admin:plugins"])`, registered next to the phase-1 router. No path clash: phase 1 has no `GET /{plugin_id}`, only `PUT /{plugin_id}/enabled` and `POST /{plugin_id}/reset`; the new `POST /{plugin_id}/uninstall` differs from `POST /restart` and `POST /packages` by its suffix; a test pins `POST /plugins/restart` reaching the restart route):

```python
class CapabilityLineOut(BaseModel): kind: Literal["extension_point", "outbound_host", "frontend_slot", "table"]; value: str
class PluginPackageOut(BaseModel):          # = PackageReport fields + upload bookkeeping
    sha256: str; size_bytes: int; plugin_id: str; short_id: str; name: str; version: str
    publisher: str; publisher_id: str | None; description: str; license: str | None; homepage: str | None
    support: str | None; categories: list[str]; changelog: str | None
    trust: Literal["official", "verified_publisher", "local", "unsigned", "invalid"]; key_id: str | None; signer: str | None
    capabilities: list[CapabilityLineOut]; problems: list[str]; warnings: list[str]
    installed_version: str | None; action: Literal["install", "update", "reinstall", "downgrade", "blocked_image"]
    installable: bool; source: Literal["upload", "repository"]; uploaded_at: UtcDateTime
class PluginInstallIn(BaseModel): enable: bool = True; restart: bool = True
class PluginUninstallIn(BaseModel): delete_data: bool = False; restart: bool = True
class RestartRoleOut(BaseModel):
    role: Literal["api", "worker", "mcp", "ai-worker"]; old: int; new: int
    state: Literal["waiting", "restarting", "done", "timeout"]
class RestartStatusOut(BaseModel):
    generation: int; reason: str; requested_at: UtcDateTime; finished_at: UtcDateTime | None
    failed_message: str | None; coordinated: bool; roles: list[RestartRoleOut]
class RestartIn(BaseModel): reason: str | None = None

POST   /api/v1/admin/plugins/packages                    multipart "file" -> PluginPackageOut (201)
       # read_upload_within_limit(file, settings.plugins_max_package_bytes) -> 413; PackageError -> 422 {detail};
       # inspect_package + packages_store.store_package(source="upload") (same sha uploaded again -> existing row, 200)
GET    /api/v1/admin/plugins/packages/{sha256}           -> PluginPackageOut (report re-evaluated for trust/installed state)
DELETE /api/v1/admin/plugins/packages/{sha256}           -> 204; 409 if it is the installed package of a plugin
POST   /api/v1/admin/plugins/packages/{sha256}/install   PluginInstallIn -> JobAccepted (202)
       # 409: runtime install unusable (reason), not installable (problems), an active plugin job for that id
POST   /api/v1/admin/plugins/{plugin_id}/uninstall       PluginUninstallIn -> JobAccepted (202); 404 unknown; 409 image plugin in image
GET    /api/v1/admin/plugins/restart                     -> RestartStatusOut | null (latest generation)
POST   /api/v1/admin/plugins/restart                     RestartIn -> RestartStatusOut (201); 409 "Ein Neustart läuft bereits"
                                                         # when the latest generation is neither finished nor failed
```

`backend/src/tiqora/api/v1/admin/plugin_repositories.py` (`prefix="/plugin-repositories"`, `tags=["admin:plugins"]`):

```python
class RepositoryOut(BaseModel):
    id: int; name: str; url: str; public_key: str | None; enabled: bool; origin: Literal["admin", "env"]
    verified: bool; repository_name: str | None; plugin_count: int
    last_checked_at: UtcDateTime | None; last_ok_at: UtcDateTime | None; last_error: str | None
class RepositoryIn(BaseModel):
    name: str            # 1..200
    url: str             # https:// only, ≤ 1000, no userinfo, no fragment
    public_key: str | None = None    # parse_public_key must accept it
    enabled: bool = True
class CatalogVersionOut(BaseModel):
    version: str; released_at: UtcDateTime | None; changelog: str | None; compatible: bool; yanked: bool
class CatalogEntryOut(BaseModel):
    repository_id: int; repository_name: str; plugin_id: str; name: str; publisher: str | None
    publisher_verified: bool; description: str; categories: list[str]; license: str | None
    homepage: str | None; icon_url: str | None; latest_version: str; latest_compatible: str | None
    installed_version: str | None; update_available: bool; image_plugin: bool; versions: list[CatalogVersionOut]
class CatalogOut(BaseModel): categories: list[str]; entries: list[CatalogEntryOut]
class DownloadIn(BaseModel): plugin_id: str; version: str

GET    /api/v1/admin/plugin-repositories                 -> list[RepositoryOut]   (env sync first)
POST   /api/v1/admin/plugin-repositories                 RepositoryIn -> RepositoryOut (201); 409 same url
PUT    /api/v1/admin/plugin-repositories/{id}            RepositoryIn -> RepositoryOut; 409 origin "env"
DELETE /api/v1/admin/plugin-repositories/{id}            -> 204; 409 origin "env"
POST   /api/v1/admin/plugin-repositories/{id}/refresh    -> JobAccepted (202)
GET    /api/v1/admin/plugin-repositories/catalog         ?q=&category=&repository_id= -> CatalogOut   (declared BEFORE /{id} routes)
POST   /api/v1/admin/plugin-repositories/{id}/download   DownloadIn -> JobAccepted (202); 409 runtime install unusable
```

Changes to `backend/src/tiqora/api/v1/admin/plugins.py` (Task 20; fields with defaults are REQUIRED in TS — every fixture/mocks include them):

```python
class PluginUpdateOut(BaseModel):
    version: str; repository_id: int; repository_name: str; image_plugin: bool
class PluginAdminOut(BaseModel):                       # existing fields unchanged, plus:
    signature_status: Literal["official", "verified_publisher", "local", "unsigned", "invalid"] | None   # None for image
    signer: str | None
    can_uninstall: bool                                # runtime source, or image row gone from the image
    migration_error: str | None
    update: PluginUpdateOut | None
class PluginAdminListOut(BaseModel):                   # existing fields unchanged, plus:
    runtime_install: bool                              # plugins_dir_status().usable and writable in the api
    runtime_install_reason: str | None                 # German when not usable
    allow_unsigned: bool
    max_package_mb: int
    coordinated_restart: bool
```

`PluginCapabilityOut.kind` gains `"table"` (owned here; plugins2a may add new `extension_point` values but no new kinds). `_build_all` calls `activate_runtime_plugins` first (Task 11) and `available_updates` (Task 20); the `restart_required` status message becomes "Änderung wird nach einem Neustart von Tiqora wirksam. Sie können Tiqora unten neu starten." when coordinated restart is on.

Scoped API keys: `/api/v1/admin/*` is already area `admin` in `domain/api_key_scopes.py` — no change; the new routes inherit it.

### D12. Frontend (Tasks 21–24)

- `pluginsApi` gains: `uploadPackage(file: File)`, `getPackage(sha)`, `deletePackage(sha)`, `installPackage(sha, body)`, `uninstall(id, body)`, `restartStatus()`, `requestRestart(reason?)`, `repositories()`, `createRepository(body)`, `updateRepository(id, body)`, `deleteRepository(id)`, `refreshRepository(id)`, `catalog(params)`, `download(repoId, body)`. Upload uses `api.request("POST", …, { body: formData })` — check how `frontend/src/lib/api.ts` handles `FormData` (pattern: the avatar or vCard upload wrapper, `frontend/src/lib/avatarApi.ts`) and copy it.
- `PluginsPage` becomes three `Tabs`: **Installiert** (today's list, plus per card: source chip — "Im Image eingebaut" / "Hochgeladen" / "Aus Paketquelle <Name>" — and trust chip via `trust.ts`, `migration_error` in the details, "Aktualisierung verfügbar: 1.2.0" badge with button "Aktualisieren" (repository update → download job → install dialog) or, for image plugins, the hint text; "Deinstallieren" when `can_uninstall`), **Verfügbar** (catalog with search field + category `SelectField` + repository filter; entry card with name, publisher (+ "Verifizierter Herausgeber" chip), description, version, "Installieren"/"Aktualisieren"/"Installiert"/"Im Image eingebaut"), **Paketquellen** (table of repositories, add/edit dialog with name, address, key; env rows read-only with "Aus der Server-Konfiguration"; "Jetzt prüfen" → refresh job; last check/error). The header keeps the phase-1 trust note (R-TRUST-WORDING) and gets the button "Paket hochladen" (hidden with `runtime_install = false`, replaced by the reason text).
- `InstallDialog` (opened with a `PluginPackageOut`): name, version, publisher, trust chip + signer, action line ("Neu installieren" / "Aktualisieren von 1.0.0 auf 1.1.0" / "Erneut installieren"), changelog (collapsed), capability list in plain words (reuse phase-1 `capabilityText`, plus `table` → "Legt die Tabelle {{name}} an"; for counts > 3 tables "Legt {{count}} Tabellen an" with the names in "Details"), problems (danger box, install button disabled), warnings (warn box), the fixed note `admin.plugins.install.trustNote` ("Erweiterungen laufen mit vollen Rechten in Tiqora. Die Angaben oben beschreiben, was die Erweiterung von sich sagt – sie sind keine Absicherung."), checkboxes "Nach der Installation einschalten" (default on, new installs only) and "Tiqora danach automatisch neu starten" (default on; hidden when `coordinated_restart = false`), confirmation checkbox "Ich vertraue diesem Paket" required for `unsigned`. Install → `JobProgress` for the returned job → on success the `RestartPanel`.
- `UninstallDialog`: radio "Daten behalten" (default) / "Daten löschen" (+ text "Vorher wird eine Sicherung erstellt, die Sie herunterladen können."), restart checkbox; job progress; on success with an export file a download link (existing job file download URL `/api/v1/jobs/{id}/files/{fileId}`).
- `RestartPanel`: polls `restartStatus()` every 2 s while not finished/failed (network errors while the api restarts are expected: show "Weboberfläche startet neu …" and keep polling up to the timeout), one row per role with the phase-1 role labels and state; finished → "Tiqora wurde neu gestartet." + button "Seite neu laden" (`window.location.reload()`, needed so the new plugin bundles load); failed → the `failed_message`; `coordinated = false` → "Bitte starten Sie Tiqora neu, damit die Änderung wirksam wird." Also a "Jetzt neu starten" button on the page whenever any plugin has `status = "restart_required"`.
- Demo mode: `mockData.ts` answers every new endpoint with fixture data (one image plugin, one uploaded verified-publisher plugin with an update, one repository with three catalog entries, a finished restart); `VITE_DEMO=1` + Playwright check of the three tabs and both dialogs.

### D13. i18n keys (de shown; en alongside; all under `admin.plugins.` unless noted)

`tabs.installed` "Installiert", `tabs.available` "Verfügbar", `tabs.repositories` "Paketquellen"; `upload` "Paket hochladen", `uploadHint` "Datei mit der Endung .tqplugin", `runtimeInstallOff` "Installation über die Oberfläche ist hier nicht möglich: {{reason}} Erweiterungen kommen über ein eigenes Image (tiqora plugins bake)."; `source.image` "Im Image eingebaut", `source.upload` "Hochgeladen", `source.repository` "Aus Paketquelle {{name}}"; `trust.official` "Offiziell", `trust.verified_publisher` "Verifizierter Herausgeber", `trust.local` "Lokal signiert", `trust.unsigned` "Unsigniert", `trust.invalid` "Signatur ungültig", `trust.signedBy` "Signiert von {{signer}}"; `update.available` "Aktualisierung verfügbar: {{version}}", `update.button` "Aktualisieren", `update.imageHint` "Neue Version {{version}} in der Paketquelle – bitte das Image neu bauen."; `install.title` "Erweiterung installieren", `install.actionInstall` "Neu installieren", `install.actionUpdate` "Aktualisieren von {{from}} auf {{to}}", `install.actionReinstall` "Erneut installieren", `install.changelog` "Änderungen", `install.problems` "Kann nicht installiert werden", `install.warnings` "Hinweise", `install.enable` "Nach der Installation einschalten", `install.restart` "Tiqora danach automatisch neu starten", `install.trustUnsigned` "Ich vertraue diesem Paket", `install.trustNote` (text above), `install.submit` "Installieren", `install.running` "Erweiterung wird installiert …", `install.done` "Installiert.", `capabilities.table` "Legt die Tabelle {{name}} an", `capabilities.tables` "Legt {{count}} Tabellen an"; `uninstall.button` "Deinstallieren", `uninstall.title` "Erweiterung deinstallieren", `uninstall.keepData` "Daten behalten", `uninstall.keepDataHint` "Die Tabellen der Erweiterung bleiben erhalten; bei einer neuen Installation sind die Daten wieder da.", `uninstall.deleteData` "Daten löschen", `uninstall.deleteDataHint` "Vorher wird eine Sicherung erstellt, die Sie herunterladen können.", `uninstall.download` "Sicherung herunterladen", `uninstall.submit` "Deinstallieren"; `restart.title` "Neustart", `restart.now` "Jetzt neu starten", `restart.running` "Tiqora wird neu gestartet …", `restart.apiDown` "Weboberfläche startet neu …", `restart.done` "Tiqora wurde neu gestartet.", `restart.reload` "Seite neu laden", `restart.manual` "Bitte starten Sie Tiqora neu, damit die Änderung wirksam wird.", `restart.state.waiting` "Wartet", `restart.state.restarting` "Startet neu", `restart.state.done` "Neu gestartet", `restart.state.timeout` "Nicht zurückgekommen", `roles.aiWorker` "KI-Hintergrunddienst"; `catalog.search` "Suchen", `catalog.category` "Kategorie", `catalog.allCategories` "Alle Kategorien", `catalog.repository` "Paketquelle", `catalog.empty` "Keine Erweiterungen gefunden.", `catalog.noRepositories` "Es ist noch keine Paketquelle eingerichtet.", `catalog.install` "Installieren", `catalog.installed` "Installiert", `catalog.incompatible` "Passt nicht zu dieser Tiqora-Version", `repositories.add` "Paketquelle hinzufügen", `repositories.name` "Name", `repositories.url` "Adresse der Paketliste", `repositories.key` "Öffentlicher Schlüssel der Paketquelle", `repositories.keyHint` "Ohne Schlüssel werden Herausgeber dieser Paketquelle nicht als verifiziert angezeigt.", `repositories.fromEnv` "Aus der Server-Konfiguration", `repositories.check` "Jetzt prüfen", `repositories.lastCheck` "Zuletzt geprüft", `repositories.verified` "Paketliste signiert", `repositories.unverified` "Paketliste nicht signiert", `repositories.delete` "Entfernen"; `jobs.kinds.core.plugin_install` "Erweiterung installieren", `jobs.kinds.core.plugin_uninstall` "Erweiterung deinstallieren", `jobs.kinds.core.plugin_fetch` "Erweiterung herunterladen", `jobs.kinds.core.plugin_repository_refresh` "Paketquelle prüfen" (top-level `jobs.kinds…`); `admin.daemons.services.plugin_repositories.name` "Erweiterungen: Paketquellen prüfen", `…description` "Lädt regelmäßig die Paketlisten der eingerichteten Paketquellen und sucht nach Aktualisierungen."

### D14. CLI (Task 7, plus `migrate` in Task 9 and `verify --path/--only` in Task 15)

`tiqora plugins` gains (all in `backend/src/tiqora/cli/plugins.py`, German errors, exit 2 on usage errors, 1 on failed checks):

- `keygen --out PRIVATE.pem` → writes PKCS#8 PEM (mode 0600, refuses to overwrite), prints `public_key` and `key_id`.
- `pack WHEEL [--changelog FILE] --output FILE.tqplugin` → reads the manifest from the wheel's package dir, writes the envelope with `CHECKSUMS` (unsigned).
- `sign FILE.tqplugin --key PRIVATE.pem [--output OUT]` → adds/replaces `SIGNATURE` (rewrites the zip; default output overwrites only with `--force`).
- `inspect FILE.tqplugin [--json]` → `PackageReport` without DB (installed state empty, key ring = official + env local keys); exit 1 when not installable.
- `index build DIR --repository-id ID --name NAME [--publisher-key ed25519:… --publisher-id ID --publisher-name NAME] --output index.json` → scans `DIR/*.tqplugin`, fills `versions` (sha256, size, sdk/tiqora/requires from the manifest, changelog from `CHANGELOG.md`), relative URLs, `generated_at = now`.
- `index sign index.json --key PRIVATE.pem` → writes `index.json.sig`.
- `migrate PLUGIN_ID [--path SITE_DIR]` → (no running loop) discover that plugin, `upgrade_plugin`, `record_migration`; exit 1 with the German error on stderr.
- `verify … --path SITE_DIR --only PLUGIN_ID` (extension of the phase-1 command).
- **`--path` rule (both commands):** with `--path` the command does **no** DB activation (the currently installed version must not be on `sys.path` next to the new one — discovery would see the id twice and `EntryPoint.load()` could import the old code). Entry points come only from `importlib.metadata.distributions(path=[SITE_DIR])` (passed to `discover_plugins(entry_points_=…)`), the site dir is put at the **front** of `sys.path` in this short-lived child process only (so the new code is what imports), and `--only` fails with exit 1 "Erweiterung <id> nicht im Paket gefunden" when the id is not in that site dir. Without `--path` both commands activate runtime packages best-effort as in Task 11.

### D15. Docs (Tasks 25, 26)

- `docs/plugins.md` Teil 1: new section "Erweiterungen über die Oberfläche installieren" (upload, trust levels, dialog, restart, uninstall with/without data, read-only root → image path, volume), "Paketquellen" (admin + env, `file://` root, keys, rollback guard, update hints), "Schlüssel und Signaturen" (keygen/sign, `TIQORA_PLUGINS_TRUSTED_KEYS`, `TIQORA_PLUGINS_ALLOW_UNSIGNED`); Part 2 (developer, English like today): "Database tables and migrations" (layout, prefix, version table, guard, MariaDB DDL caveat, backward-compatible migrations, hatch artifacts for `migrations/**`), "Packaging and signing" (`.tqplugin` format, `CHECKSUMS`, `SIGNATURE`), "Repository index format v1" (full schema, marketplace fields); Teil 3 "Grenzen": rewrite the four bullets owned here (Cross-plan contracts).
- `docs/deployment.md`: volume `tiqora_plugins` for api/worker/ai-worker/mcp, read-only root, restart policy requirement, exit code 75, all D1 env vars.
- `docs/architecture.md`: plugin install/restart paragraph; remove "Kein Nachinstallieren" statements if any.
- Spec status line: "Phase 2b implemented 2026-…, see plan …".

### Deviations from spec

1. **`.tqplugin` envelope keeps `web/`, `i18n/` and `migrations/` inside the wheel** (spec §2 lists them as top-level zip folders). One layout for baked and runtime plugins, so discovery, asset serving and migrations need one code path; the top-level `tiqora-plugin.toml` is a byte-identical copy for inspection.
2. **No pip at runtime.** The core venv is built by `uv sync` and has no pip; the baked Dockerfile even uninstalls the pip it bootstraps (R-BAKE-SUPPLY d). Runtime install unpacks the pure-Python wheel itself (equivalent to `pip install --no-deps --target` for `py3-none-any` wheels, with RECORD verification) and checks `Requires-Dist` against the distributions installed in the running image (= `core-constraints.txt`).
3. **The database is the source of truth for runtime packages; the volume is a cache.** `tiqora_plugin_package` keeps the package; every process materialises it at boot if missing. Containers on different hosts therefore need no shared volume, a lost volume heals itself, and the install job never depends on the api having written a file.
4. **Plugin migrations use one Alembic environment per plugin with the version table `tqp_<short_id>_alembic_version`, not a branch (`branch_labels=("plugin_<id>",)`) on a fixed core revision.** With a shared version table, a plugin that is removed (or whose files are missing) leaves a revision Alembic cannot locate, and every later `tiqora migrate upgrade` of core fails — a removed plugin would block upgrades. Compatibility with the core schema moves to the manifest `tiqora`/`sdk` ranges.
5. **The signature covers a `CHECKSUMS` member, not "the package hash".** Zip bytes are not stable across tools; the checksum list is, and it covers every member. Repository downloads are additionally pinned by the sha256 of the whole file from the index.
6. **Migrations run at boot by `tiqora migrate upgrade` (image plugins) or in a subprocess of the install job (runtime plugins)**, never inside a running event loop (Alembic's env uses `asyncio.run`). A plugin is loaded only when its recorded migration state matches its version.
7. **Coordinated restart uses a process registry (`tiqora_process`) and requests (`tiqora_restart`)**; processes exit themselves in the order worker → (ai-worker) → mcp → api, one instance at a time, and the sequence **stops** when a process does not come back. Exit code 75 so `restart: on-failure` also restarts.
8. **Image-baked plugins cannot be replaced through the UI** (the image wins; Assumption 5); the spec does not define precedence.
9. **Minimal signing/index tooling in this plan** (`keygen`, `pack`, `sign`, `inspect`, `index build|sign`); the spec puts `build|sign|validate` with the phase-3 dev kit, but signatures and repositories are unusable without it. `tiqora plugin new|dev` and the test kit stay in phase 3.
10. **The phase-2 validator** (spec §3 "flags raw SQL against Znuny tables", monkeypatch warning; phase-1 Deviation 11) is implemented here as static warnings in the package report; only a migration creating a non-prefixed table is blocking.
11. **Update notifications** appear on the plugin page and as a result count of the new "Paketquellen prüfen" row on the Daemons page; the System-Info page is not changed.
12. **`file://` repositories only via env and below one root directory**; the admin UI accepts `https://` only (a `file://` URL entered in the UI could read any file the process can read).
13. **Per-plugin "Daten löschen" also for image plugins that are gone from the image** (their rows and tables stay otherwise); image plugins still in the image are removed by rebuilding the image.

### Cross-plan contracts

| Contract | Owner | Consumers / rule |
|---|---|---|
| Columns added to `tiqora_plugin` and the tables `tiqora_plugin_package`, `tiqora_plugin_repository`, `tiqora_process`, `tiqora_restart` (one migration) | **plugins2b** | plugins2a adds no column to `tiqora_plugin*`; any state plugins2a needs goes into its own `tiqora_*` tables in its own migration. Migration numbering per R-MIGRATION-SERIAL (whoever merges later re-points `down_revision`, `TIQORA_HEAD`, `test_migrate_cli`). |
| `PluginAdminOut` / `PluginAdminListOut` new fields (D11), `PluginCapabilityOut.kind = "table"` | **plugins2b** | plugins2a may add new `extension_point` *values* and their labels `admin.plugins.capabilities.point.<key>` (owned by **plugins2a**); the install dialog renders every key through the same `capabilityText` (fallback: the raw key), so no code dependency. |
| `tiqora_sdk.__all__`, `SDK_VERSION`, `@tiqora/plugin-sdk` | **plugins2a** | plugins2b adds no SDK symbol; `check_compat` uses whatever `SDK_VERSION` exists. The migration contract (D6) is file-based and documented in `docs/plugins.md`. |
| `PluginRole` / `PLUGIN_ROLES` (`tiqora.plugins.state`) | **plugins2a** (if its AI extension points make the ai-worker host plugins, it adds `"ai-worker"`) | `restart.restart_roles()` filters `RESTART_ORDER` by `PLUGIN_ROLES`, so the ai-worker joins the coordinated restart automatically. plugins2b always registers the ai-worker in `tiqora_process` (heartbeat only). |
| `init_registry` / `_load_plugins` in `tiqora.plugins.bootstrap` | shared; **plugins2a merges first** | plugins2b inserts `activate_runtime_plugins` before `discovered_plugins()` and the migration gate after the requirement check (D6), on top of whatever plugins2a changed (e.g. sync-hook registration). |
| `PluginsPage.tsx`, `pluginsApi.ts`, `pluginFixtures.ts`, `mockData.ts` plugin section | **plugins2b** (restructures into tabs) | plugins2a's additions to the page (if any, e.g. new capability labels or a hook list) are kept inside the "Installiert" tab. |
| Validator (raw SQL / monkeypatch warnings, prefix check) | **plugins2b** (`install_checks.static_warnings`, `tiqora plugins inspect`) | plugins2a does not build a second validator. |
| `docs/plugins.md` Teil 3 "Grenzen" | split by bullet | **plugins2b** rewrites: "Kein Nachinstallieren im Betrieb", "Kein Marktplatz, keine Signaturen", "Keine eigenen Datenbanktabellen", "Ausschalten wirkt nach Neustart". **plugins2a** rewrites: "Der KI-Worker lädt keine Erweiterungen", "Kein Schreiben an Tickets über das SDK", "Vier Stellen in der Oberfläche", "Ein Schreiber für Ereignisse". "Keine Sandbox" stays. |
| itsm1, cmdb, itsm-import, scim | — | No functional contract. Shared files only (append-only at named anchors): the migration chain, `config.py`, `api/v1/admin/__init__.py`, `extensions/core.py` (`register_core`), `worker/services.py`, `worker/__main__.py`, i18n `en.json`/`de.json`, `docs/architecture.md`, `docs/deployment.md`, compose files. The Znuny add-on `TiqoraITSM` (itsm-import) is an OPM package for Znuny, not a Tiqora plugin; it does not use this install path. |

**Where this plan depends on plugins2a:** only through merge order on the shared files above (bootstrap, admin plugins API/page, docs, i18n, migration head). No task needs a plugins2a symbol; if plugins2a has not merged, everything here still works with the phase-1 extension points.

### Shared files (coordination)

`backend/alembic/versions_tiqora/*` (head bump), `backend/src/tiqora/db/tiqora/models.py`, `backend/src/tiqora/config.py`, `backend/src/tiqora/extensions/core.py`, `backend/src/tiqora/jobs/registry.py`, `backend/src/tiqora/api/v1/admin/__init__.py`, `backend/src/tiqora/api/v1/admin/plugins.py`, `backend/src/tiqora/api/app.py`, `backend/src/tiqora/main.py`, `backend/src/tiqora/worker/__main__.py`, `backend/src/tiqora/worker/services.py`, `backend/src/tiqora/mcp_server/server.py`, `backend/src/tiqora/ai/worker.py`, `backend/src/tiqora/cli/migrate.py`, `backend/src/tiqora/cli/plugins.py`, `backend/src/tiqora/plugins/bootstrap.py`, `backend/src/tiqora/plugins/store.py`, `backend/src/tiqora/plugins/bake.py`, `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`, `Dockerfile`, compose files, `frontend/src/routes/admin/PluginsPage.tsx`, `frontend/src/lib/pluginsApi.ts`, `frontend/src/demo/mockData.ts`, i18n `en.json`/`de.json` + propagation, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `docs/plugins.md`, `docs/deployment.md`, `docs/architecture.md`. Edits to shared files are append-only at a named anchor or confined to the functions named in the task.

---

## Tasks

Order: 1 (schema) → 2–7 (pure: signing, package, unpack, checks, index/trust/report, CLI) → 8–9 (plugin migrations) → 10–11 (package store, activation) → 12–13 (restart) → 14–20 (API + jobs) → 21–24 (frontend) → 25–26 (infra, docs, full verification). Tasks 2–7 need no database. Each task ends with its lint gate and one commit. Seed-id sub-bands: Task 1 62000–62019, Task 9 62020–62039, Task 10 62040–62059, Task 11 62060–62079, Task 12 62080–62119, Task 13 62120–62139, Task 14 62140–62159, Task 15 62160–62199, Task 16 62200–62239, Task 17 62240–62259, Task 18 62260–62279, Task 19 62280–62299, Task 20 62300–62319 (agent ids for `installed_by`/`uploaded_by`/`created_by`; seeded agents need no Znuny row unless an endpoint resolves them — the admin tests use the existing admin fixture of `test_admin_api.py`).

Test seam used everywhere (Task 3 creates it): `backend/tests/plugins/package_builder.py`

```python
def make_keypair() -> tuple[Ed25519PrivateKey, str, str]          # (private, "ed25519:…", key_id)
def build_wheel(tmp: Path, package: str = "tq_fixture_hello", *, plugin_id: str = "org.example.hello",
                version: str = "1.0.0", dist_name: str | None = None, tag: str = "py3-none-any",
                requires_dist: Sequence[str] = (), extra_members: Mapping[str, bytes] = {},
                drop: Sequence[str] = (), manifest_overrides: Mapping[str, Any] | None = None,
                record_tamper: str | None = None) -> Path
    # copies backend/tests/plugins/fixtures/<package> (same toml rewriting as helpers.install_fixture_plugin),
    # writes <dist>-<ver>.dist-info/{METADATA,WHEEL(Root-Is-Purelib: true),entry_points.txt,RECORD} with real hashes;
    # record_tamper names a member whose RECORD hash is replaced by a wrong one
def build_tqplugin(tmp: Path, wheel: Path, *, sign_with: Ed25519PrivateKey | None = None,
                   changelog: str | None = None, toml_override: bytes | None = None,
                   extra_members: Mapping[str, bytes] = {}, skip_checksum_for: Sequence[str] = ()) -> Path
    # uses tiqora.plugins.package.write_tqplugin for valid packages; the knobs produce invalid ones on purpose
def raw_zip(tmp: Path, members: Sequence[tuple[str, bytes, int]]) -> Path   # (name, data, external_attr) for hand-made attacks
```

### Task 1: Migration, ORM models, settings (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<next free number at implementation time>_plugin_install.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (new columns on `TiqoraPlugin`; classes `TiqoraPluginPackage`, `TiqoraPluginRepository`, `TiqoraProcess`, `TiqoraRestart` after `TiqoraPluginHook`), `backend/src/tiqora/config.py` (D1 settings after `plugin_tasks_interval_seconds`, property after `plugins_disabled_ids`), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version == …`)
- Test: `backend/tests/test_plugins_install_tables.py` (new, both dialects), `backend/tests/test_plugins_install_migration_db.py` (new; upgrade → downgrade → upgrade on both dialects and shape comparison with `create_all`, copied from the pattern of `backend/tests/test_plugins_migration_db.py` incl. `tests._schema_reset`), `backend/tests/test_tiqora_model_ddl.py` (runs as is), `backend/tests/test_config_plugins_install.py` (new, pure)

**Interfaces:**
- Consumes: `JOB_FILE_BLOB`, `MEDIUM_TEXT_VARIANT`, phase-1 `TiqoraPlugin`.
- Produces: D1 tables/columns and ORM classes (`TiqoraPluginPackage.data` is `deferred`); `Settings.plugins_dir`, `plugins_runtime_install`, `plugins_allow_unsigned`, `plugins_trusted_keys`, `plugins_max_package_mb`, `plugins_max_package_bytes`, `plugins_coordinated_restart`, `plugins_restart_timeout_seconds`, `plugin_repositories`, `plugin_repository_file_root`, `plugin_repositories_interval_seconds`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  # test_config_plugins_install.py
  def test_defaults(monkeypatch) -> None:
      s = Settings(_env_file=None)
      assert (s.plugins_dir, s.plugins_runtime_install, s.plugins_allow_unsigned) == ("/var/lib/tiqora/plugins", True, False)
      assert s.plugins_max_package_bytes == 20 * 1024 * 1024 and s.plugins_coordinated_restart is True
      assert s.plugin_repositories_interval_seconds == 21600
  def test_package_cap_never_exceeds_upload_limit(monkeypatch) -> None:
      monkeypatch.setenv("TIQORA_PLUGINS_MAX_PACKAGE_MB", "26")
      with pytest.raises(ValidationError):
          Settings(_env_file=None)
  # test_plugins_install_tables.py (both dialects, create_all on TiqoraBase.metadata)
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_package_blob_roundtrip_and_deferred(url_fixture, request) -> None:
      # insert TiqoraPluginPackage(sha256="a"*64, plugin_id="org.example.hello", version="1.0.0", size_bytes=3,
      #   data=b"\x00\x01\x02" * 1_000_000, manifest="…", report="{}", signature_status="unsigned", source="upload",
      #   uploaded_at=utc_now()); reload: data equals (3 MB > 64 KiB proves LONGBLOB/BYTEA); a plain select of the
      #   row does not load .data (sqlalchemy.inspect(row).unloaded contains "data")
  async def test_plugin_new_columns_nullable(url_fixture, request) -> None:
      # a TiqoraPlugin inserted with only the phase-1 columns reads back short_id/package_sha256/migrated_* = None
  async def test_repository_url_key_unique(url_fixture, request) -> None:
      # two rows with the same url_key -> IntegrityError
  async def test_restart_generation_autoincrements(url_fixture, request) -> None:
      # two TiqoraRestart rows -> generations g1 < g2
  async def test_process_row_roundtrip(url_fixture, request) -> None: ...
  ```
- [ ] **Step 2: Run** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_config_plugins_install.py tests/test_plugins_install_tables.py tests/test_plugins_install_migration_db.py` → FAIL (attributes/tables/migration missing).
- [ ] **Step 3: Implement.** Migration: `op.add_column("tiqora_plugin", …)` per D1 column; `op.create_table` for the four tables with the D1 indexes (`ix_tiqora_plugin_package_plugin_id`, `uq_tiqora_plugin_repository_url_key`, `ix_tiqora_process_role`); `data` column `sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")`, `index_json`/`report` `sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`; booleans with `server_default=sa.false()`; `downgrade()` drops the four tables and the columns. Find the head with `uv run alembic heads` (today `20261010_0073`; the next free file number is `0077`). Update `TIQORA_HEAD` + `test_migrate_cli.py`. ORM mirrors the DDL exactly (`test_tiqora_model_ddl.py`). Settings per D1.
- [ ] **Step 4: Run** the new tests + `tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_plugins_store.py` → PASS; `uv run alembic heads` shows exactly one primary head; lint gates; commit `feat(plugins): tables for packages, repositories and coordinated restarts`.

### Task 2: Keys, signatures and trust resolution (backend, pure)

**Files:**
- Create: `backend/src/tiqora/plugins/signing.py`
- Test: `backend/tests/test_plugins_signing.py`

**Interfaces:**
- Consumes: `cryptography.hazmat.primitives.asymmetric.ed25519`, `Settings.plugins_trusted_keys`.
- Produces: everything in D2 `signing.py` and the D3 rules in `resolve_trust`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  CHECKSUMS = b"tqplugin-checksums 1\n" + b"0" * 64 + b"  tiqora-plugin.toml\n"
  def _ring(**kw) -> KeyRing:
      return KeyRing(official=kw.get("official", {}), local=kw.get("local", {}), publishers=kw.get("publishers", ()))
  def test_key_text_roundtrip_and_key_id() -> None:
      pem, pub = generate_keypair()
      key = parse_public_key(pub)
      assert public_key_text(key) == pub and re.fullmatch(r"[0-9a-f]{16}", key_id(key))
  @pytest.mark.parametrize("bad", ["", "ed25519:", "rsa:AAAA", "ed25519:!!!", "ed25519:" + base64.b64encode(b"x" * 31).decode()])
  def test_parse_public_key_rejects(bad) -> None:
      with pytest.raises(SignatureError): parse_public_key(bad)
  def test_unsigned_without_signature() -> None:
      r = resolve_trust(None, CHECKSUMS, plugin_id="org.example.hello", manifest_publisher_id=None, ring=_ring())
      assert (r.level, r.problem) == (TrustLevel.UNSIGNED, None)
  def test_verify_local_key() -> None:
      priv, pub, kid = _pair()
      sig = sign_bytes(priv, CHECKSUMS)
      r = resolve_trust(sig, CHECKSUMS, plugin_id="org.example.hello", manifest_publisher_id=None,
                        ring=_ring(local={kid: parse_public_key(pub)}))
      assert r.level is TrustLevel.LOCAL and r.key_id == kid
  def test_verify_tampered_checksums_is_invalid() -> None:
      # sign CHECKSUMS, verify against CHECKSUMS + b"x" with the key in ring.official -> INVALID, "Signatur ungültig"
  def test_verify_broken_signature_json_is_invalid() -> None:
      # b"{not json" / {"format": 2, …} / alg "rsa" / signature of 63 bytes -> INVALID "Signatur ist beschädigt"
  def test_verify_unknown_key_is_unsigned_with_problem() -> None:
      # valid signature, key in no ring -> UNSIGNED, problem "Signiert mit einem unbekannten Schlüssel"
  def test_verify_publisher_key_matching_id() -> None:
      # PublisherKey(publisher_id="org.example", …, revoked=False), plugin "org.example.hello",
      # manifest_publisher_id "org.example" -> VERIFIED_PUBLISHER, signer "Example GmbH"
  @pytest.mark.parametrize("plugin_id,publisher_id", [("org.other.hello", "org.example"),
                                                       ("org.examplex.hello", "org.example"),
                                                       ("org.example.hello", None)])
  def test_verify_publisher_key_wrong_namespace_is_unsigned(plugin_id, publisher_id) -> None:
      # -> UNSIGNED with problem "…nicht zu diesem Herausgeber gehört"
  def test_verify_revoked_publisher_key_is_invalid() -> None: ...
  def test_official_wins_over_publisher_entry_with_same_key() -> None: ...
  def test_local_keys_from_settings_skip_bad_entries(caplog) -> None:
      # "ed25519:<good>, garbage , ed25519:<good2>" -> two keys, one warning
  ```
- [ ] **Step 2: Run** `uv run python -m pytest -q tests/test_plugins_signing.py` → FAIL.
- [ ] **Step 3: Implement** D2/D3. `sign_bytes` returns `json.dumps({"format": 1, "alg": "ed25519", "key_id": …, "signature": b64}, separators=(",", ":")).encode()`; `parse_signature` strict (exact keys, `format == 1`, `alg == "ed25519"`, 16-hex key id, 64-byte signature); `verify_signature` maps `InvalidSignature` to `SignatureError`. No logging of key material.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): ed25519 package signatures and trust levels`.

### Task 3: `.tqplugin` envelope and wheel reading (backend, pure) + test package builder

**Files:**
- Create: `backend/src/tiqora/plugins/package.py`, `backend/tests/plugins/package_builder.py`
- Test: `backend/tests/test_plugins_package.py`

**Interfaces:**
- Consumes: `parse_manifest`, `MANIFEST_NAME`, `ENTRY_POINT_GROUP`, `PLUGIN_ID_RE`.
- Produces:
  ```python
  class PackageError(ValueError): ...                   # German
  @dataclass(frozen=True, slots=True)
  class WheelInfo:
      filename: str; dist_name: str; version: str; tag: str; top_level: tuple[str, ...]
      package: str; requires_dist: tuple[str, ...]; entry_value: str
      files: Mapping[str, bytes]        # every wheel member (needed by the static checks); ≤ 200 MiB total
  @dataclass(frozen=True, slots=True)
  class TqPackage:
      sha256: str; size_bytes: int; manifest_text: str; manifest: PluginManifest
      checksums: bytes; signature: bytes | None; changelog: str | None; wheel_bytes: bytes; wheel: WheelInfo
  def read_tqplugin(data: bytes, *, max_bytes: int) -> TqPackage
  def write_tqplugin(wheel: Path, out: Path, *, changelog: str | None = None) -> None   # CHECKSUMS, no SIGNATURE
  def add_signature(package: Path, signature: bytes, out: Path) -> None                 # rewrite with SIGNATURE
  MAX_ENVELOPE_MEMBERS: Final = 64; MAX_WHEEL_MEMBERS: Final = 5000
  MAX_UNCOMPRESSED: Final = 200 * 1024 * 1024; MAX_MEMBER: Final = 50 * 1024 * 1024; MAX_RATIO: Final = 200
  ```
  and the builder of the "Test seam" block above.

- [ ] **Step 1: Write the failing tests** (all with `build_wheel`/`build_tqplugin`/`raw_zip`):
  ```python
  def test_read_valid_package(tmp_path) -> None:
      pkg = read_tqplugin(build_tqplugin(tmp_path, build_wheel(tmp_path)).read_bytes(), max_bytes=MB20)
      assert pkg.manifest.id == "org.example.hello" and pkg.wheel.top_level == ("tq_fixture_hello",)
      assert pkg.signature is None and pkg.sha256 == hashlib.sha256(...).hexdigest()
  @pytest.mark.parametrize("case,needle", [
      ("zip_slip", "Pfad"), ("absolute", "Pfad"), ("backslash", "Pfad"), ("drive", "Pfad"),
      ("symlink", "Verknüpfung"), ("duplicate", "doppelt"), ("duplicate_case", "doppelt"),
      ("unlisted_member", "CHECKSUMS"), ("listed_missing", "CHECKSUMS"), ("bad_hash", "Prüfsumme"),
      ("checksums_header", "CHECKSUMS"), ("two_wheels", "genau ein"), ("no_toml", "tiqora-plugin.toml"),
      ("toml_differs", "stimmt nicht"), ("too_many_members", "zu viele"), ("bomb_ratio", "zu groß"),
      ("bomb_size_lie", "zu groß"),          # header says 10 bytes, stream inflates to 60 MB -> counting reader stops
      ("record_mismatch", "RECORD"), ("native_so", "nativ"), ("tag_cp312", "reines Python"),
      ("data_dir", "Installationsdaten"), ("pth_file", ".pth"), ("not_purelib", "reines Python"),
      ("entry_point_missing", "Einstiegspunkt"), ("version_mismatch", "Version"), ("not_a_zip", "keine gültige"),
  ])
  def test_read_rejects(tmp_path, case, needle) -> None:
      data = _make_case(tmp_path, case)                   # small helper in the test module, one branch per case
      with pytest.raises(PackageError, match=needle):
          read_tqplugin(data, max_bytes=MB20)
  def test_read_rejects_over_size_cap(tmp_path) -> None:  # max_bytes=100 -> "größer als"
  def test_unknown_listed_member_is_ignored(tmp_path) -> None:
      # extra member "README.txt" listed in CHECKSUMS -> accepted
  def test_signature_roundtrip(tmp_path) -> None:
      priv, pub, kid = make_keypair()
      p = build_tqplugin(tmp_path, build_wheel(tmp_path), sign_with=priv)
      pkg = read_tqplugin(p.read_bytes(), max_bytes=MB20)
      verify_signature(pkg.signature, pkg.checksums, parse_public_key(pub))   # no exception
  def test_write_tqplugin_is_deterministic(tmp_path) -> None:
      # two writes of the same wheel -> identical CHECKSUMS bytes (members sorted, LF)
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D2 reading rules and D4 wheel rules. Read every member through a counting reader (`zipfile.ZipFile.open` + `read(MAX_MEMBER + 1)` in chunks, abort when the running total or the member exceeds its cap, ratio = uncompressed/compressed from the actual bytes read). Parse `entry_points.txt` with `configparser` (`[tiqora.plugins]` section). RECORD hashes: `urlsafe_b64encode(sha256).rstrip(b"=")`. `write_tqplugin` uses `ZipFile(..., "w", ZIP_DEFLATED)` with fixed `date_time=(1980, 1, 1, 0, 0, 0)` for every member (stable output). Error messages name the member (`f"Paketinhalt „{name}“: …"`), never its content.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): read and validate .tqplugin packages without pip`.

### Task 4: Unpacking into the plugins directory (backend, filesystem)

**Files:**
- Create: `backend/src/tiqora/plugins/runtime.py` (filesystem part: `DirStatus`, `plugins_dir_status`, `version_dir`, `materialize`, `activate_paths`, `gc_runtime_dirs`)
- Modify: `backend/src/tiqora/plugins/package.py` (`unpack_wheel`)
- Test: `backend/tests/test_plugins_runtime_fs.py`

**Interfaces:**
- Consumes: `read_tqplugin`, `TqPackage`, `clear_discovery_cache`.
- Produces: D5 filesystem functions; `unpack_wheel(wheel_bytes: bytes, target: Path) -> None`.

- [ ] **Step 1: Write the failing tests** (`settings = Settings(_env_file=None, plugins_dir=str(tmp_path / "plugins"))`, the dir created by the test unless the case needs it missing):
  ```python
  def test_dir_status_missing_dir(tmp_path) -> None:
      st = plugins_dir_status(_settings(tmp_path, create=False))
      assert not st.usable and "existiert nicht" in st.reason
  def test_dir_status_read_only(tmp_path) -> None:
      d = tmp_path / "plugins"; d.mkdir(); d.chmod(0o555)
      st = plugins_dir_status(_settings(tmp_path)); assert st.usable and not st.writable
      # skip when os.geteuid() == 0 (root ignores the mode)
  def test_dir_status_switched_off(tmp_path) -> None:   # plugins_runtime_install=False -> not usable, reason mentions the env var
  def test_materialize_then_discover_and_assets(tmp_path, monkeypatch) -> None:
      data = build_tqplugin(tmp_path, build_wheel(tmp_path)).read_bytes()
      pkg = read_tqplugin(data, max_bytes=MB20)
      site = materialize(s, pkg.manifest.id, pkg.manifest.version, pkg.sha256, data)
      assert (site.parent / "COMPLETE").read_text() == pkg.sha256
      monkeypatch.setattr(sys, "path", list(sys.path))
      activate_paths([site])
      found = {d.plugin_id: d for d in discover_plugins()}
      assert found["org.example.hello"].problem is None
      assert plugin_asset_path(found["org.example.hello"], "i18n/en.json") is not None
      # cleanup: sys.modules entries of tq_fixture_hello removed in a finalizer
  def test_materialize_is_idempotent_and_survives_concurrent_winner(tmp_path) -> None:
      # call twice -> same path, one version dir; pre-create the final dir with COMPLETE -> returns it, no .tmp-* left
  def test_materialize_never_trusts_existing_dir_without_complete(tmp_path) -> None:
      # final dir exists with a planted file but without COMPLETE -> replaced; planted file gone
  def test_unpack_refuses_escape_even_if_reader_was_bypassed(tmp_path) -> None:
      # call unpack_wheel with a hand-made wheel containing "../evil.py" -> PackageError, nothing outside target
  def test_activate_paths_appends_never_inserts(tmp_path, monkeypatch) -> None:
      monkeypatch.setattr(sys, "path", ["/a", "/b"])
      activate_paths([tmp_path / "x"]); activate_paths([tmp_path / "x"])
      assert sys.path == ["/a", "/b", str(tmp_path / "x")]
  def test_gc_keeps_current_and_previous(tmp_path) -> None:
      # three version dirs of one id + one dir of an unknown id + an old .tmp-* -> keeps the two named shas
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D5 filesystem part and `unpack_wheel` (D5 "Unpacking"). `plugins_dir_status` caches `(monotonic, result)` per `plugins_dir` for 60 s with a `reset_dir_status_cache()` for tests.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): unpack plugin packages into the plugins directory`.

### Task 5: Install checks — dependencies, name collisions, static validator (backend, pure)

**Files:**
- Create: `backend/src/tiqora/plugins/install_checks.py`
- Test: `backend/tests/test_plugins_install_checks.py`

**Interfaces:**
- Consumes: `packaging.requirements.Requirement`, `packaging.utils.canonicalize_name`, `importlib.metadata.packages_distributions`, Znuny table names from the legacy ORM metadata (`tiqora.db.legacy.base.LegacyBase.metadata.tables` after importing the legacy model modules — the same set the read path maps; verify with `grep -rn "LegacyBase" backend/src/tiqora/db/legacy`), plus the fixed list in `backend/src/tiqora/bootstrap/schema/` if a table is missing there.
- Produces: D4 functions `requirement_problems`, `collision_problems`, `migration_tables`, `static_warnings` (the blocking prefix problem is returned by a separate `prefix_problems(files, short_id) -> list[str]` so the report can keep problems and warnings apart).

- [ ] **Step 1: Write the failing tests.**
  ```python
  INSTALLED = {"httpx": "0.28.1", "pydantic": "2.9.2", "packaging": "24.2"}
  def test_requirements_satisfied() -> None:
      assert requirement_problems(["httpx>=0.27", "pydantic<3; python_version >= '3.10'"], INSTALLED) == []
  def test_requirement_missing_or_wrong_version() -> None:
      p = requirement_problems(["requests>=2", "httpx<0.20"], INSTALLED)
      assert any("„requests“" in x for x in p) and any("„httpx“" in x and "0.28.1" in x for x in p)
  def test_requirement_extra_only_lines_skipped() -> None:
      assert requirement_problems(['pytest; extra == "test"'], INSTALLED) == []
  @pytest.mark.parametrize("name", ["tiqora", "tiqora-sdk", "Tiqora_SDK"])
  def test_core_requirement_refused(name) -> None:
      assert "Tiqora bringt das selbst mit" in requirement_problems([name], INSTALLED)[0]
  @pytest.mark.parametrize("top", ["tiqora", "tiqora_sdk", "fastapi", "json", "asyncio"])
  def test_top_level_collision_refused(top) -> None:
      assert collision_problems([top]) != []
  def test_own_previous_copy_not_a_collision() -> None:
      assert collision_problems(["tq_x"], {"tq_x": ["tq-x"]}, own_dist="tq-x") == []
  def test_migration_tables_found() -> None:
      src = b'from alembic import op\nimport sqlalchemy as sa\ndef upgrade():\n    op.create_table("tqp_hello_note", sa.Column("id", sa.Integer))\n'
      assert migration_tables({"tq_x/migrations/0001_init.py": src}) == ["tqp_hello_note"]
  def test_prefix_problem_blocks() -> None:
      src = b'from alembic import op\ndef upgrade():\n    op.create_table("ticket_x")\n'
      assert "tqp_hello_" in prefix_problems({"tq_x/migrations/0001.py": src}, "hello")[0]
  def test_static_warnings_raw_sql_on_znuny_table() -> None:
      src = b'Q = "UPDATE ticket SET title = :t WHERE id = :i"\n'
      assert "„ticket“" in static_warnings({"tq_x/a.py": src}, short_id="hello")[0]
  def test_static_warnings_monkeypatch() -> None:
      src = b"import tiqora.domain.ticket_write_service as w\nw.add_article = None\n"
      assert "tiqora.domain.ticket_write_service" in static_warnings({"tq_x/a.py": src}, short_id="hello")[0]
  def test_static_warnings_ignore_own_tables_and_syntax_errors() -> None:
      # 'INSERT INTO tqp_hello_note' -> no warning; a file with a syntax error -> one warning "nicht lesbar", no exception
  ```
  (`collision_problems` gains a keyword `own_dist: str | None = None` — names whose distributions are only `own_dist` are ignored; record it in D4 when implementing.)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Marker evaluation: `Requirement.marker.evaluate({"extra": ""})`; installed versions compared with `SpecifierSet.contains(Version(v), prereleases=True)`. AST walkers only — never `exec`/`import` package code.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): install checks for dependencies, module names and raw SQL`.

### Task 6: Repository index format, key ring and the package report (backend, pure)

**Files:**
- Create: `backend/src/tiqora/plugins/repo_index.py`, `backend/src/tiqora/plugins/inspect.py`
- Test: `backend/tests/test_plugins_repo_index.py`, `backend/tests/test_plugins_inspect.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 5; `check_compat`, `release_core_version`, `SDK_VERSION`.
- Produces: D7 module; D4 `PackageReport`, `CapabilityLine`, `InstalledPlugin`, `inspect_package`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  # test_plugins_repo_index.py — INDEX = the D7 example as a dict with a real key from make_keypair()
  def test_parse_valid_index() -> None:
      idx = parse_index(json.dumps(INDEX).encode()); assert idx.plugins[0].versions[0].version == "1.1.0"
  def test_unknown_fields_ignored() -> None: ...                         # {"index_version": 1, "x": 1, …} ok
  @pytest.mark.parametrize("mutate,needle", [
      (lambda d: d.update(index_version=2), "index_version"),
      (lambda d: d["plugins"][0].update(id="Org.Example"), "Kennung"),
      (lambda d: d["plugins"][0]["versions"][0].update(sha256="xyz"), "sha256"),
      (lambda d: d["publishers"][0]["keys"][0].update(key_id="0" * 16), "key_id"),
      (lambda d: d["plugins"][0].update(publisher_id="org.unknown"), "Herausgeber"),
      (lambda d: d["plugins"].append(copy.deepcopy(d["plugins"][0])), "doppelt"),
  ])
  def test_parse_rejects(mutate, needle) -> None: ...
  def test_parse_rejects_oversized() -> None: ...                      # MAX_INDEX_BYTES + 1 -> IndexFormatError
  def test_verify_index_states() -> None:
      # no key configured -> False; key + valid sig -> True; key + missing sig / wrong sig -> IndexFormatError
  def test_resolve_package_url() -> None:
      assert resolve_package_url("https://p.example.org/r/index.json", "pkgs/a.tqplugin") == "https://p.example.org/r/pkgs/a.tqplugin"
      # absolute http:// for an https index -> IndexFormatError; file index + absolute URL -> IndexFormatError
  def test_newest_compatible_skips_yanked_and_incompatible() -> None: ...
  # test_plugins_inspect.py — uses build_wheel/build_tqplugin; ring from make_keypair()
  def test_report_install_signed_local(tmp_path) -> None:
      r = inspect_package(data, settings=S, ring=ring_local, installed={}, image_plugin_ids=frozenset())
      assert (r.action, r.installable, r.trust) == ("install", True, TrustLevel.LOCAL)
      assert CapabilityLine(kind="extension_point", value="core.http.routes") in r.capabilities
  def test_report_tables_listed(tmp_path) -> None:
      # package built from tq_fixture_tables -> CapabilityLine(kind="table", value="tqp_tables_note")
  def test_report_unsigned_blocked_unless_allowed(tmp_path) -> None:
      # allow_unsigned False -> installable False, problem mentions TIQORA_PLUGINS_ALLOW_UNSIGNED; True -> installable
  def test_report_invalid_signature_never_installable(tmp_path) -> None: ...   # even with allow_unsigned
  def test_trust_publisher_key_from_verified_index_only(tmp_path) -> None:
      # PublisherKey present -> VERIFIED_PUBLISHER; same key but ring built without it (index unverified) -> UNSIGNED
  def test_image_plugin_not_installable(tmp_path) -> None:
      # image_plugin_ids={"org.example.hello"} -> action "blocked_image", problem "ist im Image eingebaut"
  def test_update_reinstall_downgrade(tmp_path) -> None:
      # installed 0.9.0 -> "update"; 1.0.0 -> "reinstall"; 1.1.0 -> "downgrade" + problem, installable False
  def test_report_requirements_problem_and_hint(tmp_path) -> None:
      # requires_dist=["requests>=2"] -> problem + "tiqora plugins bake" hint, installable False
  def test_report_incompatible_sdk(tmp_path) -> None:
      # manifest_overrides={"sdk": ">=9"} -> problem from check_compat
  def test_report_warnings_not_blocking(tmp_path) -> None:
      # extra member with raw UPDATE ticket SQL -> warnings non-empty, installable True
  def test_report_is_json_roundtrippable(tmp_path) -> None:
      r = inspect_package(...); assert PackageReport.model_validate_json(r.model_dump_json()) == r
  ```
  Create the fixture plugin `backend/tests/plugins/fixtures/tq_fixture_tables/` in this task (needed for the table line): `tiqora-plugin.toml` (`id = "org.example.tables"`, `short_id = "tables"`, `extension_points = ["core.http.routes"]`), `__init__.py` (`plugin` object whose `register` adds one route `GET /notes` reading `tqp_tables_note` via `DbSession`), `migrations/0001_init.py` (`revision = "tables_0001"`, `down_revision = None`, creates `tqp_tables_note(id INTEGER PK autoincrement, body VARCHAR(200) NOT NULL)` and index `tqp_tables_note_body`), `i18n/en.json`, `i18n/de.json`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D7 and D4 (`inspect_package`). Problem texts German; the unsigned problem is "Paket ist nicht signiert (oder mit einem unbekannten Schlüssel). Unsignierte Pakete sind nur mit TIQORA_PLUGINS_ALLOW_UNSIGNED=1 erlaubt."
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): repository index format v1 and package report`.

### Task 7: CLI — keygen, pack, sign, inspect, index (backend)

**Files:**
- Modify: `backend/src/tiqora/cli/plugins.py`
- Test: `backend/tests/test_plugins_cli_packaging.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 6.
- Produces: D14 commands `keygen`, `pack`, `sign`, `inspect`, `index build`, `index sign` (no DB access).

- [ ] **Step 1: Write the failing tests** (helper `_run(argv) -> int` calls `tiqora.main.main(argv)` and returns the `SystemExit` code; capture stdout/stderr with `capsys`; `get_settings.cache_clear()` after env changes):
  ```python
  def test_keygen_writes_0600_and_prints_public_key(tmp_path, capsys) -> None:
      code = _run(["plugins", "keygen", "--out", str(tmp_path / "k.pem")])
      assert code == 0 and oct((tmp_path / "k.pem").stat().st_mode & 0o777) == "0o600"
      assert "ed25519:" in capsys.readouterr().out
  def test_keygen_refuses_overwrite(tmp_path) -> None: ...              # exit 2
  def test_pack_sign_inspect_roundtrip(tmp_path, monkeypatch, capsys) -> None:
      wheel = build_wheel(tmp_path)
      _run(["plugins", "keygen", "--out", str(tmp_path / "k.pem")]); pub = _pub_from(capsys)
      _run(["plugins", "pack", str(wheel), "--output", str(tmp_path / "a.tqplugin")])
      _run(["plugins", "sign", str(tmp_path / "a.tqplugin"), "--key", str(tmp_path / "k.pem"), "--output", str(tmp_path / "b.tqplugin")])
      monkeypatch.setenv("TIQORA_PLUGINS_TRUSTED_KEYS", pub); get_settings.cache_clear()
      assert _run(["plugins", "inspect", str(tmp_path / "b.tqplugin"), "--json"]) == 0
      assert json.loads(capsys.readouterr().out)["trust"] == "local"
  def test_inspect_unsigned_exits_1(tmp_path) -> None: ...
  def test_index_build_and_sign(tmp_path) -> None:
      # two packages of different versions in DIR -> index.json validates with parse_index, versions sorted,
      # urls relative ("org.example.hello-1.0.0.tqplugin"), sha256 equals the file hash; index sign -> verify_index True
  def test_sign_refuses_in_place_without_force(tmp_path) -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D14 (these subcommands). `index build` takes publishers only from the flags (one publisher per call is enough for a site repository; document combining by hand).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(plugins): CLI to create keys, pack, sign, inspect and index packages`.

### Task 8: Plugin migration environment and statement guard (backend, both dialects)

**Files:**
- Create: `backend/src/tiqora/plugins/migrations.py` (`PluginMigrationError`, `StatementGuard`, `migrations_dir`, `plugin_alembic_config`, `plugin_migration_head`, `upgrade_plugin`, `downgrade_plugin_to_base`), `backend/src/tiqora/plugins/alembic_env/env.py`, `backend/src/tiqora/plugins/alembic_env/script.py.mako` (copy of `backend/alembic/script.py.mako`)
- Modify: `backend/pyproject.toml` only if hatch would not ship `script.py.mako` (check with `uv build --wheel` + `unzip -l`; memory "Hatch-Wheel verliert ignorierte Datenfiles")
- Test: `backend/tests/test_plugins_migrations_guard.py` (pure), `backend/tests/test_plugins_migrations_db.py` (both dialects, sync tests like `test_plugins_migration_db.py`)

**Interfaces:**
- Consumes: `DiscoveredPlugin`, `install_fixture_plugin` (phase-1 helper) with `tq_fixture_tables` (Task 6), `tiqora.db.engine._normalize_url`.
- Produces: D6 functions listed under Files; `StatementGuard(short_id: str)` callable with the SQLAlchemy `before_cursor_execute` signature; `check(statement: str) -> None` method used by the tests.

- [ ] **Step 1: Write the failing tests.**
  ```python
  # test_plugins_migrations_guard.py
  G = StatementGuard("tables")
  @pytest.mark.parametrize("sql", [
      "CREATE TABLE tqp_tables_note (id INTEGER NOT NULL, PRIMARY KEY (id))",
      'CREATE TABLE "tqp_tables_note" (id SERIAL)', "CREATE TABLE `tqp_tables_note` (id INT)",
      "CREATE INDEX tqp_tables_note_body ON tqp_tables_note (body)", "DROP INDEX tqp_tables_note_body ON tqp_tables_note",
      "ALTER TABLE tqp_tables_note ADD COLUMN x INTEGER", "INSERT INTO tqp_tables_alembic_version (version_num) VALUES ('a')",
      "UPDATE tqp_tables_alembic_version SET version_num='b'", "DELETE FROM tqp_tables_note",
      "SELECT * FROM ticket", "SET NAMES utf8mb4", "CREATE TYPE tqp_tables_kind AS ENUM ('a')",
      "CREATE TABLE tqp_tables_b (a_id INTEGER REFERENCES tqp_tables_note (id))",
  ])
  def test_guard_allows(sql) -> None: G.check(sql)
  @pytest.mark.parametrize("sql", [
      "CREATE TABLE ticket_x (id INT)", "CREATE TABLE tiqora_x (id INT)", "ALTER TABLE ticket ADD COLUMN x INT",
      "DROP TABLE users", "DROP TABLE tqp_tables_note, ticket", "INSERT INTO ticket_history (id) VALUES (1)",
      "UPDATE ticket SET title='x'", "DELETE FROM article", "TRUNCATE ticket",
      "CREATE TABLE tqp_tables_c (t INTEGER REFERENCES ticket (id))", "RENAME TABLE tqp_tables_note TO ticket",
      "CREATE INDEX tqp_tables_i ON ticket (tn)", "CREATE TABLE tqp_other_x (id INT)", "GRANT ALL ON ticket TO x",
      "CREATE PROCEDURE p() BEGIN END", "/* tqp_tables_ */ DROP TABLE ticket",
  ])
  def test_guard_rejects(sql) -> None:
      with pytest.raises(PluginMigrationError, match="nicht erlaubt"): G.check(sql)
  # test_plugins_migrations_db.py (sync; parametrized over both dialects; drops tqp_tables_* in teardown)
  def test_upgrade_creates_prefixed_tables_and_own_version_table(dialect_url, tmp_path, monkeypatch) -> None:
      plugin = _discover(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
      assert upgrade_plugin(plugin, database_url=dialect_url) == "tables_0001"
      names = set(inspect(engine).get_table_names())
      assert {"tqp_tables_note", "tqp_tables_alembic_version"} <= names
      assert _core_version(engine) == TIQORA_HEAD          # tiqora_alembic_version untouched
  def test_upgrade_is_idempotent(...) -> None: ...         # second call -> same head, no error
  def test_guard_blocks_before_execution(dialect_url, tmp_path, monkeypatch) -> None:
      # fixture copy with migrations/0002_bad.py: op.create_table("tqp_tables_ok", …) then op.execute("UPDATE ticket SET title='x'")
      # -> PluginMigrationError; no Znuny row changed (count of ticket rows with title 'x' == 0)
  def test_failed_run_rolls_back_on_postgres(postgres_url, ...) -> None:
      # same bad migration -> "tqp_tables_ok" NOT present afterwards, version table still at tables_0001
  def test_failed_run_reported_on_mariadb(mariadb_url, ...) -> None:
      # same bad migration -> "tqp_tables_ok" present (DDL autocommit), version still tables_0001, error raised
  def test_post_run_diff_catches_unprefixed_table(dialect_url, monkeypatch, ...) -> None:
      # monkeypatch StatementGuard.check to a no-op; migration creates "zz_plain" -> PluginMigrationError
      # "Tabelle zz_plain …"; teardown drops zz_plain
  def test_plugin_without_migrations_returns_none(...) -> None: ...   # tq_fixture_hello -> None, no version table
  def test_two_heads_rejected(...) -> None: ...                      # two revisions with down_revision None -> PluginMigrationError
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D6 (environment, guard, post-run diff, config building). The guard parses with small regexes on the normalised statement; table names may be schema-qualified (`public.tqp_…`) — strip a leading `<schema>.`. `upgrade_plugin` raises `PluginMigrationError` with a German message for every failure (wrap Alembic/DB errors: `f"Datenbank-Änderung „{revision}“ fehlgeschlagen: {type(exc).__name__}: {exc}"[:2000]`).
- [ ] **Step 4: Run** → PASS (both dialects); `uv build --wheel` in `backend/` and `unzip -l dist/*.whl | grep alembic_env` lists `env.py` and `script.py.mako` (then delete `dist/`); lint gates; commit `feat(plugins): own database tables through guarded per-plugin migrations`.

### Task 9: Plugin migrations at boot, `plugins migrate`, load gate (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/migrations.py` (`record_migration`, `run_boot_migrations`), `backend/src/tiqora/cli/migrate.py` (`_cmd_upgrade`), `backend/src/tiqora/cli/plugins.py` (`migrate` subcommand), `backend/src/tiqora/plugins/store.py` (`BootInfo` + `_prepare_once` read `migrated_version`, `migration_error`; `sync_discovered` fills `short_id`), `backend/src/tiqora/plugins/bootstrap.py` (`_load_plugins`: gate after `_requirement_problem`)
- Test: `backend/tests/test_plugins_boot_migrations.py` (both dialects), extend `backend/tests/test_plugins_bootstrap.py`, `backend/tests/test_migration_gate.py` (one new test)

**Interfaces:**
- Consumes: Task 8; `discovered_plugins`, `check_compat`.
- Produces:
  ```python
  def run_boot_migrations(*, database_url: str, plugins: Sequence[DiscoveredPlugin],
                          rows: Mapping[str, tuple[str | None, str | None]]) -> dict[str, str | None]
      # sync; rows: plugin_id -> (migrated_version, migration_error); runs upgrade_plugin for every plugin that
      # needs it (D6 "Boot"); returns plugin_id -> error (None = ok) for the ones it ran; never raises
  async def record_migration(factory, plugin_id: str, *, version: str, head: str | None, error: str | None, now: datetime) -> None
  MIGRATION_PENDING_MESSAGE: Final = "Datenbank-Änderungen der Erweiterung stehen aus oder sind fehlgeschlagen"
  # BootInfo gains: migrated_version: str | None = None; migration_error: str | None = None
  ```

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_migrate_upgrade_runs_plugin_after_core(dialect_url, tmp_path, monkeypatch) -> None:
      _install(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
      _seed_plugin_row("org.example.tables")                    # enabled, version 1.0.0, migrated_version NULL
      assert _run(["migrate", "upgrade"]) == 0
      assert "tqp_tables_note" in _tables() and _row("org.example.tables").migrated_version == "1.0.0"
  def test_failed_plugin_migration_does_not_stop_others(dialect_url, ...) -> None:
      # two fixture plugins (one with a guard-violating migration) -> exit 0; good one migrated; bad one
      # migration_error starts "Datenbank-Änderung", migrated_version NULL
  def test_core_upgrade_ignores_plugin_version_tables(dialect_url, ...) -> None:
      # migrate the tables plugin, then remove it from sys.path (uninstalled, data kept) -> `migrate upgrade` exit 0,
      # core head unchanged, tqp_tables_alembic_version still present
  def test_skips_when_already_migrated(dialect_url, ...) -> None: ...   # migrated_version == version, no error -> upgrade_plugin not called (spy)
  def test_incompatible_or_invalid_plugin_not_migrated(...) -> None: ...
  def test_plugins_migrate_cli_with_path(dialect_url, tmp_path, ...) -> None:
      # package materialised into tmp plugins dir (Task 4) -> `plugins migrate org.example.tables --path <site>` exit 0,
      # row updated; bad migration -> exit 1, German error on stderr, row.migration_error set
  # test_plugins_bootstrap.py
  async def test_load_gate_blocks_pending_migration(...) -> None:
      # tables plugin discovered, row migrated_version NULL -> RuntimeState.FAILED, error starts MIGRATION_PENDING_MESSAGE,
      # its route not in the registry; a plugin without migrations/ loads normally
  async def test_load_gate_passes_after_migration(...) -> None: ...
  # test_migration_gate.py
  def test_plugin_migrations_never_reach_core_graph() -> None:
      # heads of build_alembic_config(include_owned=False) == {TIQORA_HEAD} with tq_fixture_tables installed
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `_cmd_upgrade`: after `command.upgrade(cfg, args.revision)` and only when `args.revision == "heads"`, `asyncio.run(_plugin_rows_and_activation())` (activation arrives in Task 11 — here only the DB rows: `select(TiqoraPlugin.id, migrated_version, migration_error)`; missing table → skip silently), then `run_boot_migrations(...)` (sync, no loop running), then `asyncio.run(_record_all(results))`; print one line per plugin ("Erweiterung org.example.tables: Datenbank aktuell (tables_0001)" / "…: FEHLER …"); exit code stays 0. `plugins migrate --path` follows the D14 `--path` rule (no activation; discovery from `distributions(path=[site])` only). `_load_plugins`: after `_requirement_problem`, if `migrations_dir(p)` and `(info.migrated_version != p.manifest.version or info.migration_error)` → record FAILED with `f"{MIGRATION_PENDING_MESSAGE}: {info.migration_error or 'noch nicht ausgeführt'}"[:2000]`.
- [ ] **Step 4: Run** → PASS + `tests/test_plugins_bootstrap.py tests/test_plugins_store.py tests/test_migrate_cli.py tests/test_migration_gate.py`; lint gates; commit `feat(plugins): run plugin migrations at startup and load only migrated plugins`.

### Task 10: Package store in the database (backend, both dialects)

**Files:**
- Create: `backend/src/tiqora/plugins/packages_store.py`
- Test: `backend/tests/test_plugins_packages_store.py` (both dialects, band 62040–62059)

**Interfaces:**
- Consumes: Task 1 models; `PackageReport` (Task 6).
- Produces:
  ```python
  async def store_package(session, *, data: bytes, report: PackageReport, manifest_text: str, source: str,
                          repository_id: int | None, uploaded_by: int | None, now: datetime) -> TiqoraPluginPackage
      # idempotent by sha256 (existing row returned, report refreshed); flush only
  async def get_package(session, sha256: str) -> TiqoraPluginPackage | None          # without data
  async def get_package_data(session, sha256: str) -> bytes | None                    # loads only the blob column
  async def delete_package(session, sha256: str) -> bool                              # False when installed (referenced)
  async def purge_stale_packages(session, *, older_than: datetime) -> int             # never installed, not referenced
  async def installed_runtime_rows(session) -> list[TiqoraPlugin]                     # source in (upload, repository), sha set
  async def mark_package_installed(session, sha256: str, *, now: datetime) -> None
  async def unmark_packages(session, plugin_id: str) -> None                          # installed_at = NULL for all its packages
  async def keep_shas(session) -> dict[str, list[str]]
      # plugin_id -> [current sha, newest previously installed sha] for gc_runtime_dirs
  ```

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_store_is_idempotent_by_hash(url_fixture, request) -> None: ...
  async def test_get_package_does_not_load_blob(url_fixture, request) -> None:
      row = await get_package(s, SHA); assert "data" in sa_inspect(row).unloaded
  async def test_delete_refuses_installed(url_fixture, request) -> None:
      # tiqora_plugin row with package_sha256 = SHA -> delete_package False; other sha -> True
  async def test_purge_stale_only_never_installed_and_old(url_fixture, request) -> None:
      # three rows: old+never installed (purged), old+installed_at set (kept), new (kept)
  async def test_keep_shas_current_and_previous(url_fixture, request) -> None: ...
  ```
- [ ] **Step 2–4:** Run → FAIL; implement (IN-lists only, no JOIN needed); run → PASS; lint gates; commit `feat(plugins): store plugin packages in the database`.

### Task 11: Runtime activation in every process, image wins, safe-mode listing (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/runtime.py` (`RuntimeActivation`, `activate_runtime_plugins`), `backend/src/tiqora/plugins/bootstrap.py` (`_load_plugins`: activation before `discovered_plugins()`, synthetic INVALID entries for activation problems), `backend/src/tiqora/api/v1/admin/plugins.py` (`_build_all`: activation first, activation problems → status `invalid` with the message), `backend/src/tiqora/cli/migrate.py` (activation before boot migrations), `backend/src/tiqora/cli/plugins.py` (`list`/`verify`: best-effort activation with a 5 s DB timeout)
- Test: `backend/tests/test_plugins_runtime_activation.py` (both dialects, band 62060–62079), extend `backend/tests/test_plugins_bootstrap.py`, `backend/tests/test_admin_plugins_api.py`

**Interfaces:**
- Consumes: Tasks 4, 9, 10.
- Produces: D5 `activate_runtime_plugins`, `RuntimeActivation`; `_load_plugins` order: activation → discovery → … (gate from Task 9).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_activation_materialises_from_db_and_discovers(url_fixture, request, tmp_path, monkeypatch) -> None:
      # store a package (Task 10) + tiqora_plugin row source "upload" with its sha; empty plugins dir ->
      # activate -> site dir created, plugin discovered, problem None
  async def test_activation_uses_existing_complete_dir_when_read_only(…) -> None:
      # materialise first, chmod 0555 -> activation works without writing (skip as root)
  async def test_missing_blob_marks_invalid_core_boots(…) -> None:
      # row with a sha that has no package row and no dir -> init_registry(role="api") boots, load result INVALID
      # "Paketdateien fehlen – bitte erneut installieren"; phase-1 fixture hello (image) still ACTIVE
  async def test_image_wins_over_runtime_package(…) -> None:
      # install_fixture_plugin(hello) as image + runtime row for org.example.hello -> row becomes source "image",
      # package_sha256 NULL; only one discovery entry (no "mehrfach installiert")
  async def test_activation_without_tables_is_empty(…) -> None:
      # drop tiqora_plugin_package from a scratch schema? -> use a factory whose execute raises the dialect's
      # missing-table error (pattern of test_plugins_bootstrap's missing-table test) -> RuntimeActivation({}, {})
  async def test_safe_mode_lists_runtime_plugins_without_import(…) -> None:
      # TIQORA_PLUGINS_SAFE_MODE=1 app; GET /api/v1/admin/plugins lists org.example.tables (status "unknown",
      # "Abgesicherter Modus: nicht geladen"); "tq_fixture_tables" not in sys.modules
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D5 activation + the integrations (inside the existing `try` of `init_registry`, so any activation error keeps R-BOOT-ANYERR); run → PASS plus `tests/test_plugins_bootstrap.py tests/test_admin_plugins_api.py tests/test_plugins_discovery.py`; lint gates; commit `feat(plugins): load installed packages in every process`.

### Task 12: Process registry and restart decisions (backend, pure + both dialects)

**Files:**
- Create: `backend/src/tiqora/plugins/restart.py` (D10 without process wiring)
- Test: `backend/tests/test_plugins_restart_decide.py` (pure), `backend/tests/test_plugins_restart_db.py` (both dialects, band 62080–62119)

**Interfaces:**
- Consumes: Task 1 models; `PLUGIN_ROLES`.
- Produces: D10 `restart.py` API (`ProcessRole`, `RESTART_ORDER`, `RESTART_EXIT_CODE`, `restart_roles`, `register_process`, `heartbeat`, `unregister_process`, `request_restart`, `ProcessRow`, `RestartRow`, `Decision`, `decide`, `restart_watch_loop`, `restart_requested`, `restart_status`, `purge_dead_processes`, `ROLE_LABEL`), plus `RestartRow(generation, requested_at, roles: tuple[str, ...], finished_at, failed_message)`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  T0 = datetime(2026, 10, 12, 8, 0, 0)
  def P(i, role, gen, hb=0, exiting=None): return ProcessRow(i, role, gen, T0 + timedelta(seconds=hb), exiting)
  R = RestartRow(generation=5, requested_at=T0, roles=("worker", "mcp", "api"), finished_at=None, failed_message=None)
  def test_decide_disabled_stays() -> None:
      assert decide(P("w1", "worker", 4), R, [P("w1", "worker", 4)], now=T0, timeout_seconds=180, enabled=False).action == "stay"
  def test_decide_worker_goes_first() -> None:
      procs = [P("w1", "worker", 4), P("m1", "mcp", 4), P("a1", "api", 4)]
      assert decide(procs[0], R, procs, now=T0, timeout_seconds=180, enabled=True).action == "exit"
      assert decide(procs[1], R, procs, now=T0, timeout_seconds=180, enabled=True).action == "stay"
  def test_decide_mcp_after_worker_back() -> None:
      procs = [P("w2", "worker", 5, hb=20), P("m1", "mcp", 4, hb=20), P("a1", "api", 4, hb=20)]
      assert decide(procs[1], R, procs, now=T0 + timedelta(seconds=25), timeout_seconds=180, enabled=True).action == "exit"
  def test_decide_two_api_one_at_a_time() -> None:
      # roles done up to api; a1 and a2 old -> a1 exits (smallest id); a2 stays while a1 has exiting_at and no new api yet;
      # once a new api (gen 5) is live, a2 exits
  def test_decide_old_heartbeat_counts_as_gone() -> None:
      # a stale old worker (heartbeat 60 s ago) does not block "worker done" when a new worker is live
  def test_timeout_stops_sequence() -> None:
      # worker exited at T0+5 and none came back; mcp at T0+200 -> "fail", message names "Hintergrunddienst"
  def test_finish_when_all_done() -> None:
      # api with gen 5 and all roles done -> "finish"
  def test_role_not_in_request_stays() -> None: ...           # ai-worker process, roles without ai-worker
  def test_restart_roles_follow_plugin_roles(monkeypatch) -> None:
      assert restart_roles() == ("worker", "mcp", "api")
      monkeypatch.setattr(state, "PLUGIN_ROLES", ("api", "worker", "mcp", "ai-worker"))
      assert restart_roles() == ("worker", "ai-worker", "mcp", "api")
  # test_plugins_restart_db.py
  async def test_register_takes_current_generation(url_fixture, request) -> None: ...
  async def test_request_restart_snapshots_live_roles_and_supersedes(url_fixture, request) -> None:
      # live worker+api rows, stale mcp row -> roles "worker,api"; a second request finishes the first
  async def test_watch_loop_exits_once(url_fixture, request) -> None:
      # one worker row + a request -> loop sets exiting_at, calls on_exit once, restart_requested() True, returns
  async def test_watch_loop_survives_db_errors(…) -> None: ...   # factory raising OperationalError twice, then ok
  async def test_restart_status_role_states(url_fixture, request) -> None: ...
  async def test_purge_dead_processes(url_fixture, request) -> None: ...
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D10 (pure `decide`, DB helpers with explicit commits per call, `restart_watch_loop` with an injectable `sleep` and `clock` for tests); run → PASS; lint gates; commit `feat(plugins): process registry and coordinated restart decisions`.

### Task 13: Restart wiring in all processes + restart endpoints (backend)

**Files:**
- Modify: `backend/src/tiqora/api/app.py` (lifespan: `register_process("api")` after `init_registry`, background task `restart_watch_loop(..., on_exit=_sigterm_self)`, cancel + `unregister_process` on shutdown), `backend/src/tiqora/main.py` (`api` branch: `sys.exit(RESTART_EXIT_CODE)` after `uvicorn.run` when `restart_requested()`), `backend/src/tiqora/worker/__main__.py` (`run_worker`: register after `init_registry`, add the loop to the `asyncio.gather` with `on_exit=stop.set`, exit code after shutdown), `backend/src/tiqora/mcp_server/server.py` (`_app_lifespan` + `run_mcp_server`), `backend/src/tiqora/ai/worker.py` (`run_ai_worker`: register as `"ai-worker"`, loop, exit code), `backend/src/tiqora/api/v1/admin/plugin_packages.py` (create with the two restart routes only), `backend/src/tiqora/api/v1/admin/__init__.py` (register the router), `backend/src/tiqora/api/v1/admin/plugins.py` (`RESTART_MESSAGE` text per D11)
- Test: `backend/tests/test_plugins_restart_wiring.py`, `backend/tests/test_admin_plugin_restart_api.py` (both dialects, band 62120–62139)

**Interfaces:**
- Consumes: Task 12.
- Produces: `GET/POST /api/v1/admin/plugins/restart` (D11 `RestartStatusOut`, `RestartIn`); every process role registered in `tiqora_process` while running.

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_api_main_exits_75_after_requested_restart(monkeypatch) -> None:
      monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
      monkeypatch.setattr(restart, "_REQUESTED", True)
      with pytest.raises(SystemExit) as e: main(["api"])
      assert e.value.code == RESTART_EXIT_CODE
  def test_worker_exit_code_after_restart(monkeypatch) -> None: ...      # run_worker with every loop stubbed, flag set -> 75
  async def test_lifespan_registers_and_unregisters_api(url_fixture, request) -> None:
      # create_app() under LifespanManager -> one tiqora_process row role "api"; after shutdown the row is gone
  async def test_restart_request_and_status(url_fixture, request) -> None:
      # POST /restart as admin -> 201, generation > 0, roles include "api"; GET -> same generation
  async def test_restart_request_conflict(url_fixture, request) -> None:
      # second POST while unfinished -> 409 "Ein Neustart läuft bereits"
  async def test_restart_requires_admin_and_csrf(url_fixture, request) -> None: ...   # non-admin 403; cross-site 403
  async def test_status_says_manual_when_disabled(url_fixture, request, monkeypatch) -> None:
      # TIQORA_PLUGINS_COORDINATED_RESTART=0 -> coordinated False
  ```
- [ ] **Step 2–4:** Run → FAIL; implement (the watch loop never starts in tests unless the test enables it: gate on `settings.environment != "test"` is NOT allowed — instead the lifespan reads `app.state.start_restart_watch` defaulting to True and the test fixtures set it False where a running loop would interfere); regenerate OpenAPI; run → PASS + `tests/test_api_app*.py tests/test_worker*.py tests/test_mcp*.py -q` (the processes' existing tests); lint gates; commit `feat(plugins): processes restart one after another on request`.

### Task 14: Upload and package inspection API (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/api/v1/admin/plugin_packages.py` (`POST /packages`, `GET /packages/{sha256}`, `DELETE /packages/{sha256}`), `backend/src/tiqora/plugins/repositories.py` (create with `build_key_ring` only; the rest arrives in Tasks 17–19), `backend/src/tiqora/plugins/inspect.py` (`installed_state(session) -> tuple[dict[str, InstalledPlugin], frozenset[str]]`: rows + discovery, image ids = discovered plugins whose row source is "image" or that have no row)
- Test: `backend/tests/test_admin_plugin_packages_api.py` (both dialects, band 62140–62159)

**Interfaces:**
- Consumes: Tasks 6, 10; `read_upload_within_limit`.
- Produces: D11 package endpoints and models `PluginPackageOut`, `CapabilityLineOut`; `build_key_ring(session, settings) -> KeyRing`; `installed_state`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_upload_returns_report_and_stores(url_fixture, request, tmp_path) -> None:
      r = await client.post("/api/v1/admin/plugins/packages", files={"file": ("a.tqplugin", data, "application/zip")})
      assert r.status_code == 201 and r.json()["plugin_id"] == "org.example.tables" and r.json()["installable"] is False
      # unsigned + allow_unsigned False -> problems contain "TIQORA_PLUGINS_ALLOW_UNSIGNED"
  async def test_upload_same_file_twice_200(url_fixture, request, tmp_path) -> None: ...
  async def test_upload_too_large_413(url_fixture, request, monkeypatch) -> None:
      # TIQORA_PLUGINS_MAX_PACKAGE_MB=1, 1.5 MB body -> 413
  async def test_upload_garbage_422(url_fixture, request) -> None:      # b"PK\x03\x04garbage" -> 422 German detail
  async def test_upload_signed_with_trusted_key_installable(url_fixture, request, monkeypatch, tmp_path) -> None:
      # TIQORA_PLUGINS_TRUSTED_KEYS=<pub> -> trust "local", installable True, capabilities include table line
  async def test_get_reevaluates_trust(url_fixture, request, monkeypatch, tmp_path) -> None:
      # upload unsigned-with-unknown-key, then set the env key -> GET shows trust "local"
  async def test_delete_package(url_fixture, request) -> None: ...      # 204; installed -> 409
  async def test_requires_admin_and_csrf(url_fixture, request) -> None: ...
  async def test_upload_while_runtime_install_unusable_still_inspects(url_fixture, request, monkeypatch) -> None:
      # plugins_dir missing -> 201 with report (inspection is useful), installable False, problem = runtime reason
  ```
- [ ] **Step 2–4:** Run → FAIL; implement (inspect synchronously — bytes ≤ 25 MB, no network, no import); regenerate OpenAPI; run → PASS; lint gates; commit `feat(plugins): upload and inspect plugin packages`.

### Task 15: Install job and endpoint (backend, both dialects)

**Files:**
- Create: `backend/src/tiqora/plugins/install_jobs.py` (payload models of D9, `PLUGIN_INSTALL_JOB`, helpers `_run_subprocess(argv, timeout) -> tuple[int, str]` with an injectable runner for tests)
- Modify: `backend/src/tiqora/extensions/core.py` (register `PLUGIN_INSTALL_JOB`), `backend/src/tiqora/jobs/registry.py` (`LANE_SLOTS` gains `"plugins": 1`), `backend/src/tiqora/plugins/bake.py` (`verify_installed_plugins(..., extra_paths: Sequence[Path] = (), only: str | None = None)`), `backend/src/tiqora/cli/plugins.py` (`verify --path --only`), `backend/src/tiqora/api/v1/admin/plugin_packages.py` (`POST /packages/{sha256}/install`)
- Test: `backend/tests/test_plugins_install_job.py` (both dialects, band 62160–62199), `backend/tests/test_plugins_bake.py` (extend for `--path/--only`)

**Interfaces:**
- Consumes: Tasks 4, 6, 8–12, 14; `enqueue_job`, `find_active_job`, `JobContext`, `JobFailed`.
- Produces: job kind `core.plugin_install` (D9 steps), endpoint `POST /api/v1/admin/plugins/packages/{sha256}/install` → `JobAccepted` (202).

- [ ] **Step 1: Write the failing tests** (the job runs through `JobRunner` or by calling the handler with a real `JobContext`, pattern of `tests/test_plugins_jobs.py`; the subprocess runner is replaced by a fake that runs the same CLI in-process via `main([...])` in a thread — plus ONE real subprocess test marked `@pytest.mark.slow` if the suite has that marker, else unmarked):
  ```python
  async def test_install_new_plugin_end_to_end(url_fixture, request, tmp_path, monkeypatch) -> None:
      # signed tables package uploaded (Task 14 helper) -> POST install {enable: true, restart: true} -> 202
      # run the job -> succeeded; tiqora_plugin row: source "upload", version "1.0.0", package_sha256 = sha,
      # signature_status "local", enabled True, installed_by = admin id, migrated_version "1.0.0";
      # table tqp_tables_note exists; a tiqora_restart row exists; result["restart_generation"] == its generation
  async def test_install_without_restart(…) -> None: ...                   # restart False -> no tiqora_restart row
  async def test_install_refused_when_dir_unusable(…) -> None:             # endpoint 409 with the reason text
  async def test_install_refused_when_not_installable(…) -> None:          # 409, detail lists problems
  async def test_second_install_for_same_plugin_conflicts(…) -> None:      # active job -> 409
  async def test_verify_failure_keeps_old_version(…) -> None:
      # package whose plugin raises at import -> job failed "Prüfung fehlgeschlagen: …"; row unchanged; no migration ran
  async def test_migration_failure_keeps_old_version(…) -> None:
      # package with a guard-violating migration -> job failed "Datenbank-Änderungen fehlgeschlagen"; row version unchanged
  async def test_update_keeps_enabled_and_hook_cursors(…) -> None:
      # installed 1.0.0 disabled + an event hook row with cursor 42 -> install 1.1.0 -> enabled still False, cursor 42
  async def test_trust_rechecked_in_job(…) -> None:
      # upload while key trusted, remove the env key before the job runs -> job failed (unsigned problem)
  async def test_update_checks_the_new_code_not_the_installed_one(…) -> None:
      # 1.0.0 installed and materialised (its site dir is what activation would add); new 1.1.0 package whose
      # plugin module raises at import -> job failed "Prüfung fehlgeschlagen" (proves the child imported 1.1.0);
      # and the reverse: broken 1.0.0 installed, good 1.1.0 -> job succeeds
  def test_verify_cli_path_only(tmp_path, …) -> None:
      # `plugins verify --import --strict --path <site> --only org.example.tables` -> 0; with a broken plugin -> 1
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D9 install; `verify --path/--only` and `migrate --path` follow the D14 `--path` rule; regenerate OpenAPI; run → PASS + `tests/test_plugins_jobs.py tests/test_jobs*.py`; lint gates; commit `feat(plugins): install plugin packages as a background job`.

### Task 16: Uninstall job with "Daten behalten" / "Daten löschen" (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/install_jobs.py` (`PLUGIN_UNINSTALL_JOB`), `backend/src/tiqora/plugins/migrations.py` (`plugin_tables`, `export_plugin_data`, `drop_plugin_data`), `backend/src/tiqora/extensions/core.py`, `backend/src/tiqora/api/v1/admin/plugin_packages.py` (`POST /{plugin_id}/uninstall`)
- Test: `backend/tests/test_plugins_uninstall_job.py` (both dialects, band 62200–62239)

**Interfaces:**
- Consumes: Tasks 8, 10–12, 15.
- Produces: job kind `core.plugin_uninstall` (D9), endpoint `POST /api/v1/admin/plugins/{plugin_id}/uninstall` → `JobAccepted`; `export_plugin_data` output format: gzip of JSON lines `{"table": "tqp_tables_note", "row": {...}}` (values via `jsonable_encoder`; bytes as base64), first line `{"plugin_id": …, "short_id": …, "exported_at": …, "tables": [...]}`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_uninstall_keep_data(url_fixture, request, …) -> None:
      # installed tables plugin with 2 note rows -> uninstall {delete_data: false} -> rows of tiqora_plugin/_runtime gone,
      # tqp_tables_note still has 2 rows, tqp_tables_alembic_version present, settings rows kept, restart requested
  async def test_delete_data_exports_then_drops_only_prefix(url_fixture, request, …) -> None:
      # plus a decoy table "tqp_tablesx_keep" and "tiqora_x_keep" -> after delete_data: all tqp_tables_* gone (incl.
      # version table), decoys present; job has one file; gunzip -> header + 2 row lines; settings "plugins.org.example.tables.*" gone
  async def test_delete_data_too_large_refuses(…, monkeypatch) -> None:
      # monkeypatch EXPORT_MAX_BYTES to 10 -> job failed with the 200 MB text; tables still there
  async def test_image_plugin_in_image_refused(…) -> None:            # endpoint 409 (can_uninstall False)
  async def test_image_plugin_gone_can_delete_data(…) -> None:
      # row source "image", short_id "tables", no discovery entry -> uninstall {delete_data: true} works
  async def test_uninstall_in_safe_mode(…, monkeypatch) -> None:
      # TIQORA_PLUGINS_SAFE_MODE=1 -> endpoint + job still work for a runtime plugin
  async def test_postgres_enum_types_dropped(postgres only) -> None:
      # tmp-written variant of tq_fixture_tables (copied into tmp_path, plus migrations/0002_kind.py adding a column
      # sa.Enum("a", "b", name="tqp_tables_kind")) -> delete_data drops the table and the type
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D9 uninstall (`drop_plugin_data` re-checks every name against `f"tqp_{short_id}_"` immediately before `DROP`); regenerate OpenAPI; run → PASS; lint gates; commit `feat(plugins): uninstall plugins and optionally delete their data after a backup`.

### Task 17: Repository configuration API and env repositories (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/repositories.py` (`parse_env_repositories`, `sync_env_repositories`, `validate_repository_input`), create `backend/src/tiqora/api/v1/admin/plugin_repositories.py` (list/create/update/delete), `backend/src/tiqora/api/v1/admin/__init__.py`
- Test: `backend/tests/test_plugins_repositories_config.py` (pure + both dialects, band 62240–62259), `backend/tests/test_admin_plugin_repositories_api.py`

**Interfaces:**
- Consumes: Task 1, `parse_public_key`.
- Produces: `RepositoryOut`, `RepositoryIn`; `EnvRepository(name: str, url: str, public_key: str | None)`; `parse_env_repositories(raw: str, *, file_root: Path) -> tuple[list[EnvRepository], list[str]]` (entries + German problems, bad entries skipped and logged).

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_parse_env_repositories() -> None:
      repos, problems = parse_env_repositories(json.dumps([
          {"name": "Intern", "url": "https://plugins.example.org/index.json", "public_key": PUB},
          {"name": "Offline", "url": "file:///srv/repo/index.json"},           # outside root -> problem
          {"name": "Bad", "url": "http://plugins.example.org/index.json"},     # http -> problem
      ]), file_root=Path("/var/lib/tiqora/plugin-repository"))
      assert [r.name for r in repos] == ["Intern"] and len(problems) == 2
  async def test_env_sync_upserts_and_disables(url_fixture, request) -> None:
      # env with 2 repos -> 2 rows origin "env"; env with 1 -> other env row enabled False; admin rows untouched
  async def test_create_repository_validates(url_fixture, request) -> None:
      # POST file:///… -> 422 "nur https"; userinfo -> 422; bad key -> 422; duplicate url -> 409; ok -> 201
  async def test_env_repository_read_only(url_fixture, request) -> None:   # PUT/DELETE -> 409
  async def test_list_shows_verification_and_counts(url_fixture, request) -> None:
      # row with index_json of 2 plugins, index_verified True -> plugin_count 2, verified True, repository_name from index
  ```
- [ ] **Step 2–4:** Run → FAIL; implement; regenerate OpenAPI; run → PASS; lint gates; commit `feat(plugins): configure plugin repositories in the admin UI or by environment`.

### Task 18: Repository refresh — fetch, verify, rollback guard, daemon loop, catalog (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/repositories.py` (`fetch_bytes`, `refresh_repository`, `run_repositories_tick`, `catalog`), `backend/src/tiqora/plugins/install_jobs.py` (`PLUGIN_REPOSITORY_REFRESH_JOB`), `backend/src/tiqora/extensions/core.py`, `backend/src/tiqora/worker/services.py` (`DaemonService(slug="plugin_repositories", …)` appended after `plugin_tasks`), `backend/src/tiqora/worker/__main__.py` (tick + `_interval_loop("plugin_repositories", …)`), `backend/src/tiqora/api/v1/admin/plugin_repositories.py` (`POST /{id}/refresh`, `GET /catalog`)
- Test: `backend/tests/test_plugins_repositories_fetch.py` (both dialects, band 62260–62279; HTTP via `httpx.MockTransport` injected through a `transport` parameter of `fetch_bytes`, resolver injected to return a private IP; file repos in `tmp_path`)

**Interfaces:**
- Consumes: Tasks 6, 10, 17; `pin_outbound_url`.
- Produces: `fetch_bytes(url: str, *, max_bytes: int, settings: Settings, transport: httpx.AsyncBaseTransport | None = None, resolver: HostResolver | None = None) -> bytes`; `refresh_repository(...) -> RefreshResult(ok: bool, plugins: int, error: str | None)`; `run_repositories_tick(settings, factory) -> dict[str, int]`; `catalog(...) -> CatalogOut`; job `core.plugin_repository_refresh`; endpoints refresh + catalog.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_refresh_https_signed_index(url_fixture, request) -> None:
      # MockTransport serves index.json + index.json.sig signed with the repo key -> row index_verified True,
      # index_generated_at stored, last_error None
  async def test_refresh_unsigned_index_without_key(…) -> None:          # public_key NULL, no .sig (404) -> ok, verified False
  async def test_refresh_bad_signature_keeps_old_index(…) -> None:       # last_error "Signatur der Paketquelle ungültig"
  async def test_index_rollback_refused(…) -> None:
      # stored generated_at 2026-10-12, served 2026-10-01 -> error "älteren Stand", stored index unchanged
  async def test_redirect_not_followed(…) -> None:                        # 302 -> error "Weiterleitungen"
  async def test_loopback_blocked_private_allowed(…) -> None:
      # resolver -> 127.0.0.1 => error; -> 10.0.0.5 => ok (Assumption 9)
  async def test_oversized_index_cut_off(…) -> None: ...                  # body > MAX_INDEX_BYTES -> error, no full read
  async def test_file_repository_inside_root_only(…, tmp_path) -> None:
      # env file repo under tmp root works; index URL "../x.tqplugin" for a package resolves outside -> refused at download (Task 19)
  async def test_tick_without_repositories_makes_no_request(…) -> None:   # transport that fails the test if called
  async def test_tick_purges_stale_packages_and_counts_updates(…) -> None: ...
  async def test_catalog_filters_and_flags(url_fixture, request) -> None:
      # two repos; q="hallo" matches name; category filter; image plugin flagged; update_available vs installed;
      # publisher_verified only for the verified index with matching namespace
  async def test_refresh_endpoint_enqueues_job(…) -> None: ...            # 202 JobAccepted; dedupe -> same job id
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D8 (refresh, daemon, catalog); i18n for the daemon row is added in Task 24 (frontend) — the backend only needs the slug; regenerate OpenAPI; run → PASS + `tests/test_worker_services*.py tests/test_admin_daemons*.py` (the daemon catalog tests count services); lint gates; commit `feat(plugins): refresh plugin repositories and list their catalogue`.

### Task 19: Download from a repository and update detection (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/plugins/repositories.py` (`download_package`, `available_updates`, `UpdateInfo`), `backend/src/tiqora/plugins/install_jobs.py` (`PLUGIN_FETCH_JOB`), `backend/src/tiqora/extensions/core.py`, `backend/src/tiqora/api/v1/admin/plugin_repositories.py` (`POST /{id}/download`)
- Test: `backend/tests/test_plugins_repositories_download.py` (both dialects, band 62280–62299)

**Interfaces:**
- Consumes: Tasks 14, 18.
- Produces: job `core.plugin_fetch` with result `{"package_sha256", "installable"}`; `available_updates(session, *, settings) -> dict[str, UpdateInfo]`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_download_stores_package_with_repository_source(…) -> None:
      # index lists 1.1.0 with the right sha -> job succeeded; package row source "repository", repository_id set;
      # report trust "verified_publisher" (index verified, key listed, namespace matches)
  async def test_download_hash_mismatch_refused(…) -> None:             # served bytes differ -> job failed, no row
  async def test_download_size_mismatch_refused(…) -> None: ...
  async def test_download_unknown_version_404(…) -> None:              # endpoint 404 "Version nicht in der Paketquelle"
  async def test_download_refused_when_runtime_install_unusable(…) -> None:   # endpoint 409
  async def test_available_updates_same_publisher_only(…) -> None:
      # installed org.example.hello 1.0.0 (publisher org.example); repo A offers 1.1.0 by org.example -> update;
      # repo B offers 2.0.0 with publisher org.evil -> ignored
  async def test_available_updates_skip_yanked_and_incompatible(…) -> None: ...
  async def test_image_plugin_update_is_info_only(…) -> None:          # UpdateInfo.image_plugin True
  ```
- [ ] **Step 2–4:** Run → FAIL; implement; regenerate OpenAPI; run → PASS; lint gates; commit `feat(plugins): download plugin packages from repositories and detect updates`.

### Task 20: Admin list — source, trust, migration state, runtime availability, updates (backend, both dialects)

**Files:**
- Modify: `backend/src/tiqora/api/v1/admin/plugins.py` (D11 field additions; `PluginCapabilityOut.kind` += `"table"` with table lines from the stored package report of runtime plugins or `migration_tables` of the installed package files for image plugins; `_build_one` signature gains `update`, `runtime problems`), `backend/src/tiqora/plugins/state.py` only if a status message needs a new constant (no new `PluginStatus` value)
- Modify (frontend, same commit so CI stays green): `frontend/src/test/pluginFixtures.ts` (the existing `PluginAdminOut`/`PluginAdminListOut` factories gain every new field with neutral values), `frontend/src/demo/mockData.ts` (existing plugin admin mock gains the fields) — no UI change yet
- Test: extend `backend/tests/test_admin_plugins_api.py` (both dialects, band 62300–62319)

**Interfaces:**
- Consumes: Tasks 9, 11, 18, 19.
- Produces: `PluginAdminOut.signature_status`, `.signer`, `.can_uninstall`, `.migration_error`, `.update`; `PluginAdminListOut.runtime_install`, `.runtime_install_reason`, `.allow_unsigned`, `.max_package_mb`, `.coordinated_restart`; `PluginUpdateOut`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_list_runtime_install_flags(url_fixture, request, monkeypatch, tmp_path) -> None:
      # writable tmp plugins dir -> runtime_install True, reason None; missing dir -> False + reason
  async def test_runtime_plugin_fields(…) -> None:
      # uploaded verified plugin -> source "upload", signature_status "verified_publisher", signer "Example GmbH",
      # can_uninstall True; image plugin -> signature_status None, can_uninstall False
  async def test_migration_error_surfaces_as_failed(…) -> None:
      # row migration_error "…" + runtime FAILED with MIGRATION_PENDING_MESSAGE -> status "failed", migration_error set
  async def test_update_field(…) -> None: ...                         # available_updates stub -> PluginUpdateOut
  async def test_table_capabilities_listed(…) -> None: ...            # kind "table", value "tqp_tables_note"
  async def test_restart_required_message_mentions_restart_button(…) -> None: ...
  ```
- [ ] **Step 2–4:** Run → FAIL; implement; regenerate OpenAPI (check `schema.d.ts` gained the fields — defaulted fields are required in TS); update the two frontend fixture places; run → PASS + the whole `tests/test_admin_plugins_api.py` + `cd frontend && pnpm lint && pnpm test --run src/routes/admin/PluginsPage.test.tsx`; lint gates; commit `feat(plugins): show source, signature, database state and updates on the extensions page API`.

### Task 21: Frontend API layer, fixtures and demo mocks (frontend)

**Files:**
- Modify: `frontend/src/lib/pluginsApi.ts`, `frontend/src/test/pluginFixtures.ts`, `frontend/src/demo/mockData.ts`
- Create: `frontend/src/routes/admin/plugins/trust.ts`
- Test: `frontend/src/lib/pluginsApi.test.ts` (new), `frontend/src/routes/admin/plugins/trust.test.ts` (new)

**Interfaces:**
- Consumes: generated `Schemas["PluginPackageOut"]`, `["PluginInstallIn"]`, `["PluginUninstallIn"]`, `["RestartStatusOut"]`, `["RestartIn"]`, `["RepositoryOut"]`, `["RepositoryIn"]`, `["CatalogOut"]`, `["CatalogEntryOut"]`, `["DownloadIn"]`, `["JobAccepted"]`.
- Produces:
  ```ts
  export type PluginPackage = Schemas["PluginPackageOut"];
  export type RestartStatus = Schemas["RestartStatusOut"];
  export type PluginRepository = Schemas["RepositoryOut"];
  export type Catalog = Schemas["CatalogOut"];
  export type CatalogEntry = Schemas["CatalogEntryOut"];
  // pluginsApi additions (D12): uploadPackage(file: File): Promise<PluginPackage>; getPackage(sha); deletePackage(sha);
  // installPackage(sha, body: Schemas["PluginInstallIn"]): Promise<Schemas["JobAccepted"]>;
  // uninstall(id, body: Schemas["PluginUninstallIn"]): Promise<Schemas["JobAccepted"]>;
  // restartStatus(signal?): Promise<RestartStatus | null>; requestRestart(reason?: string): Promise<RestartStatus>;
  // repositories(signal?); createRepository(body); updateRepository(id, body); deleteRepository(id);
  // refreshRepository(id): Promise<JobAccepted>; catalog(params: {q?: string; category?: string; repositoryId?: number}, signal?);
  // download(repoId, body: Schemas["DownloadIn"]): Promise<JobAccepted>
  // trust.ts:
  export type TrustLevel = NonNullable<PluginPackage["trust"]>;
  export function trustLabelKey(level: TrustLevel): string           // "admin.plugins.trust.<level>"
  export function trustColor(level: TrustLevel): "success" | "accent" | "muted" | "warn" | "danger"
      // official success, verified_publisher accent, local muted, unsigned warn, invalid danger (SoftChip colours)
  export function sourceLabel(t: TFunction, p: PluginAdmin): string   // image / upload / repository (name from update or repositories cache when known)
  ```
  Fixture factories (every defaulted field present, R-TS-REQUIRED-DEFAULTS): `packageReport(overrides?)`, `repository(overrides?)`, `catalogEntry(overrides?)`, `restartStatus(overrides?)`.

- [ ] **Step 1: Write the failing tests.**
  ```ts
  it("uploads a package as multipart form data", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue(packageReport());
    await pluginsApi.uploadPackage(new File(["x"], "a.tqplugin"));
    const [method, path, opts] = spy.mock.calls[0];
    expect([method, path]).toEqual(["POST", "/api/v1/admin/plugins/packages"]);
    expect(opts?.body).toBeInstanceOf(FormData);
  });
  it("encodes plugin ids and sends catalog filters as query", async () => { … });   // "org.example.hello" path segment, {q, category, repository_id}
  it("maps trust levels to labels and colours", () => {
    expect(trustColor("invalid")).toBe("danger"); expect(trustLabelKey("verified_publisher")).toBe("admin.plugins.trust.verified_publisher");
  });
  ```
- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/lib/pluginsApi.test.ts src/routes/admin/plugins/trust.test.ts` → FAIL.
- [ ] **Step 3: Implement**; demo mocks (`resolveData`) for: `GET /api/v1/admin/plugins` (now with one image plugin, one uploaded plugin `org.example.tables` with `update` 1.1.0 and `signature_status: "verified_publisher"`), `GET /api/v1/admin/plugins/restart` (finished), `GET /api/v1/admin/plugin-repositories` (one admin repo verified, one env repo), `GET …/catalog` (three entries: installable, installed with update, image plugin), `POST …/packages` (report of an unsigned package with a warning), install/uninstall/download/refresh (→ `{job_id: 990001, status: "queued"}` and `GET /api/v1/jobs/990001` succeeded).
- [ ] **Step 4: Run** → PASS; `pnpm lint`; commit `feat(plugins-ui): API wrappers, fixtures and demo data for installing plugins`.

### Task 22: Upload and install dialog (frontend)

**Files:**
- Create: `frontend/src/routes/admin/plugins/InstallDialog.tsx`, `frontend/src/routes/admin/plugins/RestartPanel.tsx`
- Modify: `frontend/src/routes/admin/PluginsPage.tsx` (header button "Paket hochladen" with a hidden `<input type="file" accept=".tqplugin">`; `runtime_install = false` → the reason text instead; opens `InstallDialog`), i18n `en.json`/`de.json` (D13 keys `upload*`, `runtimeInstallOff`, `trust.*`, `install.*`, `capabilities.table(s)`, `restart.*`, `roles.aiWorker`, `jobs.kinds.core.plugin_*`) + propagation
- Test: `frontend/src/routes/admin/plugins/InstallDialog.test.tsx`, `frontend/src/routes/admin/plugins/RestartPanel.test.tsx`, extend `frontend/src/routes/admin/PluginsPage.test.tsx`

**Interfaces:**
- Consumes: Task 21; `JobProgress`, `useJob`, `Dialog`, `SoftChip`, phase-1 `capabilityText` (export it from `PluginsPage.tsx` into `frontend/src/routes/admin/plugins/capabilities.ts` in this task — pure move + the `table` kind).
- Produces: `InstallDialog({ pkg, open, onClose, coordinatedRestart }: { pkg: PluginPackage; open: boolean; onClose: () => void; coordinatedRestart: boolean })`; `RestartPanel({ generationHint }: { generationHint?: number | null })`.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("shows capabilities, tables and the trust note", async () => {
    render(<InstallDialog pkg={packageReport({ capabilities: [{ kind: "table", value: "tqp_tables_note" }] })} open onClose={vi.fn()} coordinatedRestart />);
    expect(await screen.findByText("Legt die Tabelle tqp_tables_note an")).toBeInTheDocument();
    expect(screen.getByText(/keine Absicherung/)).toBeInTheDocument();
  });
  it("blocks install when there are problems", () => { …; expect(screen.getByRole("button", { name: "Installieren" })).toBeDisabled(); });
  it("requires the trust checkbox for unsigned packages", async () => {
    // trust "unsigned", installable true -> button disabled until "Ich vertraue diesem Paket" is checked
  });
  it("hides the enable checkbox for updates and names both versions", () => {
    // action "update", installed_version "1.0.0", version "1.1.0" -> "Aktualisieren von 1.0.0 auf 1.1.0"; no "einschalten" checkbox
  });
  it("starts the job with the chosen options and shows progress, then the restart panel", async () => {
    // installPackage called with { enable: false, restart: true }; JobProgress rendered; job succeeded -> RestartPanel visible
  });
  it("hides the restart checkbox when coordinated restart is off", () => { … });
  // RestartPanel.test.tsx
  it("polls until finished and offers a reload", async () => {
    // restartStatus: running (worker done, api waiting) -> "Tiqora wird neu gestartet …"; then finished -> "Seite neu laden"
  });
  it("keeps polling through network errors while the web interface restarts", async () => {
    // reject twice with TypeError("Failed to fetch") -> "Weboberfläche startet neu …", then finished
  });
  it("shows the failure message", async () => { … });
  it("asks for a manual restart when coordinated is false", async () => { … });
  // PluginsPage.test.tsx
  it("uploads a file and opens the install dialog", async () => { … });
  it("shows the reason instead of the upload button when runtime install is off", async () => { … });
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D12 (`InstallDialog`, `RestartPanel`, upload in the page header). `RestartPanel` uses `useQuery({ queryKey: ["admin", "plugins", "restart"], refetchInterval: (q) => finished/failed ? false : 2000, retry: true })` and treats query errors as "api restarting" until `plugins_restart_timeout`-like 5 minutes pass (then shows the error).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run src/routes/admin && node scripts/check-i18n-keys.mjs` → PASS; demo check with `VITE_DEMO=1` + Playwright (upload button → dialog with demo report → install → restart panel); delete `.playwright-mcp`; commit `feat(plugins-ui): upload and install plugin packages`.

### Task 23: "Installiert" tab — chips, updates, uninstall, restart button (frontend)

**Files:**
- Create: `frontend/src/routes/admin/plugins/UninstallDialog.tsx`
- Modify: `frontend/src/routes/admin/PluginsPage.tsx` (tabs scaffold with `Tabs`: only "Installiert" filled here; plugin card: source + trust chips, migration error in details, update badge + button, uninstall button; "Jetzt neu starten" when any plugin is `restart_required`), i18n (`tabs.*`, `source.*`, `update.*`, `uninstall.*`, `restart.now`) + propagation
- Test: `frontend/src/routes/admin/plugins/UninstallDialog.test.tsx`, extend `frontend/src/routes/admin/PluginsPage.test.tsx`

**Interfaces:**
- Consumes: Tasks 21, 22.
- Produces: `UninstallDialog({ plugin, open, onClose, coordinatedRestart })`; page tab state in the URL search param `tab` (`installed` | `available` | `repositories`, default `installed`; TanStack search params are JSON-parsed — skill `tanstack-router-search-params-json-parsed`: validate with a plain string check).

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("shows source and trust chips", async () => {
    // image plugin -> "Im Image eingebaut", no trust chip; uploaded verified -> "Hochgeladen" + "Verifizierter Herausgeber"
  });
  it("offers an update from a repository and starts the download job", async () => {
    // update {version "1.1.0", image_plugin false} -> "Aktualisierung verfügbar: 1.1.0"; click "Aktualisieren" ->
    // pluginsApi.download(repoId, {plugin_id, version}); job succeeded with result.package_sha256 -> getPackage -> InstallDialog
  });
  it("shows only a hint for image plugin updates", async () => { … });
  it("uninstalls keeping data by default", async () => {
    // UninstallDialog -> uninstall(id, { delete_data: false, restart: true })
  });
  it("deletes data and offers the backup download", async () => {
    // choose "Daten löschen" -> job succeeded with files [{id: 7}] -> link href "/api/v1/jobs/990001/files/7"
  });
  it("hides uninstall for plugins that are in the image", async () => { … });
  it("offers a restart when a change needs one", async () => {
    // a plugin with status restart_required -> "Jetzt neu starten" -> requestRestart -> RestartPanel
  });
  it("switches tabs through the URL", async () => { … });
  ```
- [ ] **Step 2–4:** Run → FAIL; implement; `pnpm lint && pnpm test --run src/routes/admin && node scripts/check-i18n-keys.mjs` → PASS; demo check (Playwright); delete `.playwright-mcp`; commit `feat(plugins-ui): updates, uninstall and restart on the extensions page`.

### Task 24: "Verfügbar" and "Paketquellen" tabs, Daemons row label (frontend)

**Files:**
- Create: `frontend/src/routes/admin/plugins/CatalogTab.tsx`, `frontend/src/routes/admin/plugins/RepositoriesTab.tsx`
- Modify: `frontend/src/routes/admin/PluginsPage.tsx` (fill the two tabs), i18n (`catalog.*`, `repositories.*`, `admin.daemons.services.plugin_repositories.*`) + propagation, `frontend/src/lib/adminSearch.ts` (keywords for the plugins page: "paketquelle", "repository", "installieren", "signatur", "aktualisierung")
- Test: `frontend/src/routes/admin/plugins/CatalogTab.test.tsx`, `frontend/src/routes/admin/plugins/RepositoriesTab.test.tsx`

**Interfaces:**
- Consumes: Tasks 21–23.
- Produces: `CatalogTab({ runtimeInstall }: { runtimeInstall: boolean })`, `RepositoriesTab()`.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("searches and filters the catalogue", async () => {
    // typing "hallo" -> catalog called with { q: "hallo" } (debounced 300 ms; use fake timers); category select -> { category: "tickets" }
  });
  it("shows the right action per entry", async () => {
    // not installed + compatible -> "Installieren"; installed same version -> "Installiert" (disabled);
    // installed older -> "Aktualisieren"; image_plugin -> "Im Image eingebaut"; latest_compatible null -> "Passt nicht zu dieser Tiqora-Version"
  });
  it("installs from a repository via download job and install dialog", async () => { … });
  it("explains when no repository is configured", async () => { … });   // "Es ist noch keine Paketquelle eingerichtet."
  // RepositoriesTab
  it("adds a repository with key", async () => {
    // dialog name/url/key -> createRepository({ name, url, public_key, enabled: true }); 422 detail shown via apiErrorMessage
  });
  it("shows env repositories read-only", async () => { … });             // "Aus der Server-Konfiguration", no edit/delete buttons
  it("checks a repository now", async () => { … });                      // refreshRepository -> JobProgress -> list refetched
  it("shows the last error and verification state", async () => { … });
  ```
- [ ] **Step 2–4:** Run → FAIL; implement D12 tabs; `pnpm lint && pnpm test --run src/routes/admin && node scripts/check-i18n-keys.mjs` → PASS; demo check of both tabs and the Daemons page row (Playwright); delete `.playwright-mcp`; commit `feat(plugins-ui): browse repositories and manage package sources`.

### Task 25: Image, compose files and deployment docs (infra)

**Files:**
- Modify: `Dockerfile` (runtime stage, as root before `USER tiqora`: `RUN mkdir -p /var/lib/tiqora/plugins /var/lib/tiqora/plugin-repository && chown -R tiqora:tiqora /var/lib/tiqora` — no `VOLUME` instruction, it would create anonymous volumes), `docker-compose.example.yml`, `docker-compose.quickstart.yml`, `docker-compose.local.yml` (named volume `tiqora_plugins:/var/lib/tiqora/plugins` on `tiqora-api`, `tiqora-worker`, `tiqora-ai-worker`, `tiqora-mcp`; `depends_on: tiqora-api: condition: service_healthy` for worker, ai-worker and mcp — today they only wait for the api to *start*, so on a fresh start they could boot before the api ran the plugin migrations and keep those plugins FAILED until their next restart (**Assumption 17**); comment block with the D1 env vars, the restart-policy requirement and the read-only-root note), `docs/deployment.md`
- Test: `backend/tests/test_compose_plugins_volume.py` (new, pure: loads each compose YAML with `yaml.safe_load` and asserts every service whose `command` is api/worker/ai-worker/mcp mounts `tiqora_plugins` at `/var/lib/tiqora/plugins` and has `restart` in {"unless-stopped", "always"}, and that worker/ai-worker/mcp depend on `tiqora-api` with `condition: service_healthy` where the file has those services)

**Interfaces:**
- Consumes: D1/D5/D10.
- Produces: an image in which `plugins_dir_status()` is usable and writable for user `tiqora` without any compose change; compose examples ready for runtime install.

- [ ] **Step 1: Write the failing test** (the compose test above; service names: read them from the files — `grep -n "command:" docker-compose.*.yml`).
- [ ] **Step 2: Run** `uv run python -m pytest -q tests/test_compose_plugins_volume.py` → FAIL.
- [ ] **Step 3: Implement**; `docs/deployment.md` section "Erweiterungen zur Laufzeit installieren": volume (shared or per container — the database holds the packages, D5; a **bind mount** keeps the host's ownership, so the host directory must belong to uid 10001: `chown -R 10001:10001 /srv/tiqora/plugins`), start order (worker/ai-worker/mcp after the api is healthy, or a second restart of them after the first start with new plugin migrations; Kubernetes: same via an init container or a rollout after the api), read-only root (`read_only: true` → mount the volume rw or set `TIQORA_PLUGINS_RUNTIME_INSTALL=0` and bake), restart policy + exit code 75, `TIQORA_PLUGINS_COORDINATED_RESTART=0` for platforms without automatic restarts, all D1 env vars with defaults, `TIQORA_PLUGIN_REPOSITORIES` JSON example with `example.org`.
- [ ] **Step 4: Run** → PASS; `docker build --target runtime -t tiqora-plugins-check .` is optional locally (slow); at least `docker run --rm --entrypoint sh <built image> -c 'test -w /var/lib/tiqora/plugins'` if an image was built; commit `build(plugins): plugins directory in the image and volumes in the compose examples`.

### Task 26: Documentation and full verification

**Files:**
- Modify: `docs/plugins.md` (D15), `docs/architecture.md`, `docs/superpowers/specs/2026-10-08-plugin-system-design.md` (status line only), `docs/compatibility.md` only if it lists plugin limitations (grep "Plugin\|Erweiterung")

**Interfaces:**
- Consumes: everything above.
- Produces: documentation; a green full run.

- [ ] **Step 1: Docs.** Write D15. Every example uses `org.example.*`, `example.org`, generated placeholder keys (`ed25519:AAAA…` shortened with "…", never a real key). Teil 3 "Grenzen": rewrite only the four bullets owned by this plan (Cross-plan contracts); keep "Keine Sandbox" and say explicitly that signatures say *who* published a package, not that it is safe.
- [ ] **Step 2: Full backend run:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (both dialects; golden suite included) → green; `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk` → clean; `uv run alembic heads` → one primary head; SDK surface snapshot tests unchanged.
- [ ] **Step 3: Full frontend run:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → green; `node scripts/check-plugin-runtime.mjs` after `pnpm build` → ok; `rtk git diff --stat packages/api-client/openapi.json docs/api/openapi.json` plausible.
- [ ] **Step 4: End-to-end smoke (local compose, optional but recommended):** `docker compose -f docker-compose.local.yml up -d --build`; `tiqora plugins keygen` → pack + sign the `tq_fixture_tables` wheel (built with `backend/tests/plugins/package_builder.py` via a small script in the scratchpad) → set `TIQORA_PLUGINS_TRUSTED_KEYS` → upload in the UI → install → watch the restart panel (worker → mcp → api) → `/agent` still works → uninstall with "Daten löschen" → download the backup. Note results in the commit body.
- [ ] **Step 5: Commit** `docs(plugins): install, signatures, repositories and plugin tables`.

---

## Self-review notes (for the executor)

- **Spec coverage:** plugin migrations (§5) → Tasks 8, 9, 16; runtime install with volume and read-only fallback (§6, Risks) → Tasks 4, 10, 11, 14, 15, 25; coordinated restart (§6) → Tasks 12, 13, 22, 23; enable/disable without uninstall (§6) → phase 1 + restart button (Task 23); uninstall "Daten behalten"/"Daten löschen" with backup (§5) → Task 16; signatures and four trust levels, admin-only install, capability dialog with "no sandbox" wording (§7) → Tasks 2, 6, 14, 22; repository index (signed JSON with plugins, versions, compatibility, hashes, publisher keys, changelog), several custom repositories via UI or env incl. `file://`, update notifications on the plugin page and Daemons page (§8) → Tasks 6, 17–19, 23, 24; marketplace-ready (§8a: `index_version`, publisher ids + keys, categories, screenshots/icon URLs, licence, homepage, support, per-version changelog/compatibility, per-repository listing with search and categories, reverse-DNS ids, nothing phones home) → D7, Tasks 6, 18, 24; validator flags (§3) → Task 5; "pure-Python only, dependencies already in the image, checked against the core lockfile" (§2) → Tasks 3, 5; safe mode recovery → Tasks 11, 16.
- **Not in this plan (by design):** the remaining extension points and sync hooks (plugins2a), apps tier, `tiqora plugin new|dev`, test kit, docs site (phase 3), SDK 1.0 (phase 4), public marketplace, paid plugins, NetBox connector.
- **Names that must stay consistent:** `TrustLevel` values = `signature_status` column values = `PluginPackageOut.trust` / `PluginAdminOut.signature_status` literals = i18n `admin.plugins.trust.<value>`; job kinds `core.plugin_install|plugin_uninstall|plugin_fetch|plugin_repository_refresh` = i18n `jobs.kinds.core.<…>`; daemon slug `plugin_repositories` = i18n `admin.daemons.services.plugin_repositories`; restart roles `api|worker|mcp|ai-worker` = `RestartRoleOut.role`.
- **Traps:** the migration head is not the highest file number (Global Constraints); `plugins verify|migrate --path` must not activate installed packages (D14 `--path` rule) or the old version gets checked; Alembic's env uses `asyncio.run` — never call `upgrade_plugin` from a running loop (subprocess or the sync migrate CLI only); `sys.path` is appended, never prepended; MariaDB DDL autocommits (test both behaviours); hatch must ship `alembic_env/script.py.mako`; defaulted Pydantic fields become required TS properties (fixtures!); TanStack search params are JSON-parsed; German strings with "Sie".
