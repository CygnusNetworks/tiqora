# Wave 4 — ITSM phase 2b: Znuny ITSM importer, Mode A→B switch, GI `ConfigItem*`, `ConfigItemUpdate`, MCP CMDB tools, Znuny add-on `TiqoraITSM` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A site that runs Znuny ITSM can copy its whole CMDB (general catalog, CI classes with every definition version, CIs with **all** versions, CI links, CI attachments) into the Tiqora CMDB as often as it likes (dry run first, re-runnable, background job with a report), then switch from Mode A ("Znuny führt das ITSM") to Mode B ("Tiqora führt das ITSM") with one explicit admin action; afterwards Znuny's ITSM screens are read-only by policy, Znuny agents and customers still see Tiqora's CIs through the optional Znuny add-on `TiqoraITSM`, integrations keep working through Znuny-compatible GenericInterface operations `ConfigItemCreate/Get/Search/Update/Delete`, process transition action `ConfigItemUpdate` runs in Tiqora, and the AI can answer "welche Kunden hängen an Switch X?" through MCP tools `cmdb_search`, `cmdb_get`, `cmdb_impact`.

**Architecture:** New backend package `tiqora.itsm.importer` (ports-and-adapters: it reads Znuny ITSM only through the itsm1 plan's read-only `tiqora.itsm.znuny_adapter` and writes the CMDB only through the cmdb plan's services, both behind this plan's `ports.py` facades, so naming drift between parallel plans is absorbed in one file). A pure package `tiqora.itsm.znuny_format` converts between Znuny's data shapes (6.x Perl-literal definitions, `XMLData`, "stringified" `Key::N` data, GI `CIXMLData`) and the Tiqora CMDB attribute JSON; the importer, the GI operations and the `ConfigItemUpdate` action all use it. The import runs as a `tiqora_job` (one-slot lane, paged, batch commits, idempotent through `tiqora_itsm_import_map`), dry run uses a counting writer, the switch is its own job (Znuny read-only flag → final delta import → `itsm.mode = tiqora`). Two versioned SQL views (`tiqora_v1_itsm_ci`, `tiqora_v1_itsm_ci_links`) are the only interface of the Perl add-on, which never writes Tiqora data.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain (`versions_tiqora`), Pydantic v2, the wave-0 job system (`tiqora.jobs`), the wave-1 GI compat provider (`tiqora.api.compat`), process transition-action registry (`tiqora.process.actions`), FastMCP 3.x, React + TanStack Router/Query (`JobDialog`/`useJob`), Perl (Znuny 6.0–7.3 OPM), pytest + testcontainers (MariaDB + PostgreSQL), golden suite against real Znuny peers (6.5 / 7.3, ITSM variants).

**Spec:** `docs/superpowers/specs/2026-10-08-itsm-cmdb-design.md` — sections "1a. Coexistence with Znuny" (Mode A/B, add-on `TiqoraITSM`, documentation duty), "Importer from Znuny ITSM", "GenericInterface", "Phases" row 2 (importer, GI `ConfigItem*`, `ConfigItemUpdate` action, MCP tools, add-on widgets, `docs/itsm.md` coexistence chapter). GI side: `docs/superpowers/specs/2026-10-08-generic-interface-design.md` ("Boundary": `ConfigItem*` on the Tiqora CMDB). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` ("Waves" row 4, "Principles", "Definition of done").

**Binding product decisions (already taken, do not reopen):** `itsm.mode` (`off` | `znuny_readthrough` | `tiqora`) defaults automatically — Znuny ITSM tables present → `znuny_readthrough` (Mode A), otherwise `tiqora` (Mode B); switching A→B is **only** an explicit admin action (this plan's switch, which includes an importer run); the docs recommend Mode B as the target. NetBox is not in wave 4.

**Scope marker:** backend Tasks 1–16 and 19–25, frontend Tasks 17–18 (+ the small `ActionPanel` flip in Task 20), Znuny add-on Tasks 26–27, golden Task 28, docs + full verification Task 29. One migration (Task 2). 29 tasks.

**Dependencies on other wave-4 plans:** this plan is implemented **after** `itsm1` and `cmdb` are merged into the integration branch (it consumes their code, see "Cross-plan contracts"). It does not depend on `plugins2a`, `plugins2b`, `scim`.

---

## Open questions for the product owner

Every item is decided conservatively in this plan and marked **Assumption:** where it is used. Answers can change a task before it starts; none blocks Task 1.

1. **Znuny Change Management data (`change_*`, `change_workorder*`).** The spec's importer section lists "`change_*` → legacy archive", but the phases table puts the legacy change archive into phase 3 (wave 5). **Assumption:** this wave only *counts* changes/work orders and lists them in the report ("kommt mit Phase 3"); nothing is imported. The pipeline has a step slot for phase 3.
2. **CIs deleted in Znuny between two import runs.** **Assumption:** never deleted in Tiqora; the report lists them as "in Znuny gelöscht" so the admin can delete them in Tiqora by hand.
3. **CI attachments.** **Assumption:** imported from Znuny's `virtual_fs` when the DB backend is used (default); when Znuny stores them in the file system backend (`VirtualFS::Backend` = FS), they are not imported and the report says so per CI.
4. **`ConfigItem*` writes and `ConfigItemUpdate` while Znuny leads (Mode A) or ITSM is off.** **Assumption:** GI `ConfigItemGet`/`ConfigItemSearch` read through the Mode A adapter; `ConfigItemCreate/Update/Delete` answer with a Znuny-style error (`<Op>.NotAvailable`, "Die CMDB wird in Znuny geführt (Modus A)"); the transition action `ConfigItemUpdate` is reported as "nur in Znuny wirksam" (as today). With `itsm.mode = off` all five operations answer `Operation.NotImplemented`.
5. **Switch details.** **Assumption:** the switch job (a) writes the Znuny read-only flag, (b) runs a final delta import, (c) sets `itsm.mode = tiqora` and records `itsm.switch`. After the switch the importer refuses to run. A rollback B→A is allowed as an explicit admin action (Tiqora's data stays; changes made in Tiqora after the switch are not visible in Znuny — the dialog says so).
6. **Switch without the add-on.** The read-only policy needs `TiqoraITSM` in Znuny. **Assumption:** the switch dialog demands a tick "In Znuny werden keine CIs mehr bearbeitet" when the admin says the add-on is not installed (Tiqora cannot detect it: the add-on never writes Tiqora data).
7. **Linking from Znuny through Tiqora's API.** **Assumption:** off by default (SysConfig `TiqoraITSM::Linking::Enabled` = 0). When on, the add-on calls `POST /api/v1/cmdb/links` with the API key stored in Znuny; Tiqora checks the permissions of the key's **bound Tiqora user** (not the Znuny agent); the Znuny agent login is only written into the link note for traceability.
8. **Customer interface page with the customer's CIs.** Exposes data to customers. **Assumption:** shipped with the add-on but its SysConfig frontend registration is `Valid="0"` (admin turns it on).
9. **Scope of the read-only policy.** The add-on blocks Znuny's agent web screens that change CIs (and ITSM change screens). Znuny's own GI `ConfigItem*` webservices, console commands and ImportExport cannot be blocked by a frontend module. **Assumption:** documented as a known gap; Tiqora shows a drift warning ("Seit der Umstellung wurden in Znuny N CIs geändert") on the import page.
10. **Priority matrix (`cip_allocate`) and service ITSM columns.** The itsm1 plan imports them (phase 1). **Assumption:** the full import run calls itsm1's two import functions as steps (so a re-run before the switch refreshes them); a dry run calls them in their dry-run mode or, if they have none, only counts the source rows.
11. **GI response shapes per Znuny version.** **Assumption:** Tiqora emulates the `ITSMConfigurationManagement` package of the Znuny framework found in the database (6.x shape for 6.x, 7.x shape for 7.x; when the two do not differ, one code path). A fresh install without Znuny uses the 7.3 shape.
12. **Long imports.** **Assumption:** job timeout 6 h, its own one-slot lane `itsm_import` (an import never blocks report jobs and never runs twice in parallel), interrupted jobs are requeued (the import is idempotent; max 3 claims).
13. **CLI.** **Assumption:** `tiqora itsm import [--dry-run]` and `tiqora itsm status`; no CLI switch (the switch is UI-only, so the acting admin is recorded).
14. **Users and timestamps.** Znuny and Tiqora share the `users` table. **Assumption:** imported CI/version `created_by` keep Znuny's user ids and `create_time`s; the import run records the admin who started it.

---

## Global Constraints

- Work in the worktree the executor is given; never push (a push to `origin main` deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data: `example.org`, `example.com`, `.test` hostnames, RFC 5737/3849 documentation IPs (`192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32`), invented names ("Switch Etage 3", "srv-db-01.example.test"). Never real PII.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q <paths>` (Docker needed; never plain `uv run pytest` — stale 3.9 pytest). Lint gates before **every** backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their own rows and are never added to `backend/tests/db_leak_baseline.txt`.
- **Write only `tiqora_*` tables, except through the existing Znuny write services** (`tiqora.domain.ticket_write_service`, `tiqora.znuny.history.history_add`, `tiqora.domain.dynamic_field_values`, `tiqora.znuny.cache_invalidation`). This plan **reads** Znuny's ITSM tables (`general_catalog*`, `configitem*`, `xml_storage`, `link_relation`, `link_object`, `link_type`, `link_state`, `virtual_fs*`, `change*`) only through itsm1's adapter, and writes only `tiqora_*` tables — the CMDB tables through the cmdb plan's services, its own tables directly.
- **Never JOIN `tiqora_*` tables with Znuny tables** (prod MariaDB mixes `utf8mb3_general_ci`/`utf8mb4_unicode_ci` → error 1267). Load keys from one side, then query the other side with an `IN` list (`bindparam(..., expanding=True)`, chunks of ≤ 500). The views of Task 2 join **only** `tiqora_*` tables; the Perl add-on filters them with bound parameters.
- **No `db.begin()` after autobegin**: a session that already executed a statement is in a transaction — `commit()`/`rollback()`, never `async with session.begin()` on it.
- **No synchronous endpoint may run longer than a few seconds** (production nginx cuts `/api/` after 90 s; anything that can exceed ~60 s is forbidden): import, dry run and switch are background jobs (`tiqora_job`) + polling (`GET /api/v1/jobs/{id}`). Admin status endpoints run bounded `COUNT`/`MAX` queries only.
- **Migrations:** Alembic two-chain setup; the one new migration (Task 2) goes into `backend/alembic/versions_tiqora` with revision = the **next free number at implementation time** after the current primary head (on 2026-10-12 the head in `tiqora-roadmap` is `20261010_0076`; itsm1 and cmdb add theirs before this plan runs — verify with `ls backend/alembic/versions_tiqora | tail -5` and `cd backend && uv run alembic heads`; never copy an id from this document). Its `down_revision` is the head **after** cmdb's migration (this plan's views depend on cmdb's tables). Skill `alembic-two-chain-new-head-forks-graph` applies; update any test that pins the head id.
- MySQL/MariaDB **and** PostgreSQL: every DB test runs on both dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern `backend/tests/test_admin_api.py:173`).
- Znuny golden peers 6.5 and 7.3 exist under `tests/golden` (`peers.yaml`, `Dockerfile.znuny`); itsm1 adds the ITSM variants (see contracts). Golden tests are manual (`GOLDEN=1`), marked `pytest.mark.golden`, never part of the PR gate.
- Seed ids only inside this plan's band **56000–56499** (sub-bands per task). Confirm free before seeding: `grep -rnE "\b56[0-4][0-9]{2}\b" backend/tests tests/golden` (free on 2026-10-12; other wave-4 plans pick bands in parallel — shift inside the band if needed). Seeded logins/names contain `itim56`.
- Znuny truthy values are Perl-truthy strings (`'0'` is false): use `tiqora.api.compat.mapping.perl.perl_truthy`, never `bool(...)`.
- Timestamps written by the application into `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`. Znuny `create_time` values are copied as stored (Znuny writes UTC with OTRSTimeZone=UTC).
- After any API model/route change: regenerate both openapi files and the client (skill `tiqora-openapi-regen-or-ci-wipes-schema`): `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; never hand-edit `schema.d.ts`.
- Frontend (`frontend/`): `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc); **never** run prettier/biome on `frontend/`. New i18n keys go into `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user as "Sie". Demo-mode mocks for every new screen (`frontend/src/demo/`).
- UI copy German first, plain words: "ITSM-Daten aus Znuny übernehmen", "Probelauf", "Übernehmen", "Auf Tiqora umstellen", "Zurück zu Znuny (Modus A)", "Bericht", "Konfigurationselemente (CIs)", "Klassen", "Verknüpfungen", "Anhänge". No "Mode B", "Delta", "Fingerprint", "Lane", "Port" in UI text (code/docs only). Znuny terms admins recognise stay: "CI", "Verwendungsstatus" (Znuny's German for deployment state), "Vorfallstatus" (incident state).
- Docs: user/admin docs in German address the reader as "Sie"; `docs/compatibility.md` and other English reference docs stay English.
- Znuny reference: core 6.5 at `<repo>/znuny-6.5.22` (contains `Kernel/System/ProcessManagement/TransitionAction/ConfigItemUpdate.pm` and `scripts/test/ProcessManagement/TransitionAction/ConfigItemUpdate.t`). The ITSM packages are **not** local: fetch `https://github.com/znuny/ITSMConfigurationManagement` (and `ITSMCore`, `GeneralCatalog`) at the tag matching the peer (6.5.x and 7.3.x) into the scratchpad when a step says "verify in ITSM source"; pin every result in a test before implementing. itsm1's `docs/itsm-import.md` (phase-0 survey) is the authoritative per-version schema record.
- Plan-specific:
  - Every module under `tiqora/itsm/znuny_format/` is pure (no DB, no network, no file system, synchronous) and has size/depth limits on untrusted input.
  - The importer never writes a CMDB table directly — only through `CmdbWritePort` (Task 1). It never reads a Znuny table directly — only through `ImportSource` (Task 1).
  - The Perl add-on reads only `tiqora_v1_itsm_ci`, `tiqora_v1_itsm_ci_links` and `tiqora_settings` (keys `itsm.mode`, `itsm.znuny_readonly`); it writes nothing to the database.
  - Every MCP tool of this plan calls `_assert_tool_allowed` and filters by class-group permission; all three are read-only and carry `readOnlyHint: true` (skill `tiqora-mcp-tools-hidden-by-mutating-default`).

## Review Focus

Inputs and conditions the spec implies but that are easy to miss; each line names the task whose tests pin it.

1. **A second, third, interrupted run of the importer** must not duplicate anything: same counts of CIs/versions/links/attachments in Tiqora, new Znuny versions appended in id order, untouched objects reported as "unverändert" — Task 9 (`test_rerun_is_idempotent_and_appends_new_versions`) and Task 13 (`test_requeued_job_resumes_without_duplicates`).
2. **CI attribute of type CI that points to a CI imported later** (forward reference across pages, or a reference to a deleted CI) must resolve to the Tiqora CI id or be kept as text with a report issue, never crash — Task 9 (`test_forward_ci_reference_resolved_in_second_pass`).
3. **A CMDB with 100k CIs** must import without loading all CIs into memory or holding one huge transaction: paged reads, one commit per batch, progress, cancel between batches — Task 9 (`test_paging_commits_per_batch_and_checks_cancel`).
4. **Somebody edits a CI in Znuny during or after the switch**: the read-only flag is set before the final delta import, the drift count after the switch is shown on the status endpoint and the page — Task 14 (`test_switch_sets_readonly_before_final_import`, `test_status_reports_drift_after_switch`).
5. **An agent (GI, MCP, Znuny widget) asks for a CI of a class whose group they have no right on**: it must not appear (GI `AccessDenied`/omitted, MCP omitted, widget row hidden) — Tasks 21, 25, 26 (`test_get_denies_class_without_ro`, `test_cmdb_search_hides_foreign_classes`, golden `test_widget_hides_ci_of_foreign_class`).

---

## Cross-plan contracts

The other wave-4 plans are written in parallel; this plan cannot see their final names. Every interface below is stated as this plan **consumes** it, with its **owner**. Task 1 checks each one against the merged code and maps it in `backend/src/tiqora/itsm/importer/facades.py` (the only file allowed to import itsm1/cmdb internals for the importer; GI/MCP/action code also go through the facades). If an owner shipped a different name or shape, only `facades.py` changes; if an owner did not ship a listed item at all, the fallback column says which task of this plan adds it.

| # | Contract | Owner | Consumed by (tasks) | Expected shape | Fallback if missing |
|---|---|---|---|---|---|
| C1 | ITSM mode | **itsm1** | 1, 14, 19–27 | Settings section `itsm`, field `mode`, stored in `tiqora_settings` under key `itsm.mode` (`off` \| `znuny_readthrough` \| `tiqora`; absent/empty = automatic). `async def effective_itsm_mode(session) -> Literal["off","znuny_readthrough","tiqora"]` (auto rule: Znuny ITSM tables present → `znuny_readthrough`, else `tiqora`). `async def znuny_itsm_present(session) -> bool`. | Task 1 adds both functions in `facades.py` (presence = `general_catalog` + `configitem` tables exist via `sqlalchemy.inspect`) |
| C2 | Mode A→B guard | **itsm1** (setting validation) | 14 | The settings section rejects saving `mode = tiqora` while `znuny_itsm_present()` is true and `tiqora_settings['itsm.switch']` is absent (422, "Die Umstellung auf Tiqora erfolgt über ITSM-Daten aus Znuny übernehmen"). This plan's switch job writes `itsm.switch` and `itsm.mode` in one transaction. | Task 14 adds the guard to itsm1's section validator |
| C3 | Znuny ITSM read adapter | **itsm1** (`tiqora/itsm/znuny_adapter/`) | 1, 7–12, 21–22, 25 | Schema-profile detection + readers for general catalog (incl. `Functionality` preference), classes, definitions (raw string + version), CI headers (paged by id), CI versions **with attribute data in Znuny `XMLData` form** (`{Key: [{"Content": v, SubKey: [...]}, ...]}`) for both 6.x (`xml_storage`) and 7.x (per phase-0 survey), `link_relation` rows involving `ITSMConfigItem`, read-only CI search for Mode A. | Task 1 maps whatever itsm1 ships onto `ImportSource`; readers itsm1 does not have (attachments `virtual_fs`, change counts, link rows by CI id) are implemented by this plan in `importer/source_extra.py` (Task 11/12) |
| C4 | Znuny definition parsing | **this plan** (`znuny_format/perl_literal.py`, `definition.py`) | 3, 8, 19, 21 | `parse_znuny_definition(raw: str) -> list[dict]` for 6.x Perl literal and 7.x YAML. itsm1 may import it; if itsm1 already shipped an equivalent, Task 3 keeps one implementation (whichever passes Task 3's vectors) and re-exports it under this name. | — |
| C5 | CMDB tables | **cmdb** | 2 (views), 9, 14 | Spec §B names verbatim: `tiqora_cmdb_catalog`, `tiqora_cmdb_class` (`group_id`), `tiqora_cmdb_class_definition`, `tiqora_cmdb_ci` (`number`, `class_id`, `name`, `deployment_state_id`, `incident_state_id`, `current_version_id`, `created`, `changed`), `tiqora_cmdb_ci_version`, `tiqora_cmdb_ci_attr`, `tiqora_itsm_link` (`source_kind`, `source_id`, `target_kind`, `target_id`, `link_type`, `created_by`, `created`) + link-type table. `source_id`/`target_id` are strings (customer logins). Kinds: `ci`, `ticket`, `service`, `kb`, `customer_user`, `customer_company`. | none — Task 2 adapts the view SQL to the real column names (the view columns stay as defined in D2) |
| C6 | CMDB class schema JSON + Znuny converter | **cmdb** (class designer "YAML import of Znuny class definitions") | 4, 8, 19, 21 | `znuny_definition_to_schema(definition: list[dict]) -> (schema: dict, issues: list[str])`; schema attribute entries carry `key`, `name`, `type`, `required`, `searchable`, `count_min`, `count_max`, `count_default`, type options (catalog class, CI class filter, …) and `sub` (nested). | Task 4 implements the Znuny→Tiqora type map in `znuny_format/definition.py` against cmdb's schema model |
| C7 | CI attribute JSON (version `attributes`) | **cmdb** | 4, 9, 19, 21–23, 25 | Assumed: `{key: [entry, ...]}`, `entry = {"value": scalar \| None, "sub": {subkey: [entry, ...]}}` (`sub` omitted when empty). | Only `znuny_format/xmldata.py` depends on it — adapt there |
| C8 | CMDB write service | **cmdb** | 1, 7–11, 20, 23–24 | Functions to: upsert catalog item; upsert class; add class definition (no new version when equal); create CI with a given number/created/created_by; add CI version with given created/created_by, "unchanged → no new version" rule, attribute index maintenance, current-pointer update; ensure link type; add link (idempotent); add CI attachment; delete CI; recompute incident states for CI ids. | Missing functions are added by Task 1 to `tiqora/cmdb/import_api.py` (thin wrappers over cmdb's models + its own index/propagation helpers), coordinated with the cmdb owner |
| C9 | CMDB read + permission + impact | **cmdb** | 21–22, 25 | `visible_class_ids(session, user_id, perm: Literal["ro","rw"]) -> set[int]` (class `group_id` × Znuny group rights); CI get/search (name/number/class/states/attribute filters, paging); `impact(session, ci_id, *, max_depth, user_id)` → services, customers, open tickets, CIs reachable via propagating link types. | none (core CMDB feature); Task 25 skips `cmdb_impact` with a clear message only if impact is absent, and reports it |
| C10 | REST `/api/v1/cmdb/links` + API-key area `cmdb` | **cmdb** | 27 | `POST /api/v1/cmdb/links` `{ci_id, target_kind, target_id, link_type, note?}` → 201; area `cmdb` in `tiqora.domain.api_key_scopes.API_KEY_AREAS` with prefix `/api/v1/cmdb`. | Task 27 documents linking as unavailable and leaves the SysConfig switch off |
| C11 | `docs/itsm.md` | **itsm1** creates the file (German, "Sie"); **cmdb** owns its CMDB chapters; **this plan** owns exactly the chapters `## Koexistenz mit Znuny` (with `### Was Sie in welchem Modus sehen`, `### Umstellung auf Tiqora (Ablauf)`, `### Was bleibt, was entfällt (je Znuny-Bildschirm)`, `### Zurück zu Modus A`), `## ITSM-Daten aus Znuny übernehmen` and `## Znuny-Erweiterung TiqoraITSM`. `docs/itsm-import.md`: itsm1 owns the per-version schema chapters, this plan appends `## Importer: Zuordnung und Bericht`. | If `docs/itsm.md` does not exist, Task 29 creates it with only this plan's chapters and a one-line intro |
| C12 | Golden ITSM peers + Layer-A ITSM fixtures | **itsm1** | 7–12, 28 | `peers.yaml` variants (expected ids `znuny-6.5-itsm`, `znuny-7.3-itsm`) with GeneralCatalog/ITSMCore/ITSMConfigurationManagement (+ IncidentProblem/Change) installed; backend helper `backend/tests/_itsm_znuny_schema.py` with `create_znuny_itsm_tables(sync_url: str, profile: str) -> None` / `drop_znuny_itsm_tables(sync_url: str) -> None` creating the real DDL on the test containers. | Task 7 adds the Layer-A helper from the DDL recorded in `docs/itsm-import.md`; Task 28 adds the peer variant (OPM install in `Dockerfile.znuny` behind `GOLDEN_ITSM=1`) |
| C13 | Views `tiqora_v1_itsm_ci`, `tiqora_v1_itsm_ci_links` | **this plan** | 26–27 (Perl), any later plan | Columns frozen in D2; changing them means a `tiqora_v2_*` view, never an edit of v1. | — |
| C14 | `tiqora_itsm_import_map` | **this plan** | itsm1 Mode A deep links may read it later | `(source_kind, source_id) → target_id` | — |
| C15 | Staged data while Znuny leads | **itsm1** (Mode A read path) + **cmdb** (CMDB API/UI) | 9, 12, 14 | In `znuny_readthrough` the CMDB API/UI, the ticket CI panel and reports read Znuny through the adapter and ignore `tiqora_cmdb_*` rows, so an import before the switch is invisible to agents (staging); from `tiqora` on they read the Tiqora tables. | Task 14 adds a test that the CMDB list endpoint in mode A does not return an imported-only CI; if it does, the switch dialog and the import confirm text say "Übernommene Daten sind sofort in Tiqora sichtbar" and the open question is raised again |

---

## Design (fixed)

### D0. File map

Backend (new unless marked):
- `backend/src/tiqora/itsm/importer/__init__.py` — package docstring only.
- `backend/src/tiqora/itsm/importer/ports.py` — source records (frozen dataclasses), `ImportSource`, `CmdbWritePort`, `CmdbReadPort`, `ItsmModePort` protocols.
- `backend/src/tiqora/itsm/importer/facades.py` — the only bridge to itsm1/cmdb code: `ZnunyAdapterSource`, `CmdbServiceWriter`, `CmdbServiceReader`, `ItsmModeFacade`, `get_ports(session) -> Ports`.
- `backend/src/tiqora/itsm/importer/source_extra.py` — readers not in itsm1's adapter (attachments, change counts, link rows by CI id) — read-only, IN-lists.
- `backend/src/tiqora/itsm/importer/mapstore.py` — `tiqora_itsm_import_map` access + `fingerprint()`.
- `backend/src/tiqora/itsm/importer/report.py` — `ImportReport`, `StepCounts`, `ImportIssue`, CSV export.
- `backend/src/tiqora/itsm/importer/writer.py` — `DryRunWriter` (counts, fake ids) implementing `CmdbWritePort`.
- `backend/src/tiqora/itsm/importer/steps/{__init__,catalog,classes,cis,links,attachments,delegated}.py`
- `backend/src/tiqora/itsm/importer/pipeline.py` — `run_import(...)`.
- `backend/src/tiqora/itsm/importer/jobs.py` — `ITSM_IMPORT_JOB`, `ITSM_SWITCH_JOB`.
- `backend/src/tiqora/itsm/importer/switch.py` — switch, rollback, drift, status.
- `backend/src/tiqora/itsm/importer/schemas.py` — API models.
- `backend/src/tiqora/itsm/znuny_format/{__init__,perl_literal,definition,xmldata,stringified,gi_cixml}.py` — pure conversions.
- `backend/src/tiqora/itsm/addon_views.py` — view names + DDL per dialect.
- `backend/src/tiqora/itsm/config_item_update.py` — transition action handler.
- `backend/src/tiqora/api/compat/config_items.py` — `op_config_item_{create,get,search,update,delete}`.
- `backend/src/tiqora/api/v1/admin/itsm_import.py` — admin API.
- `backend/src/tiqora/cli/itsm.py` — CLI.
- `backend/src/tiqora/mcp_server/cmdb_tools.py` — MCP tools (imported at the end of `server.py`).
- Modified: `backend/src/tiqora/db/tiqora/models.py` (2 models), `backend/src/tiqora/extensions/core.py` (job handlers), `backend/src/tiqora/jobs/registry.py` (`LANE_SLOTS["itsm_import"] = 1`), `backend/src/tiqora/process/actions/registry.py` (`ConfigItemUpdate` handler), `backend/src/tiqora/process/engine.py` (docstring), `backend/src/tiqora/api/compat/webservices.py` (`OPERATION_TYPES`), `backend/src/tiqora/api/compat/router.py` (dispatch + canonical routes), `backend/src/tiqora/api/v1/admin/__init__.py` (router), `backend/src/tiqora/main.py` (CLI), `backend/src/tiqora/mcp_server/server.py` (import line).
- Migration: `backend/alembic/versions_tiqora/<next>_itsm_import.py`.

Frontend: `frontend/src/lib/itsmImportApi.ts`, `frontend/src/routes/admin/ItsmImportPage.tsx` (+ `.test.tsx`), `frontend/src/components/itsm-import/{ImportStatusCard,ImportRunReport,SwitchDialog,RollbackDialog}.tsx` (+ tests), `frontend/src/demo/itsmImportMock.ts` (+ registration in `frontend/src/demo/handlers.ts`), `frontend/src/router.tsx`, `frontend/src/lib/adminSearch.ts`, `frontend/src/i18n/locales/{en,de}.json` (+ propagated), `frontend/src/components/process-designer/panels/ActionPanel.test.tsx` (fixture flip).

Znuny add-on: `packages/znuny-addon/TiqoraITSM/` (`TiqoraITSM.sopm`, `LICENSE`, `install/README.md`, `Kernel/Config/Files/XML/TiqoraITSM.xml`, `Kernel/System/TiqoraITSM.pm`, `Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm`, `Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt`, `Kernel/Modules/TiqoraITSMReadOnly.pm`, `Kernel/Modules/CustomerTiqoraITSM.pm`, `Kernel/Output/HTML/Templates/Standard/CustomerTiqoraITSM.tt`, `Kernel/Modules/AgentTiqoraITSMLink.pm`, `Kernel/Language/de_TiqoraITSM.pm`), `packages/znuny-addon/README.md` (mention).

Golden: `tests/golden/test_itsm_import.py`, `tests/golden/test_gi_config_items.py`, `tests/golden/test_tiqoraitsm_addon.py`.

Docs: `docs/itsm.md` (chapters per C11), `docs/itsm-import.md` (chapter per C11), `docs/compatibility.md`, `docs/parallel-operation.md`, `docs/process-management.md`, `docs/guide/znuny-to-tiqora.md` (pointer), `packages/znuny-addon/TiqoraITSM/install/README.md`.

### D1. Tables (Task 2)

`tiqora_itsm_import_run` — one row per dry run, import or switch:

| column | type | notes |
|---|---|---|
| `id` | BigInteger PK autoincrement | |
| `kind` | String(20) | `dry_run` \| `import` \| `switch` \| `rollback` |
| `status` | String(20) | `running` \| `done` \| `failed` \| `cancelled` |
| `job_id` | BigInteger NULL | `tiqora_job.id` (no FK; jobs are purged) |
| `started_by` | Integer | Znuny `users.id` (no FK) |
| `started_at` | DateTime | naive UTC |
| `finished_at` | DateTime NULL | |
| `profile` | String(50) NULL | Znuny ITSM schema profile reported by the adapter |
| `summary` | Text (MEDIUMTEXT on MariaDB) | JSON of `ImportReport.summary()` (counts per step + first 200 issues) |
| `message` | String(500) NULL | failure text |

Index `ix_tiqora_itsm_import_run_started` on (`started_at`).

`tiqora_itsm_import_map` — idempotency:

| column | type | notes |
|---|---|---|
| `id` | BigInteger PK | |
| `source_kind` | String(20) | `catalog`, `class`, `definition`, `ci`, `version`, `link`, `attachment` |
| `source_id` | String(255) | Znuny id as string; links: `"<SrcObj>:<SrcKey>><TgtObj>:<TgtKey>:<TypeName>"`; attachments: `"<ci_id>/<filename>"` |
| `target_id` | BigInteger | Tiqora id (catalog/class/definition/ci/version/attachment id; links: 0) |
| `fingerprint` | String(64) | sha256 hex of the canonical JSON of the source record |
| `run_id` | BigInteger | last run that touched the row |
| `updated_at` | DateTime | |

Unique `uq_tiqora_itsm_import_map_src` on (`source_kind`, `source_id`); index on (`source_kind`, `target_id`).

Settings keys written by this plan into `tiqora_settings` (plain rows, not a settings section): `itsm.switch` = JSON `{"switched_at": "<iso utc>", "by": <user_id>, "run_id": <id>}` (absent = not switched), `itsm.znuny_readonly` = `"1"`/`"0"`.

### D2. Views for the Znuny add-on (Task 2) — contract C13

Both views join only `tiqora_*` tables. Created by the migration with `op.execute`; the same SQL text lives in `tiqora/itsm/addon_views.py` (`VIEW_SQL: dict[str, str]`) for test fixtures; a test compares the migration's strings with the module constants (migrations must not import app code). Dialect differences: none (plain SQL, `UNION ALL`, no casts — `tiqora_itsm_link.source_id/target_id` are strings per C5).

`tiqora_v1_itsm_ci`: `ci_id` (int), `number` (str), `name` (str), `class_id` (int), `class_key` (str), `class_name` (str), `class_group_id` (int, Znuny `permission_groups.id`), `deployment_state` (str, catalog name), `deployment_functionality` (str: `preproductive` \| `productive` \| `postproductive` \| NULL), `incident_state` (str), `incident_functionality` (str: `operational` \| `warning` \| `incident` \| NULL), `changed` (datetime, naive UTC).

```sql
CREATE VIEW tiqora_v1_itsm_ci AS
SELECT ci.id AS ci_id, ci.number AS number, ci.name AS name,
       cl.id AS class_id, cl.key AS class_key, cl.name AS class_name, cl.group_id AS class_group_id,
       ds.name AS deployment_state, ds.functionality AS deployment_functionality,
       ist.name AS incident_state, ist.functionality AS incident_functionality,
       ci.changed AS changed
FROM tiqora_cmdb_ci ci
JOIN tiqora_cmdb_class cl ON cl.id = ci.class_id
LEFT JOIN tiqora_cmdb_catalog ds ON ds.id = ci.deployment_state_id
LEFT JOIN tiqora_cmdb_catalog ist ON ist.id = ci.incident_state_id
```

(`cl.key` must be quoted where `key` is reserved: MariaDB backticks, PostgreSQL double quotes — `addon_views.view_sql(dialect)` renders the quoting; the migration does the same with `op.get_bind().dialect.name`.)

`tiqora_v1_itsm_ci_links`: one row per (CI, linked object) in **both** directions: `ci_id` (int), `object_kind` (str: `ticket`, `customer_user`, `customer_company`, `service`, `ci`, `kb`), `object_key` (str), `link_type` (str key), `direction` (`source` | `target`: the CI's side), `created` (datetime).

Per C5 a CI is stored in `tiqora_itsm_link` with kind `ci` and its id as a decimal string. The view casts the **link side** (never the CI side, so no join is needed and no index is defeated); the cast sits inside a `CASE` so PostgreSQL never casts a customer login:

```sql
CREATE VIEW tiqora_v1_itsm_ci_links AS
SELECT CASE WHEN l.source_kind = 'ci' THEN CAST(l.source_id AS {INT}) END AS ci_id,
       l.target_kind AS object_kind, l.target_id AS object_key,
       l.link_type AS link_type, 'source' AS direction, l.created AS created
FROM tiqora_itsm_link l WHERE l.source_kind = 'ci'
UNION ALL
SELECT CASE WHEN l.target_kind = 'ci' THEN CAST(l.target_id AS {INT}) END,
       l.source_kind, l.source_id, l.link_type, 'target', l.created
FROM tiqora_itsm_link l WHERE l.target_kind = 'ci'
```

`{INT}` = `SIGNED` on MariaDB/MySQL, `BIGINT` on PostgreSQL (`addon_views.view_sql(name, dialect)` renders it; the migration renders the same way). If cmdb's link model stores CI ids in a separate numeric column, Task 2 uses that column instead of the cast and records it in the module docstring — the view columns stay as listed. The Perl side reads in two queries (links by `object_kind` + `object_key`, then `tiqora_v1_itsm_ci WHERE ci_id IN (…)`), never a join.

### D3. Ports (Task 1)

```python
# backend/src/tiqora/itsm/importer/ports.py
@dataclass(frozen=True, slots=True)
class ZnunyCatalogItem:
    id: int; catalog_class: str; name: str; valid: bool; comment: str | None; functionality: str | None

@dataclass(frozen=True, slots=True)
class ZnunyClassDefinition:
    id: int; class_id: int; version: int; raw: str; created_at: datetime; created_by: int

@dataclass(frozen=True, slots=True)
class ZnunyCi:
    id: int; number: str; class_id: int; last_version_id: int | None
    deployment_state_id: int | None; incident_state_id: int | None
    created_at: datetime; created_by: int; changed_at: datetime; changed_by: int

@dataclass(frozen=True, slots=True)
class ZnunyCiVersion:
    id: int; ci_id: int; name: str; definition_id: int
    deployment_state_id: int | None; incident_state_id: int | None
    created_at: datetime; created_by: int
    xml_data: Mapping[str, Any]      # Znuny XMLData: {Key: [{"Content": v, Sub: [...]}, ...]}

@dataclass(frozen=True, slots=True)
class ZnunyLink:
    source_object: str; source_key: str; target_object: str; target_key: str
    type_name: str; state_name: str; created_at: datetime; created_by: int
    @property
    def map_key(self) -> str:
        return f"{self.source_object}:{self.source_key}>{self.target_object}:{self.target_key}:{self.type_name}"

@dataclass(frozen=True, slots=True)
class ZnunyAttachment:
    ci_id: int; filename: str; content_type: str; size: int; data: bytes | None  # None: FS backend
    created_at: datetime

class ImportSource(Protocol):
    async def profile(self) -> str | None: ...                      # None = Znuny ITSM not installed
    async def catalog_items(self) -> list[ZnunyCatalogItem]: ...
    async def class_definitions(self) -> list[ZnunyClassDefinition]: ...
    async def ci_count(self) -> int: ...
    async def ci_ids_page(self, after_id: int, limit: int) -> list[int]: ...
    async def cis(self, ids: Sequence[int]) -> list[ZnunyCi]: ...
    async def versions(self, ci_ids: Sequence[int]) -> dict[int, list[ZnunyCiVersion]]: ...  # ascending id
    async def links_for_cis(self, ci_ids: Sequence[int]) -> list[ZnunyLink]: ...
    async def attachments(self, ci_ids: Sequence[int], *, with_data: bool) -> list[ZnunyAttachment]: ...
    async def change_counts(self) -> dict[str, int]: ...             # {"changes": n, "workorders": m}
    async def max_ci_change_time(self) -> datetime | None: ...
    async def count_cis_changed_since(self, since: datetime) -> int: ...

class CmdbWritePort(Protocol):
    async def upsert_catalog_item(self, *, catalog_class: str, key: str, name: str, valid: bool, functionality: str | None) -> int: ...
    async def upsert_class(self, *, key: str, name: str, group_id: int | None, valid: bool) -> int: ...
    async def add_class_definition(self, class_id: int, schema: dict[str, Any], *, created_at: datetime, created_by: int) -> tuple[int, int]: ...  # (definition_id, version)
    async def create_ci(self, *, number: str, class_id: int, name: str, deployment_state_id: int | None, created_at: datetime, created_by: int) -> int: ...
    async def add_ci_version(self, ci_id: int, *, definition_version: int, name: str, deployment_state_id: int | None, incident_state_id: int | None, attributes: dict[str, Any], change_note: str | None, created_at: datetime, created_by: int) -> int | None: ...  # None = unchanged
    async def latest_version_id(self, ci_id: int) -> int | None: ...
    async def ensure_link_type(self, *, key: str, name: str, directed: bool, propagates_incidents: bool) -> None: ...
    async def add_link(self, *, source_kind: str, source_id: str, target_kind: str, target_id: str, link_type: str, created_at: datetime, created_by: int) -> bool: ...  # False = existed
    async def add_ci_attachment(self, ci_id: int, *, filename: str, content_type: str, data: bytes, created_at: datetime, created_by: int) -> int: ...
    async def delete_ci(self, ci_id: int, *, user_id: int) -> None: ...
    async def recompute_incident_states(self, ci_ids: Sequence[int]) -> None: ...

class CmdbReadPort(Protocol):
    async def visible_class_ids(self, user_id: int, perm: Literal["ro", "rw"]) -> set[int]: ...
    async def get_ci(self, ci_id: int, *, version_id: int | None = None) -> CiView | None: ...
    async def ci_id_by_number(self, number: str) -> int | None: ...
    async def search_cis(self, query: CiQuery, *, class_ids: set[int], limit: int, offset: int) -> list[int]: ...
    async def class_by_key_or_name(self, value: str) -> CmdbClassView | None: ...
    async def catalog_item(self, catalog_class: str, name_or_id: str | int) -> CatalogView | None: ...
    async def impact(self, ci_id: int, *, max_depth: int, user_id: int) -> ImpactView: ...

class ItsmModePort(Protocol):
    async def effective_mode(self) -> Literal["off", "znuny_readthrough", "tiqora"]: ...
    async def znuny_itsm_present(self) -> bool: ...
```

`CiView`, `CiQuery`, `CmdbClassView`, `CatalogView`, `ImpactView` are frozen dataclasses in `ports.py`:
`CiView(id, number, name, class_id, class_key, class_name, deployment_state, deployment_state_id, incident_state, incident_state_id, version_id, definition_version, attributes: dict, schema: dict, created_at, created_by, changed_at, attachments: list[tuple[int, str, str, int]])`;
`CiQuery(number: str | None, name: str | None, class_keys: list[str], deployment_states: list[str], incident_states: list[str], attributes: dict[str, str], created_after: datetime | None, created_before: datetime | None, changed_after: datetime | None, changed_before: datetime | None, text: str | None)` (`*` wildcards as in Znuny);
`CmdbClassView(id, key, name, group_id, schema, definition_version)`; `CatalogView(id, catalog_class, key, name, functionality)`;
`ImpactView(cis: list[tuple[int, str, str]], services: list[tuple[int, str]], customers: list[tuple[str, str]], open_tickets: list[tuple[int, str, str]], truncated: bool)`.

`Ports` = `@dataclass(frozen=True) class Ports: source: ImportSource; writer: CmdbWritePort; reader: CmdbReadPort; mode: ItsmModePort`; `get_ports(session: AsyncSession) -> Ports` (facades bound to the given session; the caller owns the transaction).

### D4. Conversions (`tiqora.itsm.znuny_format`, Tasks 3–5)

- `perl_literal.parse_perl_literal(text: str, *, max_depth: int = 64, max_nodes: int = 200_000) -> Any` — safe subset parser for Znuny 6.x definitions: `[ ]`, `{ }`, `=>`, `,`, `'...'` (with `\\` and `\'`), `"..."` (with `\\`, `\"`, `\n`, `\t`; `$`/`@` interpolation → `PerlLiteralError`), integers/decimals, barewords as hash keys, `undef` → `None`, `#` comments, trailing commas. Anything else → `PerlLiteralError(position, message)`. No `eval`.
- `definition.parse_znuny_definition(raw: str) -> list[dict]` — tries YAML (`yaml.safe_load`) when `raw.lstrip()` starts with `---` or `- ` or the profile says 7.x, otherwise Perl literal; result must be a list of dicts with `Key`; 7.x definitions that wrap attributes (verify in ITSM 7.3 source: `Definition`/`Sections`/`Attributes`?) are normalised to the 6.x list form here.
- `definition.to_tiqora_schema(znuny_definition) -> (schema, issues)` — delegates to cmdb's converter (C6) through the facade.
- `xmldata.xmldata_to_attributes(xml_data, schema, *, ref_resolver) -> (attributes, issues)` and `attributes_to_xmldata(attributes, schema) -> dict` — walk the schema; `Content` → `value`; sub keys → `sub`; values of type CI are mapped through `ref_resolver.ci(znuny_ci_id) -> int | None`, GeneralCatalog values through `ref_resolver.catalog(znuny_gc_id) -> int | None` (unresolved → keep the raw value as text + issue `unresolved_reference`); Date/DateTime normalised to ISO (`YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS`); keys not in the schema → kept under their key + issue `unknown_attribute`.
- `stringified.xmldata_to_stringified(xml_data) -> dict[str, str]` and `stringified.stringified_to_xmldata(stringified, definition, original, *, separator=",", customer_lookup) -> dict` — exact ports of `XMLData2StringifiedXMLData` / `StringifiedXMLData2XMLData` and the `Key::` expansion of `ConfigItemAttributesGet` (`ConfigItemUpdate.pm` lines ~300–720).
- `gi_cixml.cixml_to_xmldata(cixml, definition) -> (xml_data, errors)` and `xmldata_to_cixml(xml_data, definition) -> dict` — the GI `CIXMLData` format (verify in ITSM source `Kernel/GenericInterface/Operation/ConfigItem/Common.pm`: `CheckXMLData`, `ConvertDataToInternal`/`FormatXMLData`, `InvertFormatData`, `ReplaceInputDate`/`ReplaceInputDateTime`, GeneralCatalog name ↔ id, Customer/CustomerCompany validation).

### D5. Importer pipeline (Tasks 6–13)

Order of steps (each a function `async def run_step(ctx: StepContext) -> None`, report counts under its name):

1. `catalog` — every general catalog item → `upsert_catalog_item` (map `catalog`).
2. `classes` — classes (general catalog class `ITSM::ConfigItem::Class`) → `upsert_class` (group from the class's `Permission` preference, verify key in GeneralCatalog source), then every definition version in ascending `version` → `add_class_definition` (map `definition` → (definition_id, version)).
3. `cis_pass1` — page CI ids (`page_size`, default 500); for unmapped CIs `create_ci` (keeping `number`, `created_at`, `created_by`); map `ci`. Commit per page.
4. `cis_pass2` — page again; for each CI load versions; skip versions already mapped; **conflict rule**: if `writer.latest_version_id(ci)` is not a mapped version target → issue `changed_in_tiqora`, skip the CI; otherwise for each new version in ascending id: convert attributes (D4, resolver backed by the map) and `add_ci_version` (a `None` return still maps the Znuny version to the latest Tiqora version id). Commit per page; `check_cancelled()` + `progress()` per page.
5. `links` — per page of CI ids `links_for_cis`; map Znuny objects: `ITSMConfigItem` → `ci` (via map), `Ticket` → `ticket` (key as is), `Service` → `service`; `FAQ`/`ITSMChange`/`ITSMWorkOrder`/others → skipped, counted under "nicht übernommen (Objekttyp)"; link types → `ensure_link_type` (key = Znuny type name; `propagates_incidents` from Znuny's `ITSMConfigItem::LinkType` / `IncidentLinkTypeDirection` SysConfig — verify name, read through `tiqora.znuny.sysconfig.SysConfig`; default false); state `Valid` only (others skipped). `add_link` idempotent; map `link`.
6. `attachments` — per page; DB-backend data imported, FS backend → issue `attachment_in_filesystem`; map `attachment` with fingerprint (size + sha256).
7. `priority_matrix`, `service_meta` — delegated to itsm1 (C3 note, open question 10).
8. `changes` — counts only (open question 1).
9. `deleted_in_znuny` — map rows of kind `ci` whose source id no longer exists in Znuny → issue list (open question 2).
10. `incident_states` — `recompute_incident_states` for every touched CI id (batched by 500).

`StepContext` = `(session_factory, ports_factory, run_id, dry_run, page_size, report, progress, check_cancelled, mapstore)`. Dry run: `DryRunWriter` instead of the facade writer, mapstore in read-only mode (lookups yes, writes into an in-memory overlay), no commits of CMDB data; it still writes its `tiqora_itsm_import_run` row.

Report: `ImportReport` with `steps: dict[str, StepCounts(created, updated, unchanged, skipped, problems)]` and `issues: list[ImportIssue(step, code, object_ref, message)]`; summary JSON stores counts + first 200 issues; the full issue list is a CSV job file (`itsm-import-bericht.csv`, `;`-separated, UTF-8 BOM).

### D6. Jobs (Task 13)

- `ITSM_IMPORT_JOB = JobHandler(kind="itsm.import", payload_model=ItsmImportPayload, run=run_itsm_import_job, title_key="jobs.kinds.itsm.import", timeout_seconds=6 * 3600, lane="itsm_import", max_attempts=3, requeue_if_interrupted=True)`; payload `ItsmImportPayload(user_id: int, dry_run: bool, run_id: int, page_size: int = 500)`.
- `ITSM_SWITCH_JOB = JobHandler(kind="itsm.switch", payload_model=ItsmSwitchPayload, run=run_itsm_switch_job, title_key="jobs.kinds.itsm.switch", timeout_seconds=6 * 3600, lane="itsm_import", max_attempts=3, requeue_if_interrupted=True)`; payload `ItsmSwitchPayload(user_id: int, run_id: int, addon_installed: bool)`.
- `LANE_SLOTS` gains `"itsm_import": 1`. Dedupe key `"itsm_import"` for both kinds (a switch and an import never run at the same time; the API refuses a second start with 409).
- The handlers never call `ctx.checkpoint` (every step is idempotent, a requeue is safe).

### D7. Switch, rollback, drift (Task 14)

- Preconditions (checked by the API, re-checked by the job): `znuny_itsm_present()`; effective mode is `znuny_readthrough`; no `itsm.switch` row; at least one `import` run with status `done`; no active import/switch job; body `{confirm: "UMSTELLEN", addon_installed: bool, edits_stopped: bool}` with `edits_stopped` required true when `addon_installed` is false.
- Job: (1) write `itsm.znuny_readonly = "1"`, commit; (2) run the full pipeline (not dry); (3) on success write `itsm.mode = "tiqora"` (via `ItsmModePort`/settings store) and `itsm.switch` JSON in one transaction, invalidate nothing in Znuny (no Znuny data changed); on failure set `itsm.znuny_readonly = "0"` again and fail the run with the message.
- Rollback (`POST /rollback`, `{confirm: "ZURÜCK"}`), synchronous (two setting writes): delete `itsm.switch`, set `itsm.mode = "znuny_readthrough"`, set `itsm.znuny_readonly = "0"`, record a `rollback` run row.
- After the switch the import API answers 409 "Die ITSM-Daten werden bereits in Tiqora geführt."
- Drift: `status()` returns `znuny_changes_since_switch = source.count_cis_changed_since(switched_at)` (bounded `COUNT` on `configitem.change_time`) when switched.

### D8. Admin API (Task 15) — `/api/v1/admin/itsm/import`, admin only (`AdminUser`)

- `GET /status` → `ItsmImportStatusOut{mode, znuny_itsm_present, profile, switched: SwitchInfoOut | None, readonly_flag: bool, last_run: ImportRunOut | None, active_job_id: int | None, can_import: bool, can_switch: bool, blocked_reason: str | None, znuny_counts: {cis, classes, changes, workorders} | None, znuny_changes_since_switch: int | None}`.
- `POST /runs` `{dry_run: bool}` → 202 `JobAccepted` (409 when blocked).
- `GET /runs?limit=20` → `list[ImportRunOut{id, kind, status, started_at, finished_at, started_by_name, job_id, steps: dict[str, StepCountsOut], issues: list[ImportIssueOut{step, code, object_ref, message, explanation}], issue_count: int}]` (`explanation` = `ISSUE_CODES[code]`, Task 16).
- `GET /runs/{id}` → `ImportRunOut`.
- `POST /switch` `{confirm, addon_installed, edits_stopped}` → 202 `JobAccepted` (400 wrong confirm, 409 preconditions).
- `POST /rollback` `{confirm}` → 200 `ItsmImportStatusOut`.

### D9. GI `ConfigItem*` (Tasks 21–24)

- `OPERATION_TYPES` gains `"ConfigItem::ConfigItemCreate": "ConfigItemCreate"`, `…Get`, `…Search`, `…Update`, `…Delete`.
- Canonical routes — verify against `development/webservices/GenericConfigItemConnectorREST.yml` of ITSMConfigurationManagement 6.5 (expected: `POST /ConfigItem` create, `GET /ConfigItem/:ConfigItemID` get, `POST /ConfigItemSearch` search, `PATCH /ConfigItem/:ConfigItemID` update, `DELETE /ConfigItem/:ConfigItemID` delete); add them under `/znuny-compat/` exactly as the YAML says, and the SOAP dispatch via `_CANONICAL_OPS`.
- Auth: `_auth_from_params` (session or user/password); agents only (verify whether 7.x allows customer users for Get/Search — if yes, customers see only CIs linked to them or their company, via `CmdbReadPort.search_cis` with a customer filter); permission: `ro` on the class group for Get/Search, `rw` for Create/Update/Delete (verify the exact Znuny check: `ITSMConfigItem->Permission(Scope => 'Class'|'Item', Type => 'ro'|'rw')`).
- Mode gating per open question 4. In Mode A, Get/Search use itsm1's adapter (C3) — same response shape.
- Error codes and messages: copied from the ITSM source per operation (`<Op>.MissingParameter`, `<Op>.AuthFail`, `<Op>.AccessDenied`, `<Op>.InvalidParameter`, …), pinned in tests with the exact strings found.

### D10. `ConfigItemUpdate` (Tasks 19–20)

Handler `tiqora.itsm.config_item_update.action_config_item_update(session, config, ticket_id, user_id, sysconfig)`: mode gate (only `tiqora`; otherwise log + collect as unsupported like today, return); `UserID`/`DynamicField_UserID` override; smart tags are already replaced by the engine (`config` arrives replaced) — verify `_ReplaceTicketAttributes`/`_ReplaceAdditionalAttributes` equivalents in `tiqora.process.smart_tags`; split `ConfigItemID`/`ConfigItemNumber` on `\s*,\s*`, numbers → ids, dedupe; per CI: current version → XMLData (D4) → `ConfigItemAttributesGet` port → new XMLData → attributes → `add_ci_version` (`change_note = "Prozess <ProcessEntityID>"` when available). `DeplStateName`/`InciStateName`/`Name` set the header fields. Unknown CI → log + skip (Znuny logs, returns 1).

### D11. MCP tools (Task 25)

`cmdb_search(query: str = "", class_key: str | None = None, deployment_state: str | None = None, incident_state: str | None = None, limit: int = 20)`, `cmdb_get(ci_id: int | None = None, number: str | None = None)`, `cmdb_impact(ci_id: int, max_depth: int = 4)`; all `annotations={"readOnlyHint": True}`; class-group `ro` filter; Mode A: search/get through the adapter, impact answers `{"error": "Die Auswirkungsanalyse gibt es erst, wenn die CMDB in Tiqora geführt wird."}`; `off` → same style error. Results are compact dicts (no attachments' content).

### D12. Znuny add-on `TiqoraITSM` (Tasks 26–27)

- Same packaging as TiqoraSync (`.sopm` source, Frameworks 6.0.x–7.3.x, `Vendor` Cygnus Networks GmbH, GPL-3.0-only, `install/README.md`).
- `Kernel::System::TiqoraITSM`: `Active()` (true when `tiqora_settings['itsm.mode']` = `tiqora`, or when the row is absent/empty and the `configitem` table does not exist — the binding auto rule), `ReadOnly()` (`itsm.znuny_readonly` = `1` or SysConfig `TiqoraITSM::ReadOnly::Force`), `TicketCIs(TicketID, UserID)`, `CustomerCIs(CustomerUserID, CustomerID)`, `CIURL(CIID)`; reads only the two views + `tiqora_settings` (key column quoting like `TiqoraSync::_KeyColumn`); returns empty lists when the views do not exist (logs at `debug`).
- Agent widget `Ticket::Frontend::AgentTicketZoom###Widgets###0350-TiqoraITSM` (`Location` Sidebar, `Async` 1) → `Kernel::Output::HTML::TicketZoom::TiqoraITSM` → template `AgentTicketZoom/TiqoraITSM.tt` titled "Betroffene CIs (Tiqora)": per CI name (link `TiqoraITSM::BaseURL` + `/agent/cmdb/ci/<id>` — path per cmdb's route; verify), class, Verwendungsstatus, Vorfallstatus (coloured dot by functionality); rows of classes whose `class_group_id` the agent lacks `ro` on are hidden; widget hidden when inactive or empty (except an "CI verknüpfen" button when linking is enabled).
- Read-only policy `PreApplicationModule###TiqoraITSMReadOnly` → `Kernel::Modules::TiqoraITSMReadOnly::PreRun`: when `ReadOnly()` and `$Self->{Action}` is in `TiqoraITSM::ReadOnly::Actions` (default list = the CI/change **write** actions of ITSMConfigurationManagement/ITSMChangeManagement, verified from their `Frontend::Module###…` XML — expected `AgentITSMConfigItemAdd`, `AgentITSMConfigItemEdit`, `AgentITSMConfigItemDelete`, `AgentITSMConfigItemBulk`, `AgentITSMConfigItemAttachment` (upload part), `AdminITSMConfigItem`, `AgentITSMChangeAdd`, `AgentITSMChangeEdit`, `AgentITSMWorkOrderAdd`, `AgentITSMWorkOrderEdit`, …) → returns an error page "Die CMDB wird jetzt in Tiqora gepflegt" with a link to Tiqora; read-only screens (zoom, overview, search) keep working.
- Customer page `CustomerFrontend::Module###CustomerTiqoraITSM` + navigation (both `Valid="0"`, open question 8): list of CIs linked to the customer user or company.
- Linking (opt-in): `Frontend::Module###AgentTiqoraITSMLink` (AJAX, agent needs `rw` on the ticket) → server-side `Kernel::System::WebUserAgent` `POST <BaseURL>/api/v1/cmdb/links` with `Authorization: Bearer <TiqoraITSM::APIKey>`; the key never reaches the browser.
- `Kernel/Language/de_TiqoraITSM.pm` for all strings (English source strings, German translations with "Sie").

---

## Deviations from spec

1. **Legacy change archive** (`change_*` → `tiqora_itsm_legacy_change`) is not imported in this wave; the report counts changes and work orders (phases table puts the archive into phase 3; open question 1). The pipeline has a reserved step name `changes`.
2. **CLI** is `tiqora itsm import [--dry-run]` + `tiqora itsm status` (spec: `tiqora itsm import --dry-run`); no CLI switch (open question 13).
3. **Read-only policy** is a PreApplication module driven by a Tiqora-written flag, not ACL entries: Znuny ACLs restrict ticket actions only and cannot lock CI screens. It covers the web GUI only (open question 9).
4. **Views** carry more columns than the spec lists (`class_group_id` for permission filtering, functionality columns for state colours). Spec named the two views; names kept.
5. **Add-on detection:** Tiqora cannot see whether `TiqoraITSM` is installed (the add-on never writes Tiqora data, per spec); the switch dialog asks the admin instead (open question 6).
6. **GI in Mode A** answers reads through the adapter and refuses writes (open question 4); the spec only describes `ConfigItem*` "mapped to the Tiqora model".

## Shared files (coordination with other wave-4 plans)

- `backend/src/tiqora/extensions/core.py` (job handlers), `backend/src/tiqora/jobs/registry.py` (`LANE_SLOTS`), `backend/src/tiqora/api/compat/router.py` + `webservices.py`, `backend/src/tiqora/mcp_server/server.py` (one import line), `frontend/src/router.tsx`, `frontend/src/lib/adminSearch.ts`, i18n `en.json`/`de.json`, `docs/compatibility.md`, `docs/itsm.md`: add entries, never reorder or reformat others' lines; resolve merge conflicts by keeping both sides.
- Migration chain: this plan's migration must follow cmdb's; re-chain `down_revision` at merge if another wave-4 migration landed in between.

---

## Tasks

### Task 1: Contract check, ports and facades (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/__init__.py`, `backend/src/tiqora/itsm/importer/ports.py`, `backend/src/tiqora/itsm/importer/facades.py`
- Create (only if C8 functions are missing): `backend/src/tiqora/cmdb/import_api.py`
- Test: `backend/tests/test_itsm_import_ports.py`

**Interfaces:**
- Consumes: itsm1 (C1, C3), cmdb (C5–C9) — read their merged code first: `ls backend/src/tiqora/itsm backend/src/tiqora/cmdb`, `grep -rn "def " backend/src/tiqora/itsm/znuny_adapter backend/src/tiqora/cmdb | less`, `docs/itsm-import.md`.
- Produces: everything in D3 (`ZnunyCatalogItem`, `ZnunyClassDefinition`, `ZnunyCi`, `ZnunyCiVersion`, `ZnunyLink`, `ZnunyAttachment`, `CiView`, `CiQuery`, `CmdbClassView`, `CatalogView`, `ImpactView`, `ImportSource`, `CmdbWritePort`, `CmdbReadPort`, `ItsmModePort`, `Ports`, `get_ports(session) -> Ports`).

- [ ] **Step 1: Write the contract table.** At the top of `facades.py` write a module docstring with one line per contract C1–C10: the real module/function the facade calls, or "added by this plan in `cmdb/import_api.py`" / "not available (fallback: …)". This docstring is the reviewer's checklist.

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_itsm_import_ports.py
"""Ports and facades: protocol conformance and both-dialect smoke tests (band 56000-56019)."""
from __future__ import annotations

import pytest

from tiqora.itsm.importer import facades, ports


def test_facades_implement_the_ports() -> None:
    # runtime_checkable protocols: attribute presence is enough to catch renames
    for cls, proto in (
        (facades.ZnunyAdapterSource, ports.ImportSource),
        (facades.CmdbServiceWriter, ports.CmdbWritePort),
        (facades.CmdbServiceReader, ports.CmdbReadPort),
        (facades.ItsmModeFacade, ports.ItsmModePort),
    ):
        missing = [n for n in proto.__protocol_attrs__ if not hasattr(cls, n)]  # type: ignore[attr-defined]
        assert missing == [], f"{cls.__name__} lacks {missing}"


def test_link_map_key_is_stable() -> None:
    link = ports.ZnunyLink("ITSMConfigItem", "7", "Ticket", "42", "RelevantTo", "Valid",
                           created_at=ports.EPOCH, created_by=1)
    assert link.map_key == "ITSMConfigItem:7>Ticket:42:RelevantTo"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_mode_facade_without_znuny_itsm_is_tiqora(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    async with _session(sync_url) as session:   # helper as in test_admin_api.py:160
        p = facades.get_ports(session)
        assert await p.mode.znuny_itsm_present() is False   # core schema only
        assert await p.mode.effective_mode() == "tiqora"
        assert await p.source.profile() is None
```

(`ports.EPOCH = datetime(1970, 1, 1)` is a module constant used by tests; `_session` is copied from `test_admin_api.py:160` — `_to_async_url` + `async_sessionmaker`.)

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_itsm_import_ports.py`
Expected: FAIL — `ModuleNotFoundError: tiqora.itsm.importer`.

- [ ] **Step 4: Implement** `ports.py` exactly as D3 (all protocols `@runtime_checkable`). `facades.py`: one class per port, each holding the `AsyncSession`, every method a one-line delegation (or a short mapping of the owner's return type to the port dataclass). Where C8 functions are missing, add them in `backend/src/tiqora/cmdb/import_api.py` as thin wrappers over cmdb's ORM models **and cmdb's own** version/attribute-index/propagation helpers (never re-implement the index or propagation). `get_ports(session)` builds all four.

- [ ] **Step 5: Run tests + gates**

Run: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_itsm_import_ports.py && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/tiqora/itsm/importer backend/src/tiqora/cmdb/import_api.py backend/tests/test_itsm_import_ports.py
git commit -m "feat(itsm-import): ports and facades onto the ITSM adapter and the CMDB services"
```
(Leave `cmdb/import_api.py` out of `git add` when it was not needed.)

---

### Task 2: Migration — import tables and the add-on views (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<next>_itsm_import.py`, `backend/src/tiqora/itsm/addon_views.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (append `TiqoraItsmImportRun`, `TiqoraItsmImportMap`)
- Test: `backend/tests/test_itsm_import_tables.py`

**Interfaces:**
- Consumes: cmdb tables (C5).
- Produces: ORM `TiqoraItsmImportRun`, `TiqoraItsmImportMap` (D1); `addon_views.VIEW_NAMES = ("tiqora_v1_itsm_ci", "tiqora_v1_itsm_ci_links")`; `addon_views.view_sql(name: str, dialect: str) -> str` (the `SELECT …` body); `addon_views.create_views_sync(conn: sqlalchemy.Connection) -> None`; `addon_views.drop_views_sync(conn) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_itsm_import_tables.py
"""Import tables + add-on views on both dialects (band 56020-56049).

Seeds one class (group 56020 = a Znuny group created here), two CIs and links CI->ticket,
ticket->CI, CI->customer_user through the cmdb ORM models, then reads the views the way
the Perl add-on does."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from tiqora.itsm import addon_views

MIGRATION = next(Path(__file__).parents[1].glob("alembic/versions_tiqora/*_itsm_import.py"))


def test_migration_uses_the_module_sql() -> None:
    spec = importlib.util.spec_from_file_location("mig", MIGRATION)
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)  # type: ignore[union-attr]
    for dialect in ("mysql", "postgresql"):
        for name in addon_views.VIEW_NAMES:
            assert mig.view_sql(name, dialect) == addon_views.view_sql(name, dialect)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_views_return_links_in_both_directions(url_fixture, request) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)          # creates tables via TiqoraBase.metadata.create_all + views
    try:
        with _engine(sync_url).connect() as c:
            rows = c.execute(text(
                "SELECT ci_id, direction, link_type FROM tiqora_v1_itsm_ci_links"
                " WHERE object_kind = 'ticket' AND object_key = :k ORDER BY ci_id"),
                {"k": str(ids["ticket"])}).all()
            assert [(r.ci_id, r.direction) for r in rows] == [(ids["ci_a"], "source"), (ids["ci_b"], "target")]
            ci = c.execute(text("SELECT * FROM tiqora_v1_itsm_ci WHERE ci_id = :i"), {"i": ids["ci_a"]}).mappings().one()
            assert ci["class_group_id"] == 56020
            assert ci["incident_functionality"] == "operational"
            cust = c.execute(text("SELECT ci_id FROM tiqora_v1_itsm_ci_links WHERE object_kind='customer_user'"
                                  " AND object_key = 'itim56.kunde@example.org'")).scalars().all()
            assert cust == [ids["ci_a"]]
    finally:
        _cleanup(sync_url, ids)    # drops views it created only if they were absent before


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_import_map_unique_per_source(url_fixture, request) -> None:
    ...  # insert (ci, "56030") twice -> IntegrityError; different kinds same id -> ok
```

Write `_seed`/`_cleanup`/`_engine` in the test module; seeding uses cmdb's ORM models (names from Task 1's docstring).

- [ ] **Step 2: Run to verify failure** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_itsm_import_tables.py` → FAIL (`StopIteration`: no migration file).

- [ ] **Step 3: Implement.** Models per D1 (`MEDIUM_TEXT` variant for `summary` like `20261010_0076_gi_debugger.py`). `addon_views.py` per D2: `view_sql` returns the body with `{INT}` and the quoted `key` column rendered (`` `key` `` / `"key"`); `create_views_sync` runs `CREATE VIEW <name> AS <body>`; `drop_views_sync` `DROP VIEW IF EXISTS`. Migration: `revision` = next free number, `down_revision` = current head (after cmdb), `create_table` × 2 + indexes, then for both views `op.execute(f"CREATE VIEW {name} AS {view_sql(name, op.get_bind().dialect.name)}")`; it defines its **own** `view_sql` (copy of the module function — migrations do not import app code); `downgrade` drops views then tables.

- [ ] **Step 4: Run** the test file + `cd backend && uv run alembic heads` (exactly one primary head) + the gates. Expected: PASS. Update any test pinning the head id (`grep -rn "TIQORA_HEAD\|20261010_00" backend/tests | head`).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions_tiqora backend/src/tiqora/itsm/addon_views.py backend/src/tiqora/db/tiqora/models.py backend/tests/test_itsm_import_tables.py
git commit -m "feat(itsm-import): import run/map tables and the versioned views for the Znuny add-on"
```

---

### Task 3: Znuny definitions — safe Perl-literal parser and YAML (backend, pure)

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_format/__init__.py`, `backend/src/tiqora/itsm/znuny_format/perl_literal.py`, `backend/src/tiqora/itsm/znuny_format/definition.py`
- Create: `backend/tests/fixtures/itsm/definition_computer_6x.pl`, `backend/tests/fixtures/itsm/definition_computer_7x.yml`, `backend/tests/fixtures/itsm/definition_computer.json`
- Test: `backend/tests/test_itsm_znuny_definition.py`

**Interfaces:**
- Produces: `PerlLiteralError(ValueError)` with `.position: int`; `parse_perl_literal(text: str, *, max_depth: int = 64, max_nodes: int = 200_000) -> Any`; `DefinitionError(ValueError)`; `parse_znuny_definition(raw: str) -> list[dict[str, Any]]` (C4).

**Fixtures:** the "Computer" class definition of ITSMConfigurationManagement — 6.5 tag file `var/packagesetup/ITSMConfigurationManagement.pm` (or `Kernel/System/ITSMConfigItem/Definition…`, verify location; it is the default definition the package installs) saved verbatim as `definition_computer_6x.pl`; the 7.3 tag's YAML definition of the same class as `.yml`; the expected normalised list as JSON (write it by hand from the 6.x file, it is the ground truth; the 7.x file must normalise to the same list modulo keys 7.x added — list those in the test). Skip condition: if itsm1 already ships an equivalent parser, run these tests against it, keep the better one, re-export it here, and delete the other (one implementation).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_itsm_znuny_definition.py
import json
from pathlib import Path

import pytest

from tiqora.itsm.znuny_format.definition import DefinitionError, parse_znuny_definition
from tiqora.itsm.znuny_format.perl_literal import PerlLiteralError, parse_perl_literal

FIX = Path(__file__).parent / "fixtures" / "itsm"


@pytest.mark.parametrize(("src", "expected"), [
    ("[ { Key => 'Vendor', Name => 'Vendor', Searchable => 1, Input => { Type => 'Text', Size => 50, MaxLength => 50, }, }, ]",
     [{"Key": "Vendor", "Name": "Vendor", "Searchable": 1, "Input": {"Type": "Text", "Size": 50, "MaxLength": 50}}]),
    ("{ a => undef, 'b c' => \"x\\ty\", d => -1.5 } # trailing comment", {"a": None, "b c": "x\ty", "d": -1.5}),
    ("[ 'it\\'s', \"q\\\"\" ]", ["it's", 'q"']),
])
def test_perl_literal_subset(src, expected) -> None:
    assert parse_perl_literal(src) == expected


@pytest.mark.parametrize("src", [
    "system('id')", "[ \"$ENV{HOME}\" ]", "[ @list ]", "{ a => sub { 1 } }", "[ 1, 2", "qw(a b)",
])
def test_perl_literal_rejects_code(src) -> None:
    with pytest.raises(PerlLiteralError):
        parse_perl_literal(src)


def test_perl_literal_depth_limit() -> None:
    with pytest.raises(PerlLiteralError, match="depth"):
        parse_perl_literal("[" * 100 + "]" * 100)


def test_6x_and_7x_normalise_to_the_same_list() -> None:
    expected = json.loads((FIX / "definition_computer.json").read_text())
    assert parse_znuny_definition((FIX / "definition_computer_6x.pl").read_text()) == expected
    assert _strip_7x_only(parse_znuny_definition((FIX / "definition_computer_7x.yml").read_text())) == expected


def test_definition_must_be_list_of_keyed_hashes() -> None:
    with pytest.raises(DefinitionError):
        parse_znuny_definition("{ Key => 'x' }")
```

(`_strip_7x_only` removes the keys the test lists as 7.x additions; keep it in the test module.)

- [ ] **Step 2: Run** `cd backend && uv run python -m pytest -q tests/test_itsm_znuny_definition.py` → FAIL (module missing).

- [ ] **Step 3: Implement** a hand-written recursive-descent parser (tokenizer: whitespace/comments, `[ ] { } , =>`, single/double-quoted strings, numbers `-?\d+(\.\d+)?`, barewords `[A-Za-z_][A-Za-z0-9_:]*`; only `undef` is a bareword value; a bareword before `=>` is a key; `$`/`@` inside double quotes → error). Count nodes and depth. `parse_znuny_definition`: YAML when the text starts with `---` or `-` (after whitespace), else Perl; normalise the 7.x wrapper (as found in the 7.3 source) to the 6.x list form; validate every element is a dict with a string `Key`, recursively for `Sub`.

- [ ] **Step 4: Run** tests + gates → PASS.

- [ ] **Step 5: Commit** — `git add backend/src/tiqora/itsm/znuny_format backend/tests/fixtures/itsm backend/tests/test_itsm_znuny_definition.py && git commit -m "feat(itsm-import): safe parser for Znuny 6.x/7.x CI class definitions"`

---

### Task 4: `XMLData` ⇄ Tiqora attributes and the stringified form (backend, pure)

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_format/xmldata.py`, `backend/src/tiqora/itsm/znuny_format/stringified.py`
- Modify: `backend/src/tiqora/itsm/znuny_format/definition.py` (add `to_tiqora_schema`, `to_znuny_definition`)
- Test: `backend/tests/test_itsm_znuny_xmldata.py`

**Interfaces:**
- Consumes: C6 converter through `facades` (`definition.to_tiqora_schema(defn) -> tuple[dict, list[str]]` calls cmdb's `znuny_definition_to_schema`); C7 attribute shape.
- Produces:
  - `class RefResolver(Protocol): def ci(self, znuny_ci_id: str) -> int | None; def catalog(self, znuny_gc_id: str) -> int | None`
  - `xmldata_to_attributes(xml_data: Mapping[str, Any], schema: Mapping[str, Any], resolver: RefResolver) -> tuple[dict[str, Any], list[AttrIssue]]`
  - `attributes_to_xmldata(attributes: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]`
  - `AttrIssue(code: Literal["unresolved_reference","unknown_attribute","bad_value"], path: str, value: str)`
  - `definition.to_znuny_definition(schema) -> list[dict]` (inverse direction: `Key`, `Name`, `CountMin/Max/Default`, `Input.Type` (reverse type map), `Sub`) — needed by Tasks 19/21.
  - `stringified.xmldata_to_stringified(xml_data) -> dict[str, str]`
  - `stringified.stringified_to_xmldata(stringified: Mapping[str, Any], definition: list[dict], original: Mapping[str, str], *, customer_lookup: Callable[[str], list[str]] | None = None, prefix: str = "") -> dict[str, Any]`
  - `stringified.expand_list_keys(params: Mapping[str, Any], separator: str = ",") -> dict[str, Any]` (the `Key::` → `Key::1..n` split and bare `Key` → `Key::` rule of `ConfigItemAttributesGet`)

- [ ] **Step 1: Write the failing tests** — vectors copied from `znuny-6.5.22/scripts/test/ProcessManagement/TransitionAction/ConfigItemUpdate.t` (lines ~120–300 and ~460–500) and the POD examples of `ConfigItemUpdate.pm`:

```python
# backend/tests/test_itsm_znuny_xmldata.py
from tiqora.itsm.znuny_format.stringified import (
    expand_list_keys, stringified_to_xmldata, xmldata_to_stringified)
from tiqora.itsm.znuny_format.xmldata import attributes_to_xmldata, xmldata_to_attributes

NIC_DEF = [{"Key": "NIC", "Name": "Network Adapter", "CountMin": 0, "CountMax": 10, "CountDefault": 1,
            "Input": {"Type": "Text", "Required": 1},
            "Sub": [{"Key": "IPoverDHCP", "CountMin": 1, "CountMax": 1, "CountDefault": 1,
                     "Input": {"Type": "GeneralCatalog", "Class": "ITSM::ConfigItem::YesNo"}},
                    {"Key": "IPAddress", "CountMin": 0, "CountMax": 20, "CountDefault": 0,
                     "Input": {"Type": "Text"}}]},
           {"Key": "CPU", "CountMin": 0, "CountMax": 16, "Input": {"Type": "Text"}},
           {"Key": "Vendor", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Text"}}]
NIC_XML = {"NIC": [{"Content": "NicName1", "IPoverDHCP": [{"Content": "37"}], "IPAddress": [{"Content": "123"}]},
                   {"Content": "NicName2", "IPoverDHCP": [{"Content": "38"}], "IPAddress": [{"Content": "456"}]}]}


def test_xmldata_to_stringified_pod_example() -> None:
    assert xmldata_to_stringified(NIC_XML) == {
        "NIC::1": "NicName1", "NIC::1::IPAddress::1": "123", "NIC::1::IPoverDHCP::1": "37",
        "NIC::2": "NicName2", "NIC::2::IPAddress::1": "456", "NIC::2::IPoverDHCP::1": "38"}


def test_expand_list_keys_splits_and_trims() -> None:
    assert expand_list_keys({"CPU::": "NEW-2-CPU, NEW-2-CPU2", "Vendor": "V", "Ram::1": "R"}) == {
        "CPU::1": "NEW-2-CPU", "CPU::2": "NEW-2-CPU2", "Vendor::1": "V", "Ram::1": "R"}


def test_stringified_empty_string_deletes_and_original_fills() -> None:
    original = xmldata_to_stringified({"Vendor": [{"Content": "Old"}], "CPU": [{"Content": "c1"}]})
    out = stringified_to_xmldata({"Vendor::1": ""}, NIC_DEF, original)
    assert out["Vendor"] == [{"Content": ""}]          # '' wins over the original (ConfigItemUpdate.pm ~680)
    assert out["CPU"] == [{"Content": "c1"}]           # untouched keys come from the original


def test_stringified_customer_lookup_unique_hit_only() -> None:
    defn = [{"Key": "Owner", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Customer"}}]
    hit = stringified_to_xmldata({"Owner::1": "kunde"}, defn, {}, customer_lookup=lambda s: ["itim56.kunde"])
    assert hit["Owner"] == [{"Content": "itim56.kunde"}]
    many = stringified_to_xmldata({"Owner::1": "k"}, defn, {"Owner::1": "alt"}, customer_lookup=lambda s: ["a", "b"])
    assert many["Owner"] == [{"Content": "alt"}]


def test_round_trip_through_tiqora_attributes(schema_for_nic) -> None:
    attrs, issues = xmldata_to_attributes(NIC_XML, schema_for_nic, _Resolver({"37": 9037, "38": 9038}))
    assert issues == []
    assert attrs["NIC"][0] == {"value": "NicName1", "sub": {"IPoverDHCP": [{"value": 9037}], "IPAddress": [{"value": "123"}]}}
    assert attributes_to_xmldata(attrs, schema_for_nic) == _xml_with_catalog_ids(NIC_XML, {"37": 9037, "38": 9038})


def test_unresolved_ci_reference_kept_as_text_with_issue(schema_with_ci_ref) -> None:
    attrs, issues = xmldata_to_attributes({"DependsOn": [{"Content": "999"}]}, schema_with_ci_ref, _Resolver({}))
    assert attrs["DependsOn"] == [{"value": "999"}]
    assert [i.code for i in issues] == ["unresolved_reference"]
```

(`schema_for_nic` / `schema_with_ci_ref` fixtures: build via `definition.to_tiqora_schema(NIC_DEF)` so the test follows cmdb's real schema shape; `_Resolver`, `_xml_with_catalog_ids` small helpers in the test.)

- [ ] **Step 2: Run** → FAIL (imports).
- [ ] **Step 3: Implement** — ports of the three Perl functions line by line (keep Perl truthiness: `!$X` is true for `""`, `"0"`, `None`; the `$Counter gt $ItemCountMin` string comparison is a numeric intent — implement numerically and note it), the schema walk for `xmldata_to_attributes`/`attributes_to_xmldata` (only file depending on C7), and the reverse type map. Size guard: refuse `xml_data` deeper than 16 levels or with more than 50 000 leaf values (`ValueError`).
- [ ] **Step 4: Run** tests + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): convert Znuny XMLData and stringified CI data to Tiqora attributes"` (add the three source files + test).

---

### Task 5: GI `CIXMLData` format (backend, pure)

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_format/gi_cixml.py`, `backend/tests/fixtures/itsm/gi_vectors.json`
- Test: `backend/tests/test_itsm_gi_cixml.py`

**Interfaces:**
- Produces: `CixmlError(code: str, message: str)` (exception; `code` = Znuny suffix such as `InvalidParameter`); `cixml_to_xmldata(cixml: Mapping[str, Any], definition: list[dict], *, lookups: GiLookups) -> dict[str, Any]` (raises `CixmlError`); `xmldata_to_cixml(xml_data: Mapping[str, Any], definition: list[dict], *, lookups: GiLookups) -> dict[str, Any]`; `class GiLookups(Protocol)`: `catalog_id(catalog_class: str, name: str) -> int | None`, `catalog_name(catalog_id: int) -> str | None`, `customer_user_exists(login: str) -> bool`, `customer_company_exists(customer_id: str) -> bool` (sync callables over preloaded dicts; the operation preloads).

- [ ] **Step 1: Collect vectors.** From ITSMConfigurationManagement 6.5 tag: `Kernel/GenericInterface/Operation/ConfigItem/Common.pm` (functions that check and convert `CIXMLData`: required keys, `CountMax`, Date/DateTime format checks, GeneralCatalog name → id, Customer/CustomerCompany existence) and `scripts/test/GenericInterface/Operation/ConfigItem/*.t` (request/response pairs). Write 10–20 vectors into `gi_vectors.json` as `{"name", "definition", "cixml", "xml_data" | "error": {"code", "message"}}`, copying the error message texts verbatim. Repeat for the 7.3 tag; mark vectors that differ with `"profile": "7"`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_itsm_gi_cixml.py
import json
from pathlib import Path

import pytest

from tiqora.itsm.znuny_format.gi_cixml import CixmlError, cixml_to_xmldata, xmldata_to_cixml

VECTORS = json.loads((Path(__file__).parent / "fixtures/itsm/gi_vectors.json").read_text())


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(v) -> None:
    lookups = _Lookups(v.get("catalog", {}), v.get("customers", []), v.get("companies", []))
    if "error" in v:
        with pytest.raises(CixmlError) as exc:
            cixml_to_xmldata(v["cixml"], v["definition"], lookups=lookups)
        assert (exc.value.code, exc.value.message) == (v["error"]["code"], v["error"]["message"])
    else:
        assert cixml_to_xmldata(v["cixml"], v["definition"], lookups=lookups) == v["xml_data"]
        assert xmldata_to_cixml(v["xml_data"], v["definition"], lookups=lookups) == v["cixml"]
```

- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** (array-vs-hash handling: a single entry may be a hash, several an array — exactly as the vectors show). **Step 5: Run** tests + gates → PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat(gi): CIXMLData conversion for the ConfigItem operations"`

---

### Task 6: Import framework — map store, report, dry-run writer, step context (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/mapstore.py`, `backend/src/tiqora/itsm/importer/report.py`, `backend/src/tiqora/itsm/importer/writer.py`, `backend/src/tiqora/itsm/importer/steps/__init__.py`, `backend/src/tiqora/itsm/importer/issue_codes.py` (`ISSUE_CODES: dict[str, str]`, filled with every code listed in Task 16; Task 16 adds the consistency test and the docs)
- Test: `backend/tests/test_itsm_import_framework.py`

**Interfaces:**
- Consumes: Task 1 ports, Task 2 `TiqoraItsmImportMap`.
- Produces:
  - `fingerprint(record: Any) -> str` (sha256 of `json.dumps(dataclasses.asdict(record) | dict, sort_keys=True, default=str, separators=(",", ":"))`; `bytes` fields hashed as sha256 hex first).
  - `class MapStore`: `__init__(session, *, run_id: int, read_only: bool)`; `async get(kind: str, source_id: str) -> MapEntry | None`; `async get_many(kind: str, source_ids: Sequence[str]) -> dict[str, MapEntry]` (IN-list chunks of 500); `async put(kind, source_id, target_id: int, fp: str) -> None` (upsert by select-then-update/insert, no dialect-specific upsert; in `read_only` mode writes go to an in-memory overlay that `get`/`get_many` also read); `async all_source_ids(kind: str) -> list[str]`. `MapEntry(target_id: int, fingerprint: str)`.
  - `ImportIssue(step: str, code: str, object_ref: str, message: str)`, `StepCounts(created=0, updated=0, unchanged=0, skipped=0, problems=0)`, `class ImportReport`: `count(step, field, n=1)`, `issue(step, code, object_ref, message)`, `summary(max_issues=200) -> dict`, `to_csv() -> bytes` (BOM, `;`, header `Schritt;Code;Objekt;Meldung`).
  - `class DryRunWriter(CmdbWritePort)`: every create returns a decreasing fake id (`-1, -2, …`), `add_ci_version` returns a fake id unless `attributes` equal the last ones given for that CI in this run (then `None`), `latest_version_id` answers from the fake state first, then the real reader (passed in), records nothing in the DB.
  - `@dataclass class StepContext: session_factory, ports_for: Callable[[AsyncSession], Ports], run_id: int, dry_run: bool, page_size: int, report: ImportReport, progress: Callable[[int, int | None, str], Awaitable[None]], check_cancelled: Callable[[], Awaitable[None]], user_id: int, writer_for: Callable[[AsyncSession], CmdbWritePort], mapstore_for: Callable[[AsyncSession], MapStore]`, plus `touched_ci_ids: set[int]`.

- [ ] **Step 1: Write the failing tests** — pure tests for `fingerprint` (stable across dict order, differs on any field), `ImportReport.summary` (cuts issues at 200, keeps counts), `to_csv` (BOM + header + escaping of `;` and newlines), `DryRunWriter` (fake ids negative and unique, unchanged version → `None`); DB tests (both dialects, band 56050–56069) for `MapStore`: put/get/get_many over 1 200 ids (chunking), update of fingerprint keeps one row, `read_only=True` leaves the table untouched but `get` sees the overlay.

```python
@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_mapstore_read_only_overlay(url_fixture, request) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    async with _session(sync_url) as s:
        store = MapStore(s, run_id=56050, read_only=True)
        await store.put("ci", "56051", -1, "fp")
        assert (await store.get("ci", "56051")).target_id == -1
        await s.commit()
    async with _session(sync_url) as s:
        assert await MapStore(s, run_id=56050, read_only=False).get("ci", "56051") is None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): map store, report and dry-run writer"`

---

### Task 7: Test fixtures + step `catalog` (backend)

**Files:**
- Create: `backend/tests/_itsm_import_fixtures.py`, `backend/src/tiqora/itsm/importer/steps/catalog.py`
- Create (only if C12 helper is missing): `backend/tests/_itsm_znuny_schema.py`
- Test: `backend/tests/test_itsm_import_catalog.py`

**Interfaces:**
- Consumes: Task 6 (`StepContext`, `MapStore`, `ImportReport`), C3/C8 through ports.
- Produces:
  - `_itsm_import_fixtures.FakeSource(ImportSource)` — in-memory source built from Python lists (`catalog`, `definitions`, `cis`, `versions`, `links`, `attachments`, `changes`), with `remove_ci(id)` / `add_version(ci_id, name, xml)` / `rename_catalog_item(id, name)` mutators for re-run tests, and test knobs: `fail_once_after_pages: int | None` (raise `RuntimeError("worker died")` once when `ci_ids_page` is called for page n+1), `on_first_page: Callable[[], None] | None` (called on the first `ci_ids_page` call), `changed_since_count: int` + `changed_since_args: list[datetime]` (for `count_cis_changed_since`), `max_ids_requested_per_call()`.
  - `_itsm_import_fixtures.sample_source(**knobs) -> FakeSource` — 1 catalog class set (deployment states `Planned` (preproductive), `Production` (productive), `Retired` (postproductive); incident states `Operational`, `Warning`, `Incident`; `ITSM::ConfigItem::YesNo` Yes/No), 2 classes (`Computer` with the NIC definition of Task 4 in two versions, `Network` with one), 10 CIs (`itim56-ci-01` … `-10`, numbers `2026101256001`…), 1–3 versions each, links CI↔CI (`DependsOn`), CI→Ticket (`RelevantTo`), one link to `FAQ` (unsupported), attachments on CI 1 (DB) and CI 2 (FS: `data=None`).
  - `_itsm_import_fixtures.make_ctx(session_factory, source, *, dry_run=False, page_size=3, user_id=56070) -> StepContext` and `cleanup_cmdb_rows(sync_url)` (deletes what the importer created: map rows by `run_id` in the band + CMDB rows the map points to, via cmdb's models).
  - `steps.catalog.run_catalog(ctx: StepContext) -> dict[str, int]` (returns `{znuny_gc_id: tiqora_catalog_id}` for the run's resolver).

- [ ] **Step 1: Write the failing tests** (both dialects, band 56070–56099):

```python
async def test_catalog_import_creates_then_is_unchanged(url_fixture, request) -> None:
    factory = _factory(request.getfixturevalue(url_fixture))
    src = sample_source()
    try:
        ctx = make_ctx(factory, src)
        ids = await run_catalog(ctx)
        assert ctx.report.steps["catalog"].created == len(src.catalog)
        assert {it.functionality for it in await _tiqora_catalog(factory)} >= {"productive", "operational", "incident"}
        ctx2 = make_ctx(factory, src)
        assert await run_catalog(ctx2) == ids
        assert ctx2.report.steps["catalog"].unchanged == len(src.catalog)
        src.rename_catalog_item(src.catalog[0].id, "Produktiv")        # fingerprint changes
        ctx3 = make_ctx(factory, src)
        await run_catalog(ctx3)
        assert ctx3.report.steps["catalog"].updated == 1
    finally:
        cleanup_cmdb_rows(...)


async def test_catalog_dry_run_writes_nothing(url_fixture, request) -> None:
    ...  # dry_run=True: counts == created, CMDB catalog table unchanged, map table unchanged
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `run_catalog`: load all items (catalogs are small), `get_many` map, per item compare fingerprint → `upsert_catalog_item` (key = Znuny name, `catalog_class` = Znuny class) → `put`; counts; one commit at the end (skip commit when `dry_run`). If the C12 helper is missing, add `_itsm_znuny_schema.py` now from the DDL in `docs/itsm-import.md` (6.x profile at least) — needed by Task 12's adapter integration test.
- [ ] **Step 4: Run** + gates → PASS. **Step 5: Commit** — `git commit -m "feat(itsm-import): general catalog step and importer test fixtures"`

---

### Task 8: Step `classes` — classes and every definition version (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/steps/classes.py`
- Test: `backend/tests/test_itsm_import_classes.py`

**Interfaces:**
- Consumes: Task 3 `parse_znuny_definition`, Task 4 `definition.to_tiqora_schema`, Task 7 catalog ids.
- Produces: `run_classes(ctx: StepContext, catalog_ids: dict[str, int]) -> ClassMaps` with `ClassMaps(class_ids: dict[int, int], definitions: dict[int, DefinitionTarget])`, `DefinitionTarget(tiqora_class_id: int, definition_id: int, version: int, schema: dict, znuny_definition: list[dict])`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56100–56119): (a) two Znuny definition versions of `Computer` → two Tiqora definition versions in ascending order, the class's current definition = the newer; (b) a re-run adds nothing; (c) a third Znuny version appears → exactly one new Tiqora version; (d) an unparsable definition (`"[ { Key => "`) → issue `definition_unreadable`, the class is still created, its CIs are later skipped with issue `no_definition` (assert in Task 9); (e) class group: the Znuny class's permission group (general catalog preference, verify key name, e.g. `Permission`) becomes `group_id`; missing → `None` + issue `class_without_group` (the CMDB then applies its default — cmdb decides).

```python
async def test_definition_versions_imported_in_order(url_fixture, request) -> None:
    ...
    maps = await run_classes(ctx, catalog_ids)
    computer = maps.class_ids[src.class_id("Computer")]
    versions = await _definition_versions(factory, computer)
    assert [v.version for v in versions] == [1, 2]
    assert maps.definitions[src.definition_id("Computer", 2)].version == 2
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (class key = Znuny class name; schema conversion issues → report `definition_converted_with_issues` with the issue texts; created/created_by from Znuny). **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): CI classes with every definition version"`

---

### Task 9: Steps `cis_pass1` / `cis_pass2` — CIs and all versions (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/steps/cis.py`
- Test: `backend/tests/test_itsm_import_cis.py`

**Interfaces:**
- Consumes: Tasks 4, 6, 7, 8.
- Produces: `run_cis_pass1(ctx, maps: ClassMaps, catalog_ids: dict[str, int]) -> None`; `run_cis_pass2(ctx, maps: ClassMaps, catalog_ids: dict[str, int]) -> None`; `MapResolver(ImportRefResolver)` backed by `MapStore.get_many` preloaded per page (CI refs) and `catalog_ids`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56120–56199). Include the Review Focus tests verbatim by name:

```python
async def test_rerun_is_idempotent_and_appends_new_versions(url_fixture, request) -> None:
    src = sample_source(); factory = _factory(...)
    await _full_ci_import(factory, src)                      # catalog + classes + pass1 + pass2
    before = await _counts(factory)                          # cis, versions, attr rows
    await _full_ci_import(factory, src)
    assert await _counts(factory) == before
    src.add_version(ci_id=src.ci_id("itim56-ci-03"), name="itim56-ci-03", xml={"Vendor": [{"Content": "Neu"}]})
    ctx = await _full_ci_import(factory, src)
    after = await _counts(factory)
    assert after.versions == before.versions + 1
    assert ctx.report.steps["cis_pass2"].updated == 1
    latest = await _latest_attributes(factory, "2026101256003")
    assert latest["Vendor"] == [{"value": "Neu"}]


async def test_forward_ci_reference_resolved_in_second_pass(url_fixture, request) -> None:
    # CI 01 (first page) has a CI-type attribute pointing to CI 10 (last page)
    ...
    assert (await _latest_attributes(factory, "2026101256001"))["DependsOn"] == [{"value": tiqora_id_of_ci10}]


async def test_paging_commits_per_batch_and_checks_cancel(url_fixture, request) -> None:
    calls: list[str] = []
    ctx = make_ctx(factory, src, page_size=3)
    ctx.check_cancelled = _recorder(calls, "cancel?"); ctx.progress = _recorder(calls, "progress")
    commits = _count_commits(factory)                        # wraps AsyncSession.commit
    await run_cis_pass1(ctx, maps, catalog_ids)
    assert commits.value == 4                                 # 10 CIs / 3 per page
    assert calls.count("cancel?") == 4
    assert src.max_ids_requested_per_call() <= 3              # never loads more than a page


async def test_ci_changed_in_tiqora_is_not_overwritten(url_fixture, request) -> None:
    await _full_ci_import(factory, src)
    ci = await _tiqora_ci_id(factory, "2026101256004")
    own = await _add_version_via_cmdb_service(factory, ci, {"Vendor": [{"value": "in Tiqora geändert"}]})
    src.add_version(ci_id=src.ci_id("itim56-ci-04"), name="itim56-ci-04", xml={"Vendor": [{"Content": "aus Znuny"}]})
    ctx = await _full_ci_import(factory, src)
    assert await _latest_version_id(factory, ci) == own
    assert [(i.code, i.object_ref) for i in ctx.report.issues if i.code == "changed_in_tiqora"] == [("changed_in_tiqora", "2026101256004")]


async def test_preserves_number_created_and_created_by(url_fixture, request) -> None: ...
async def test_ci_without_usable_definition_is_skipped_with_issue(url_fixture, request) -> None: ...
async def test_dry_run_counts_like_real_run_and_writes_nothing(url_fixture, request) -> None:
    dry = await _full_ci_import(factory, src, dry_run=True)
    assert await _counts(factory) == ZERO
    real = await _full_ci_import(factory, src)
    for step in ("cis_pass1", "cis_pass2"):
        assert dry.report.steps[step].created == real.report.steps[step].created
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D5 steps 3–4. Pass 1: `ci_ids_page(after, page_size)` → `cis(ids)` → `get_many("ci", …)` → `create_ci` for unmapped CIs with `name = number` (provisional; pass 2's first version sets the real name through `add_ci_version`), keeping `number`, `created_at`, `created_by` → `put("ci", …)`. Pass 2: per page `versions(ids)`; per CI skip mapped versions; conflict check (`latest_version_id` not among this CI's mapped version targets → issue `changed_in_tiqora`, skip the CI); per new version: definition by `definition_id` (missing → issue `no_definition`, skip the CI), convert with `MapResolver`, `add_ci_version(...)` with Znuny's `create_time`/`create_by` → `put("version", …)` (a `None` return maps the Znuny version onto the current Tiqora version id); add the Tiqora CI id to `ctx.touched_ci_ids`. Both passes: commit per page (never in dry run), `check_cancelled()` and `progress(done, total, "CIs werden übernommen …")` per page, `total = await source.ci_count()`.
- [ ] **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): CIs with all versions, paged and idempotent"`

---

### Task 10: Step `links` (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/steps/links.py`
- Test: `backend/tests/test_itsm_import_links.py`

**Interfaces:**
- Consumes: Task 9 map (`ci`), ports `links_for_cis`, `ensure_link_type`, `add_link`; `tiqora.znuny.sysconfig.SysConfig` for the incident-propagation link types.
- Produces: `run_links(ctx) -> None`; `ZNUNY_OBJECT_KINDS = {"ITSMConfigItem": "ci", "Ticket": "ticket", "Service": "service"}`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56200–56229): CI↔CI `DependsOn` → one link with kind `ci`/`ci`, ids = Tiqora ids as strings; CI→Ticket keeps the ticket id; a link seen from both CIs' pages is added once (`add_link` returns False the second time, counted `unchanged`); `FAQ` link → `skipped` + issue `unsupported_object`; link with state other than `Valid` → skipped; link to a CI that is not mapped (deleted CI) → issue `unresolved_reference`; propagation flag: with SysConfig `ITSMConfigItem::LinkStatus::LinkTypes` (verify the real key in ITSMConfigurationManagement; it lists link types that propagate incident state with a direction) containing `DependsOn`, `ensure_link_type(key="DependsOn", propagates_incidents=True)` is called (assert through the CMDB link-type table); re-run → all `unchanged`.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — per page of mapped CI ids (iterate `MapStore.all_source_ids("ci")` in pages), collect links, dedupe by `map_key`, map both ends, `ensure_link_type` once per type per run, `add_link`, `put("link", map_key, 0, fp)`. **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): CI links from link_relation"`

---

### Task 11: Step `attachments` + reader for `virtual_fs` (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/steps/attachments.py`, `backend/src/tiqora/itsm/importer/source_extra.py`
- Modify: `backend/src/tiqora/itsm/importer/facades.py` (`ZnunyAdapterSource.attachments` delegates to `source_extra` when itsm1 has no reader)
- Test: `backend/tests/test_itsm_import_attachments.py`

**Interfaces:**
- Produces: `source_extra.read_ci_attachments(session, ci_ids: Sequence[int], *, with_data: bool, sysconfig: SysConfig) -> list[ZnunyAttachment]`; `steps.attachments.run_attachments(ctx) -> None`.

**Znuny facts to verify first** (ITSMConfigurationManagement 6.5 `Kernel/System/ITSMConfigItem.pm` `ConfigItemAttachmentAdd/List/Get`): attachments live in `virtual_fs` with `filename = 'ConfigItem/<ConfigItemID>/<FileName>'`, metadata in `virtual_fs_preferences` (`ContentType`, `FilesizeRaw`), content in `virtual_fs_db` (DB backend) — the backend name is in `virtual_fs.backend` (`Kernel::System::VirtualFS::DB` vs `…::FS`). Pin these in the test seeds.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56230–56259): reader test seeds `virtual_fs*` rows (core tables, present in the test schema) for two CIs (DB backend with bytes incl. `\x00`, FS backend) and asserts `ZnunyAttachment` records (FS → `data=None`), `with_data=False` loads no `virtual_fs_db` content (assert via a statement recorder: no `SELECT … content`), IN-list chunks; step test with `FakeSource`: DB attachment imported once (re-run `unchanged`), FS attachment → issue `attachment_in_filesystem`, CI not mapped → skipped.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (data loaded per page only for attachments whose fingerprint (`size` + name + `create_time`) changed; max 50 MB per file, larger → issue `attachment_too_large`). **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): CI attachments from Znuny's virtual file system"`

---

### Task 12: Delegated steps, change counts, deleted CIs, incident states, real-adapter integration (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/steps/delegated.py`, `backend/src/tiqora/itsm/importer/pipeline.py`
- Modify: `backend/src/tiqora/itsm/importer/source_extra.py` (`count_changes`, `count_cis_changed_since`, `max_ci_change_time` when itsm1 lacks them)
- Test: `backend/tests/test_itsm_import_pipeline.py`

**Interfaces:**
- Consumes: Tasks 7–11; itsm1's priority-matrix and service-meta import functions (C3 note / open question 10) through `facades`.
- Produces: `STEP_ORDER: tuple[str, ...] = ("catalog", "classes", "cis_pass1", "cis_pass2", "links", "attachments", "priority_matrix", "service_meta", "changes", "deleted_in_znuny", "incident_states")`; `async def run_import(session_factory, *, run_id: int, user_id: int, dry_run: bool, page_size: int, progress, check_cancelled) -> ImportReport` (raises `ImportBlocked(message)` when `profile() is None`).

- [ ] **Step 1: Write the failing tests** (both dialects, band 56260–56299):

```python
async def test_full_pipeline_with_fake_source(url_fixture, request, monkeypatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())   # test hook in facades
    report = await run_import(factory, run_id=56260, user_id=56261, dry_run=False, page_size=4,
                              progress=_noop, check_cancelled=_noop)
    assert list(report.steps) == list(STEP_ORDER)
    assert report.steps["changes"].skipped == 2                          # counted, not imported
    assert {i.code for i in report.issues} >= {"unsupported_object", "attachment_in_filesystem"}


async def test_deleted_in_znuny_reported_not_deleted(...) -> None:
    ...  # import, src.remove_ci(id), import again -> issue deleted_in_znuny, CI still in Tiqora


async def test_incident_states_recomputed_for_touched_cis(...) -> None:
    ...  # CI 05 version has incident state Incident; DependsOn link 06->05 propagates
         # -> after the run CI 06's derived incident state is Warning (cmdb semantics)


async def test_real_adapter_on_znuny_6x_tables(url_fixture, request) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    create_znuny_itsm_tables(sync_url, "itsm-6")                  # C12 helper
    try:
        seed_znuny_itsm_6x(sync_url)   # in _itsm_import_fixtures: 2 classes, 3 CIs, xml_storage rows, 1 link
        report = await run_import(...)  # no override: real ZnunyAdapterSource
        assert report.steps["cis_pass2"].created == 4               # 3 CIs, 4 versions
    finally:
        drop_znuny_itsm_tables(sync_url); cleanup_cmdb_rows(sync_url)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `run_import`: creates its own sessions per step (`async with session_factory() as s`), resolves `profile`, runs `STEP_ORDER`, passes maps between steps, calls `check_cancelled` between steps; `priority_matrix`/`service_meta` call itsm1's functions (dry run → their dry-run flag if present, else count the source rows of `cip_allocate` / ITSM service columns and report `skipped`); `changes` reads counts; `deleted_in_znuny` compares `MapStore.all_source_ids("ci")` with Znuny ids in IN-list chunks (`SELECT id FROM configitem WHERE id IN …`, through the source); `incident_states` batches `touched_ci_ids` by 500. `facades.source_override: ImportSource | None = None` and `facades.znuny_present_override: bool | None = None` (read by `ItsmModeFacade.znuny_itsm_present`) are the only test hooks.
- [ ] **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): pipeline with delegated steps, change counts and deleted-CI report"`

---

### Task 13: Job handlers, run records, CLI (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/jobs.py`, `backend/src/tiqora/cli/itsm.py`
- Modify: `backend/src/tiqora/extensions/core.py` (register `ITSM_IMPORT_JOB`, `ITSM_SWITCH_JOB` — the switch handler function is a stub raising `JobFailed("noch nicht verfügbar")` until Task 14), `backend/src/tiqora/jobs/registry.py` (`LANE_SLOTS`: add `"itsm_import": 1`), `backend/src/tiqora/main.py` (`add_itsm_subparser(sub)`)
- Test: `backend/tests/test_itsm_import_jobs.py`, `backend/tests/test_cli_itsm.py`

**Interfaces:**
- Consumes: Task 12 `run_import`, `tiqora.jobs.*`.
- Produces: `ItsmImportPayload`, `ItsmSwitchPayload`, `ITSM_IMPORT_JOB`, `ITSM_SWITCH_JOB` (D6); `IMPORT_DEDUPE_KEY = "itsm_import"`; `class ImportBusy(Exception)` (an import/switch job is active; API → 409, CLI → exit 1); `async def start_run(session, *, kind: Literal["dry_run","import","switch","rollback"], user_id: int) -> TiqoraItsmImportRun` (flush only; raises `ImportBusy` for the first three kinds while an `itsm.import`/`itsm.switch` job is active); `async def finish_run(session_factory, run_id, *, status, report: ImportReport | None, message: str | None) -> None`; CLI `tiqora itsm import [--dry-run] [--page-size N] [--json]`, `tiqora itsm status [--json]`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56300–56329):

```python
async def test_import_job_writes_run_summary_and_csv(url_fixture, request, monkeypatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    run_id, job_id = await _enqueue(factory, dry_run=False, user_id=56300)
    await run_jobs_until_idle(factory)          # existing helper from tests/_gi_mail_jobs.py or jobs tests
    run = await _run(factory, run_id)
    assert run.status == "done" and run.job_id == job_id
    summary = json.loads(run.summary)
    assert summary["steps"]["cis_pass2"]["created"] > 0
    files = await _job_files(factory, job_id)
    assert [f.name for f in files] == ["itsm-import-bericht.csv"]


async def test_requeued_job_resumes_without_duplicates(url_fixture, request, monkeypatch) -> None:
    clean = await _single_clean_run_counts(request.getfixturevalue(url_fixture))   # fresh DB state, one run
    src = sample_source(fail_once_after_pages=1)        # raises RuntimeError("worker died") on page 2, once
    monkeypatch.setattr(facades, "source_override", src)
    run_id, job_id = await _enqueue(factory, dry_run=False, user_id=56301)
    with pytest.raises(RuntimeError):
        await _call_handler(factory, job_id)            # first claim dies mid-import (page 1 committed)
    await _call_handler(factory, job_id)                # requeued claim, same payload / run_id
    assert await _counts(factory) == clean              # no duplicate CIs, versions, links, attachments
    assert (await _run(factory, run_id)).status == "done"


async def test_second_start_is_refused_while_active(url_fixture, request) -> None:
    ...  # enqueue twice with dedupe key -> same job id; start_run refuses with ImportBusy


async def test_failed_run_records_message(...) -> None: ...   # source.profile() None -> status failed,
                                                              # message "In der Datenbank wurde kein Znuny ITSM gefunden."
```

CLI test: `main(["itsm", "import", "--dry-run", "--json"])` with the fake source prints JSON with `steps`; exit code 0; `itsm status` prints mode + last run; a running job → exit 1 with a message on stderr (pattern `cli/report.py:_cmd_backfill`).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (handler: `start`→`running` row exists from the API/CLI; `run_import` with `ctx.progress`/`ctx.check_cancelled`; on success `ctx.add_file("itsm-import-bericht.csv", "text/csv; charset=utf-8", report.to_csv())`; `finish_run`; `JobCancelled` → run `cancelled`; any other exception → run `failed` + re-raise). CLI runs `run_import` inline (no job), creating and finishing its own run row, refusing when an `itsm.import`/`itsm.switch` job is active or `itsm.switch` exists.
- [ ] **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): import job, run records, report file and tiqora itsm CLI"`

---

### Task 14: Switch A→B, rollback, drift, status (backend)

**Files:**
- Create: `backend/src/tiqora/itsm/importer/switch.py`
- Modify: `backend/src/tiqora/itsm/importer/jobs.py` (real `run_itsm_switch_job`); itsm1's ITSM settings section validator (only if C2 guard is missing)
- Test: `backend/tests/test_itsm_import_switch.py`

**Interfaces:**
- Produces: `SwitchBlocked(Exception)` with `.message`; `async def import_status(session) -> ImportStatus` (dataclass mirroring `ItsmImportStatusOut`, D8); `async def check_can_import(session) -> None` (raises `SwitchBlocked`); `async def check_can_switch(session, *, addon_installed: bool, edits_stopped: bool) -> None`; `async def rollback(session, *, user_id: int) -> None` (flush only); `SWITCH_KEY = "itsm.switch"`, `READONLY_KEY = "itsm.znuny_readonly"`, `MODE_KEY = "itsm.mode"`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56330–56369):

```python
async def test_switch_sets_readonly_before_final_import(url_fixture, request, monkeypatch) -> None:
    seen: list[str | None] = []
    src = sample_source(on_first_page=lambda: seen.append(_setting(sync_url, READONLY_KEY)))
    monkeypatch.setattr(facades, "source_override", src)
    monkeypatch.setattr(facades, "znuny_present_override", True)
    await _done_import(factory)                                   # precondition
    await _run_switch(factory, user_id=56330, addon_installed=True)
    assert seen[-1] == "1"                                        # flag was set before the import read data
    assert _setting(sync_url, MODE_KEY) == "tiqora"
    assert json.loads(_setting(sync_url, SWITCH_KEY))["by"] == 56330


async def test_switch_failure_clears_readonly_and_keeps_mode(url_fixture, request, monkeypatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source(fail_once_after_pages=0))
    monkeypatch.setattr(facades, "znuny_present_override", True)
    await _done_import(factory, source=sample_source())
    with pytest.raises(RuntimeError):
        await _run_switch(factory, user_id=56331, addon_installed=True)
    assert _setting(sync_url, READONLY_KEY) == "0"
    assert _setting(sync_url, MODE_KEY) in (None, "", "znuny_readthrough")
    assert _setting(sync_url, SWITCH_KEY) is None
    assert (await _last_run(factory)).status == "failed"
async def test_switch_requires_successful_import(...) -> None:       # no done import -> SwitchBlocked
async def test_switch_without_addon_requires_edits_stopped(...) -> None: ...
async def test_import_refused_after_switch(...) -> None:             # check_can_import -> SwitchBlocked
async def test_rollback_restores_mode_a(...) -> None:                # keys removed/reset, run row kind rollback
async def test_status_reports_drift_after_switch(url_fixture, request, monkeypatch) -> None:
    src = sample_source(); src.changed_since_count = 3              # count_cis_changed_since -> 3
    monkeypatch.setattr(facades, "source_override", src)
    monkeypatch.setattr(facades, "znuny_present_override", True)
    _put_setting(sync_url, SWITCH_KEY, json.dumps({"switched_at": "2026-10-12T08:00:00", "by": 56332, "run_id": 1}))
    _put_setting(sync_url, MODE_KEY, "tiqora")
    async with factory() as session:
        status = await import_status(session)
    assert status.znuny_changes_since_switch == 3
    assert src.changed_since_args == [datetime(2026, 10, 12, 8, 0)]
    assert status.can_import is False
async def test_settings_form_cannot_choose_tiqora_while_znuny_itsm_present(...) -> None:
    # C2 guard: PUT /api/v1/admin/settings/itsm {"mode": "tiqora"} -> 422 while present and not switched
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** per D7. Mode write goes through the settings section store of itsm1 (`save_section_values` with the existing section) so validation and caches behave as for an admin save; the guard (C2) allows the save when `SWITCH_KEY` is being written in the same transaction (the switch writes `SWITCH_KEY` first, then saves the section). `import_status` uses only bounded queries (`COUNT(*)` on Znuny ITSM tables via the source, last run by `started_at DESC LIMIT 1`, active job via `find_active_job`).
- [ ] **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): explicit switch to Tiqora with read-only flag, rollback and drift count"`

---

### Task 15: Admin API `/api/v1/admin/itsm/import` (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/itsm_import.py`, `backend/src/tiqora/itsm/importer/schemas.py`
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `packages/api-client/openapi.json`, `docs/api/openapi.json`
- Test: `backend/tests/test_admin_itsm_import_api.py`

**Interfaces:**
- Consumes: Tasks 13–14.
- Produces: routes and models of D8 (`ItsmImportStatusOut`, `SwitchInfoOut`, `ImportRunOut`, `StepCountsOut`, `ImportIssueOut`, `ImportStartIn{dry_run: bool}`, `SwitchIn{confirm: str, addon_installed: bool, edits_stopped: bool = False}`, `RollbackIn{confirm: str}`), tag `admin:itsm-import`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56370–56399): non-admin → 403 on every route; `GET /status` on a core-only DB → `znuny_itsm_present=false`, `can_import=false`, `blocked_reason` = "In der Datenbank wurde kein Znuny ITSM gefunden."; with the fake source override: `POST /runs {"dry_run": true}` → 202 + job id, a `dry_run` run row; a second POST while active → 409; `POST /switch` with `confirm: "falsch"` → 400; preconditions missing → 409 with the `SwitchBlocked` message; `GET /runs` lists newest first with `started_by_name`; `POST /rollback` when not switched → 409. Every endpoint returns within the test without running the job (no inline work).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (pattern `api/v1/admin/reporting.py`; `start_run` + `enqueue_job(..., dedupe_key=IMPORT_DEDUPE_KEY)` in one transaction; `started_by_name` resolved by IN-list over `users`). **Step 4:** regenerate openapi (Global Constraints) and run tests + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): admin API for status, runs, switch and rollback"` (include both openapi files).

---

### Task 16: Importer section in `docs/itsm-import.md` + report codes reference (docs + backend constant)

**Files:**
- Modify: `docs/itsm-import.md` (append `## Importer: Zuordnung und Bericht`, C11)
- Modify: `backend/src/tiqora/itsm/importer/issue_codes.py` (created in Task 6)
- Test: `backend/tests/test_itsm_import_issue_codes.py`

**Interfaces:**
- Produces: `ISSUE_CODES: dict[str, str]` — code → German explanation shown in the UI and the CSV (`unsupported_object`: "Verknüpfung mit einem Objekt, das Tiqora (noch) nicht kennt (z. B. FAQ, Change)", `attachment_in_filesystem`, `attachment_too_large`, `unresolved_reference`, `unknown_attribute`, `bad_value`, `definition_unreadable`, `definition_converted_with_issues`, `no_definition`, `class_without_group`, `changed_in_tiqora`, `deleted_in_znuny`, `changes_not_imported`).

- [ ] **Step 1: Write the failing test**: every `code` string used in `backend/src/tiqora/itsm/importer/**` (`grep`-style scan in the test with `re.findall(r'issue\([^,]+,\s*"([a-z_]+)"', text)`) has an entry in `ISSUE_CODES`; every entry is used; every entry appears in `docs/itsm-import.md` as a table row.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the dict and the docs chapter (German, "Sie"): what is mapped to what (table Znuny → Tiqora for catalog, classes, definitions, CIs, versions, links per object type, attachments, matrix, service meta, changes), idempotency ("Sie können den Import beliebig oft wiederholen …"), conflict rule, the issue-code table, how to read the CSV.
- [ ] **Step 4: Run** + gates → PASS. **Step 5: Commit** — `git commit -m "docs(itsm-import): mapping and report codes of the Znuny ITSM importer"`

---

### Task 17: Frontend — API layer, demo mocks, admin page "ITSM-Daten aus Znuny übernehmen" (frontend)

**Files:**
- Create: `frontend/src/lib/itsmImportApi.ts`, `frontend/src/routes/admin/ItsmImportPage.tsx`, `frontend/src/routes/admin/ItsmImportPage.test.tsx`, `frontend/src/components/itsm-import/ImportStatusCard.tsx`, `frontend/src/components/itsm-import/ImportRunReport.tsx`, `frontend/src/components/itsm-import/ImportRunReport.test.tsx`, `frontend/src/demo/itsmImportMock.ts`, `frontend/src/demo/itsmImportMock.test.ts`
- Modify: `frontend/src/demo/mockData.ts` (delegate `/api/v1/admin/itsm/import/*` to `resolveItsmImport`), `frontend/src/router.tsx` (route `/admin/itsm-import` under `adminLayoutRoute`), `frontend/src/lib/adminSearch.ts` (entry slug `itsm-import`, group = the ITSM group itsm1/cmdb added, else `system`; keywords `["itsm", "cmdb", "znuny", "import", "übernehmen", "umstellen", "ci", "konfigurationselemente"]`), `frontend/src/i18n/locales/en.json`, `de.json` (+ propagate)

**Interfaces:**
- Consumes: Task 15 endpoints through the generated client types (`components["schemas"]["ItsmImportStatusOut"]` etc.); `JobDialog` (`frontend/src/components/jobs/JobDialog.tsx`).
- Produces: `itsmImportApi = { status(signal), startRun(dryRun: boolean), runs(signal), run(id, signal), switchToTiqora(body), rollback(body) }`; `ImportRunReport({ run }: { run: ImportRunOut })`; i18n namespace `admin.itsmImport.*`, job titles `jobs.kinds.itsm.import`, `jobs.kinds.itsm.switch`.

UI (German copy; English keys mirror it):
- Title "ITSM-Daten aus Znuny übernehmen"; intro: "Hier übernehmen Sie CIs, Klassen und Verknüpfungen aus Znuny ITSM in die CMDB von Tiqora. Sie können den Import beliebig oft wiederholen, bis Sie auf Tiqora umstellen."
- Status card: Modus ("Znuny führt das ITSM" / "Tiqora führt das ITSM" / "ITSM ausgeschaltet"), "Znuny ITSM gefunden: <Profil>" or the blocked reason, counts in Znuny (CIs, Klassen, Changes, Arbeitsaufträge), last run (kind, time, status).
- Buttons "Probelauf" (secondary) and "Übernehmen" (primary; confirm dialog "Die Daten werden in die CMDB von Tiqora geschrieben. Solange Znuny das ITSM führt, sehen Ihre Agenten weiter die Daten aus Znuny."), both disabled with the reason as tooltip when `can_import` is false; starting opens `JobDialog`.
- Runs table (newest first): Art (Probelauf/Übernahme/Umstellung/Zurück), Gestartet, von, Status, "Bericht" (expands `ImportRunReport`: per step created/updated/unchanged/skipped/problems with German step labels, first 200 issues with the text from `ISSUE_CODES` sent by the API, link "Vollständiger Bericht (CSV)" to the job file when the job still exists).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/routes/admin/ItsmImportPage.test.tsx
it("starts a dry run and opens the job dialog", async () => {
  mockApi({ status: statusFixture({ can_import: true }), startRun: { job_id: 7, status: "queued" } });
  renderWithProviders(<ItsmImportPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Probelauf" }));
  expect(itsmImportApi.startRun).toHaveBeenCalledWith(true);
  expect(await screen.findByTestId("job-dialog")).toBeInTheDocument();
});

it("disables import with the blocked reason", async () => {
  mockApi({ status: statusFixture({ can_import: false, blocked_reason: "Die ITSM-Daten werden bereits in Tiqora geführt." }) });
  renderWithProviders(<ItsmImportPage />);
  const btn = await screen.findByRole("button", { name: "Übernehmen" });
  expect(btn).toBeDisabled();
  expect(screen.getByText("Die ITSM-Daten werden bereits in Tiqora geführt.")).toBeInTheDocument();
});

it("asks for confirmation before a real import", async () => { ... });
```

`ImportRunReport.test.tsx`: renders step labels ("CIs (Grunddaten)", "CI-Versionen", "Verknüpfungen", "Anhänge", "Prioritätsmatrix", "Service-Angaben", "Changes (nur gezählt)", "In Znuny gelöscht", "Vorfallstatus") and an issue row; empty issues → "Keine Auffälligkeiten". `itsmImportMock.test.ts`: `resolveData("/api/v1/admin/itsm/import/status", "GET")` returns a Mode-A status with `can_import: true`, `POST …/runs` returns `{job_id, status: "queued"}`.

- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/routes/admin/ItsmImportPage.test.tsx src/components/itsm-import src/demo/itsmImportMock.test.ts` → FAIL.
- [ ] **Step 3: Implement** (pattern `ReportingAdminPage.tsx`: `useQuery` status with 10 s refetch while a job is active; `useMutation`; `JobDialog` with `onSucceeded` → invalidate status + runs). Add i18n keys to `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs`.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS. Visual check in demo mode (`VITE_DEMO=1`), delete `.playwright-mcp` before committing.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): admin page with dry run, import and run reports"`

---

### Task 18: Frontend — switch dialog, rollback, drift warning (frontend)

**Files:**
- Create: `frontend/src/components/itsm-import/SwitchDialog.tsx`, `SwitchDialog.test.tsx`, `RollbackDialog.tsx`, `RollbackDialog.test.tsx`
- Modify: `frontend/src/routes/admin/ItsmImportPage.tsx`, `frontend/src/demo/itsmImportMock.ts`, i18n `en.json`/`de.json` (+ propagate)

**Interfaces:**
- Consumes: `itsmImportApi.switchToTiqora({confirm, addon_installed, edits_stopped})`, `itsmImportApi.rollback({confirm})`.

UI:
- Button "Auf Tiqora umstellen" (visible in Mode A when `can_switch`; disabled with reason otherwise). Dialog text: what happens in order ("1. Znuny-Bildschirme zum Bearbeiten von CIs werden gesperrt (mit der Erweiterung TiqoraITSM). 2. Tiqora übernimmt ein letztes Mal alle Änderungen aus Znuny. 3. Ab dann pflegen Sie CIs nur noch in Tiqora."), radio "Die Erweiterung TiqoraITSM ist in Znuny installiert" / "… ist nicht installiert"; when not installed a required checkbox "In Znuny werden keine CIs mehr bearbeitet"; text field "Geben Sie UMSTELLEN ein, um zu bestätigen"; submit enabled only when valid; starts the switch job → `JobDialog` titled "Umstellung auf Tiqora".
- After the switch: green banner "Tiqora führt das ITSM seit <Datum> (umgestellt von <Name>)"; if `znuny_changes_since_switch > 0` an amber warning "Seit der Umstellung wurden in Znuny <n> CIs geändert. Diese Änderungen sind in Tiqora nicht enthalten." with a link to the docs chapter `docs/itsm.md#zurück-zu-modus-a`.
- Link-button "Zurück zu Znuny (Modus A)" → `RollbackDialog`: "Tiqora zeigt dann wieder die CIs aus Znuny. Änderungen, die Sie seit der Umstellung in Tiqora gemacht haben, sind in Znuny nicht sichtbar." + "Geben Sie ZURÜCK ein".

- [ ] **Step 1: Write the failing tests**: submit disabled until `UMSTELLEN` typed; not-installed path requires the checkbox and sends `edits_stopped: true`; drift warning renders with count; rollback sends `{confirm: "ZURÜCK"}` and refreshes the status.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** + i18n + mock states (switched with drift 3). **Step 4: Run** frontend gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(itsm-import): switch to Tiqora and back, with drift warning"`

---

### Task 19: `ConfigItemUpdate` — attribute merge, ported from Znuny (backend, pure)

**Files:**
- Create: `backend/src/tiqora/itsm/config_item_update.py` (pure part: `build_update`)
- Test: `backend/tests/test_itsm_config_item_update_pure.py`

**Interfaces:**
- Consumes: Task 4 (`expand_list_keys`, `xmldata_to_stringified`, `stringified_to_xmldata`, `definition.to_znuny_definition`, `xmldata.attributes_to_xmldata`/`xmldata_to_attributes`).
- Produces: `HEADER_KEYS = ("ConfigItemID", "Name", "ClassName", "ClassID", "DeplStateID", "InciStateID", "DeplStateName", "InciStateName")`; `@dataclass(frozen=True) class CiUpdate: name: str | None; depl_state_name: str | None; inci_state_name: str | None; depl_state_id: int | None; inci_state_id: int | None; attributes: dict[str, Any]`; `build_update(config: Mapping[str, Any], *, current_attributes: Mapping[str, Any], schema: Mapping[str, Any], separator: str = ",", customer_lookup: Callable[[str], list[str]] | None) -> CiUpdate` — the port of `ConfigItemAttributesGet` (remove `ConfigItemNumber`/`UserID`; header keys kept; non-`::` keys become `Key::`; expand; definition-driven merge with the original).

- [ ] **Step 1: Write the failing tests** — the Znuny `.t` scenarios (`znuny-6.5.22/scripts/test/ProcessManagement/TransitionAction/ConfigItemUpdate.t`): (1) lines ~280–300: `Vendor::1`, `CPU::1`, `Ram::1`, `HardDisk::1/2` with `Capacity::1` → expected `XMLData` at ~400–420 converted to attributes; (2) lines ~460–490: `'Vendor::1' => ''` deletes, `'CPU::' => 'NEW-2-CPU, NEW-2-CPU2'` → two CPUs, `DeplStateName => 'Planned'` → header; (3) keys not in the definition are ignored (Znuny drops them in `StringifiedXMLData2XMLData`); (4) `Separator` override `;`.

```python
def test_cpu_list_and_vendor_delete() -> None:
    upd = build_update(
        {"ConfigItemID": "5", "Vendor::1": "", "CPU::": "NEW-2-CPU, NEW-2-CPU2", "Ram::1": "NEW-2-Ram",
         "DeplStateName": "Planned", "InciStateName": "Operational", "UserID": "1"},
        current_attributes=_attrs({"Vendor": ["Vendor-Test"], "CPU": ["CPU-Test"], "Ram": ["Ram-Test"]}),
        schema=COMPUTER_SCHEMA, customer_lookup=None)
    assert upd.depl_state_name == "Planned"
    assert _values(upd.attributes, "CPU") == ["NEW-2-CPU", "NEW-2-CPU2"]
    assert _values(upd.attributes, "Vendor") == [""]
    assert _values(upd.attributes, "Ram") == ["NEW-2-Ram"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (current attributes → XMLData via `attributes_to_xmldata` → stringified original → merge → XMLData → attributes; catalog values stay ids). **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(process): ConfigItemUpdate attribute merge ported from Znuny"`

---

### Task 20: `ConfigItemUpdate` handler wiring (backend + small frontend flip)

**Files:**
- Modify: `backend/src/tiqora/itsm/config_item_update.py` (add `action_config_item_update`), `backend/src/tiqora/process/actions/registry.py` (`m("ConfigItemUpdate", config_item_update.action_config_item_update, _CONFIG_ITEM_UPDATE)` with a lazy import inside `core_transition_actions`, like `execute_invoker_action`; keep `_CONFIG_ITEM_UPDATE` and add `ActionParam("DeplStateName", "text")`, `ActionParam("InciStateName", "text")`, `ActionParam("Name", "text")`, `ActionParam("Separator", "text")`), `backend/src/tiqora/process/engine.py` (module docstring: remove "Only ConfigItemUpdate has no Tiqora handler", describe the mode gate), `frontend/src/components/process-designer/panels/ActionPanel.test.tsx` (fixture `supported: true` for `ConfigItemUpdate` and the assertion that changes with it), `docs/process-management.md:529` and `docs/compatibility.md:946` rows
- Test: `backend/tests/test_itsm_config_item_update_action.py`, `backend/tests/test_process_action_registry.py` (adjust)

**Interfaces:**
- Consumes: Task 19 `build_update`; ports `CmdbReadPort.ci_id_by_number/get_ci/catalog_item`, `CmdbWritePort.add_ci_version`, `ItsmModePort.effective_mode`.
- Produces: `async def action_config_item_update(session: AsyncSession, config: dict[str, Any], ticket_id: int, user_id: int, sysconfig: SysConfig) -> None` (the `ActionHandler` signature of `process/actions/registry.py`); raises `UnsupportedInMode` (subclass of the engine's "unsupported action" signal — reuse whatever the engine uses for `run is None`, verify in `engine.execute_transition_action`) when the mode is not `tiqora`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 56400–56429): mode `tiqora` + CI seeded through cmdb's service: `ConfigItemNumber: "2026101256401, 2026101256402"` updates both CIs (one new version each, `created_by` = acting user; `UserID` in config overrides it); duplicate ids → one version; unknown number → logged, no exception, other CI still updated; unchanged data → no new version; `DeplStateName` unknown → logged and header unchanged (Znuny: `_ITSMConfigItemVersionAdd` fails → returns); mode `znuny_readthrough` → the engine records the action in `unsupported_actions` (assert through `execute_transition_action` with a minimal process fixture from `tests/test_process_*`), no CMDB write; `registry.resolve_action("ConfigItemUpdate").run is not None`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** per D10 (customer lookup = `CustomerSearch`-like: `customer_user.login` exact match first, else `LIKE` on login/email/first/last name, valid only, max 2 rows — enough to tell "unique"). **Step 4: Run** backend tests + gates, and `cd frontend && pnpm lint && pnpm test --run src/components/process-designer` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(process): ConfigItemUpdate transition action on the Tiqora CMDB"`

---

### Task 21: GI `ConfigItem*` — common layer, routing, `ConfigItemGet` (backend)

**Files:**
- Create: `backend/src/tiqora/api/compat/config_items.py`, `backend/tests/fixtures/itsm/gi_shapes.md` (notes from the source survey, see Step 1)
- Modify: `backend/src/tiqora/api/compat/webservices.py` (`OPERATION_TYPES` + 5 entries), `backend/src/tiqora/api/compat/router.py` (5 branches in `_dispatch_operation`, canonical routes per the REST YAML, `__all__`/imports), `backend/src/tiqora/api/compat/operations.py` (`__all__` untouched; `_auth_from_params`, `_err`, `_reprefix_auth_error` are imported from there)
- Test: `backend/tests/test_compat_config_items_get.py`

**Interfaces:**
- Consumes: ports (`CmdbReadPort`, `ItsmModePort`), Task 5 (`xmldata_to_cixml`, `GiLookups`), Task 4 (`attributes_to_xmldata`, `to_znuny_definition`), itsm1 adapter for Mode A reads (through `ZnunyAdapterSource`/a read facade added here: `facades.ModeAReader.get_ci(znuny_id) -> CiView | None`, `search(query) -> list[int]`).
- Produces: `async def op_config_item_get(data, session, session_store, *, request=None, settings=None) -> dict[str, Any]` and the same signature for `op_config_item_search`, `op_config_item_create`, `op_config_item_update`, `op_config_item_delete` (the last four answer `{"Error": {"ErrorCode": "Operation.NotImplemented", …}}` until Tasks 22–24); helpers `async def _gi_ports(session) -> tuple[Ports, str]` (ports + effective mode), `def _mode_error(op: str, mode: str) -> dict | None`, `async def _agent_auth(op, data, session, session_store, request, settings) -> tuple[int, str] | dict` (agent-only; customer path only if Step 1 shows Znuny allows it), `async def _gi_lookups(session, definition) -> GiLookups` (preloads catalog names/ids for the classes in the definition, customer existence checked lazily by IN-list).

- [ ] **Step 1: Source survey (write it down before coding).** From ITSMConfigurationManagement 6.5 **and** 7.3 tags: `Kernel/GenericInterface/Operation/ConfigItem/ConfigItemGet.pm` (parameters: `ConfigItemID` comma list or array, `Attachments`; response `ConfigItem` list with keys — expected `ConfigItemID`, `Number`, `Class`, `Name`, `DeplState`, `DeplStateType`, `InciState`, `InciStateType`, `CurDeplState`, `CurDeplStateType`, `CurInciState`, `CurInciStateType`, `CreateTime`, `CreateBy`, `ChangeTime`?, `VersionID`, `CIXMLData`, `Attachment` with base64 `Content`), auth and permission checks (`ITSMConfigItem->Permission(Scope => 'Item', ItemID, UserID, Type => $Self->{Config}->{Permission})`?), error codes and texts, single-vs-list response rule; `development/webservices/GenericConfigItemConnectorREST.yml` for routes/methods; SysConfig `GenericInterface::Operation::ConfigItemGet###Permission`. Record everything in `backend/tests/fixtures/itsm/gi_shapes.md` with file/line references; differences 6.5 vs 7.3 in a separate table (open question 11).

- [ ] **Step 2: Write the failing tests** (both dialects, band 56430–56459; CIs seeded through cmdb's service; agents `itim56.gi.ro` with `ro` on the class group, `itim56.gi.none` without):

```python
async def test_get_returns_znuny_shape(client_and_ids) -> None:
    client, ids = client_and_ids
    r = await client.get(f"/znuny-compat/ConfigItem/{ids.ci}", params={"UserLogin": "itim56.gi.ro", "Password": PW})
    body = r.json()
    item = body["ConfigItem"] if isinstance(body["ConfigItem"], dict) else body["ConfigItem"][0]
    assert item["Number"] == "2026101256431"
    assert item["Class"] == "Computer"
    assert item["CIXMLData"]["Vendor"] == "itim56 Hersteller"


async def test_get_denies_class_without_ro(client_and_ids) -> None:
    r = await client.get(f"/znuny-compat/ConfigItem/{ids.ci}", params={"UserLogin": "itim56.gi.none", "Password": PW})
    assert r.json()["Error"]["ErrorCode"] == "ConfigItemGet.AccessDenied"   # exact code from Step 1


async def test_get_mode_off_not_implemented(...) -> None: ...          # itsm.mode=off -> Operation.NotImplemented
async def test_get_in_mode_a_reads_znuny(...) -> None: ...             # FakeModeAReader returns a CiView
async def test_get_with_attachments_base64(...) -> None: ...
async def test_get_multiple_ids_and_unknown_id(...) -> None: ...       # error text verbatim from Step 1
async def test_webservice_route_dispatches_config_item_get(...) -> None:
    # a gi_webservice_config row with Type ConfigItem::ConfigItemGet routes to op_config_item_get
```

- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** (attachments content via cmdb's attachment read; `CreateBy` as Znuny does — user id; times `YYYY-MM-DD HH:MM:SS`). **Step 5: Run** tests + gates; regenerate openapi (canonical routes are part of the app) → PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat(gi): ConfigItemGet on the Tiqora CMDB and ConfigItem routing"`

---

### Task 22: GI `ConfigItemSearch` (backend)

**Files:**
- Modify: `backend/src/tiqora/api/compat/config_items.py`
- Test: `backend/tests/test_compat_config_items_search.py`

**Interfaces:**
- Consumes: `CmdbReadPort.search_cis(CiQuery, class_ids, limit, offset)`, `visible_class_ids(user, "ro")`.
- Produces: `op_config_item_search` (request `ConfigItem` hash — verify keys in `ConfigItemSearch.pm`: `Number`, `Name`, `ClassIDs`/`Class`, `DeplStates`, `InciStates`, `CIXMLData`, `PreviousVersionSearch`, `OrderBy`, `OrderByDirection`, `Limit`, `ConfigItemCreateTimeNewerDate`/`OlderDate`, `ConfigItemChangeTimeNewerDate`/`OlderDate`; response `ConfigItemIDs` list, or the "no result" error/empty answer exactly as Znuny returns it).

- [ ] **Step 1: Write the failing tests** (both dialects, band 56460–56489): wildcard `Name: "itim56-*"`; class filter by name; `DeplStates: ["Production"]`; attribute filter through `CIXMLData: {"Vendor": "itim56*"}` (uses cmdb's attribute index); CIs of a class without `ro` are omitted silently (Znuny filters, verify); `Limit`; ordering; unknown class name → Znuny's error; no result → Znuny's answer (verify: empty `ConfigItemIDs` or error); `PreviousVersionSearch: 1` → **Assumption:** searches all versions' names via cmdb's version table if cmdb exposes it, otherwise answers like the current-version search and logs `gi_previous_version_search_unsupported` (documented in compatibility.md).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(gi): ConfigItemSearch on the Tiqora CMDB"`

---

### Task 23: GI `ConfigItemCreate` and `ConfigItemUpdate` (backend)

**Files:**
- Modify: `backend/src/tiqora/api/compat/config_items.py`
- Test: `backend/tests/test_compat_config_items_write.py`

**Interfaces:**
- Consumes: Task 5 `cixml_to_xmldata`, Task 4 `xmldata_to_attributes`, `CmdbWritePort.create_ci/add_ci_version/add_ci_attachment`, cmdb's normal CI number generator (through the facade: `CmdbServiceWriter.next_number(class_id) -> str` — add to the port in D3 if cmdb has it, else creation uses cmdb's own create function that numbers itself; record which in `facades.py`).
- Produces: `op_config_item_create` (request `ConfigItem{Class, Name, DeplState, InciState, CIXMLData, Attachment?}` → `{ConfigItemID, Number}`), `op_config_item_update` (`ConfigItemID` + `ConfigItem{…}` → `{ConfigItemID, ConfigItemVersionID, Number}`; verify response keys and whether `Attachment` on update adds or replaces).

- [ ] **Step 1: Write the failing tests** (both dialects, band 56490–56529): create with valid data → CI exists with one version, attributes converted (GeneralCatalog name → Tiqora catalog id), response keys exact; missing `Class` → `ConfigItemCreate.MissingParameter` with Znuny's text; unknown `DeplState` → Znuny's `InvalidParameter` text; `CIXMLData` violating `CountMax` → Znuny's error; agent without `rw` on the class group → `AccessDenied`; Mode A → `ConfigItemCreate.NotAvailable` "Die CMDB wird in Znuny geführt (Modus A)."; update creates exactly one new version, unchanged payload → no new version but Znuny's success answer with the current version id (verify Znuny: VersionAdd returns the old id when unchanged); attachments base64 decoded, content type kept, max 50 MB each (`InvalidParameter` beyond).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (session commit at the end like `op_out_of_office`; history/audit as cmdb's service does). **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(gi): ConfigItemCreate and ConfigItemUpdate on the Tiqora CMDB"`

---

### Task 24: GI `ConfigItemDelete`, SOAP check, compatibility rows (backend + docs)

**Files:**
- Modify: `backend/src/tiqora/api/compat/config_items.py`, `docs/compatibility.md` (GI operations table: five `ConfigItem*` rows with "implemented on the Tiqora CMDB", Mode A/off behaviour, `PreviousVersionSearch` note, per-version shape note)
- Test: `backend/tests/test_compat_config_items_delete.py`, `backend/tests/test_compat_config_items_soap.py`

**Interfaces:**
- Produces: `op_config_item_delete` (`ConfigItemID` comma list/array → response per source survey, e.g. `{"ConfigItemID": [..]}`; partial failure semantics verbatim from `ConfigItemDelete.pm`).

- [ ] **Step 1: Write the failing tests** (both dialects, band 56530–56549): delete two CIs → gone from cmdb (through `CmdbReadPort.get_ci` → `None`), their links removed (cmdb semantics), map rows of the importer untouched (re-import would recreate — documented); no `rw` → `AccessDenied`; Mode A → `NotAvailable`. SOAP: `POST /znuny-compat/soap/<webservice>` with a `ConfigItemGet` envelope returns the same data as REST (pattern of the existing SOAP tests, `grep -ln "soap" backend/tests | head`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** + docs rows. **Step 4: Run** + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(gi): ConfigItemDelete and SOAP for the ConfigItem operations"`

---

### Task 25: MCP tools `cmdb_search`, `cmdb_get`, `cmdb_impact` (backend)

**Files:**
- Create: `backend/src/tiqora/mcp_server/cmdb_tools.py`
- Modify: `backend/src/tiqora/mcp_server/server.py` (last line: `from tiqora.mcp_server import cmdb_tools  # noqa: E402,F401  — registers the CMDB tools`)
- Test: `backend/tests/test_mcp_cmdb_tools.py`; adjust `backend/tests/test_mcp_server.py::test_mcp_tools_list` (expected tool names) and `test_mcp_every_tool_checks_allowlist` (it must cover the new module)

**Interfaces:**
- Consumes: `server.mcp`, `server._get_user_id`, `server._get_state`, `server._assert_tool_allowed`; ports `CmdbReadPort` (`visible_class_ids`, `search_cis`, `get_ci`, `ci_id_by_number`, `impact`), `ItsmModePort`, Mode A read facade (Task 21).
- Produces (D11): `cmdb_search(ctx, query="", class_key=None, deployment_state=None, incident_state=None, limit=20) -> list[dict]` (each `{ci_id, number, name, class, deployment_state, incident_state}`; `limit` clamped 1–100); `cmdb_get(ctx, ci_id=None, number=None) -> dict` (`{ci_id, number, name, class, deployment_state, incident_state, version_id, changed, attributes: {label: value(s)}, links: [{kind, key, link_type, direction}]}`; attribute labels from the schema, catalog ids rendered as names; max 200 attribute values, then `"truncated": true`); `cmdb_impact(ctx, ci_id, max_depth=4) -> dict` (`{ci, services, customers, open_tickets, cis, truncated}`; `max_depth` clamped 1–8).

- [ ] **Step 1: Write the failing tests** (MariaDB like `test_mcp_server.py` + one PostgreSQL case for search; band 56550–56579):

```python
async def test_cmdb_search_hides_foreign_classes(mcp_db) -> None:
    with _patch_user_id(AGENT_RO_COMPUTER):
        hits = await _call("cmdb_search", {"query": "itim56"})
    assert {h["class"] for h in hits} == {"Computer"}            # "Network" CI of a foreign group hidden


async def test_cmdb_get_by_number_renders_catalog_names(mcp_db) -> None: ...
async def test_cmdb_impact_lists_customers_and_open_tickets(mcp_db) -> None:
    # switch CI -> DependsOn <- server CI -> linked ticket of customer itim56.kunde@example.org
    ...
    assert out["customers"] == [{"login": "itim56.kunde@example.org", "name": "Kunde itim56"}]


async def test_cmdb_tools_are_read_only_annotated() -> None:
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("cmdb_search", "cmdb_get", "cmdb_impact"):
        assert tools[name].annotations.readOnlyHint is True


async def test_cmdb_impact_in_mode_a_explains(mcp_db) -> None: ...
@pytest.mark.parametrize("tool_name", ["cmdb_search", "cmdb_get", "cmdb_impact"])
async def test_cmdb_tools_respect_tool_allowlist(tool_name) -> None: ...   # pattern test_mcp_tool_allowlist_enforced
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `@mcp.tool(description=..., annotations={"readOnlyHint": True})` (verify the parameter name in the pinned fastmcp: `grep -n "annotations" $(uv run python -c "import fastmcp,os;print(os.path.dirname(fastmcp.__file__))")/tools/tool.py`); every tool: `_get_user_id(ctx)`, `_assert_tool_allowed(name)`, own session from `_get_state().session_factory`, mode check, permission filter. Descriptions in English, mention "read-only".
- [ ] **Step 4: Run** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_mcp_cmdb_tools.py tests/test_mcp_server.py` + gates → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(mcp): read-only CMDB tools cmdb_search, cmdb_get, cmdb_impact"`

---

### Task 26: Znuny add-on `TiqoraITSM` — package, system module, agent ticket-zoom widget (Perl + golden)

**Files:**
- Create: `packages/znuny-addon/TiqoraITSM/TiqoraITSM.sopm`, `LICENSE` (copy of TiqoraSync's), `Kernel/Config/Files/XML/TiqoraITSM.xml`, `Kernel/System/TiqoraITSM.pm`, `Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm`, `Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt`, `Kernel/Language/de_TiqoraITSM.pm`, `install/README.md`
- Modify: `packages/znuny-addon/README.md` (second package)
- Test: `tests/golden/test_tiqoraitsm_addon.py`

**Interfaces:**
- Consumes: views (D2, C13), `tiqora_settings` keys `itsm.mode`, `itsm.znuny_readonly`.
- Produces (Perl, `Kernel::System::TiqoraITSM`): `Active() -> 0|1`, `ReadOnly() -> 0|1`, `TicketCIs(TicketID => $ID, UserID => $UserID) -> \@CIs` (each `{CIID, Number, Name, ClassName, DeplState, DeplFunctionality, InciState, InciFunctionality, LinkType, URL}`), `CustomerCIs(CustomerUserID => $Login, CustomerID => $ID) -> \@CIs`, `CIURL(CIID => $ID) -> $URL`, `_SettingGet(Key => $Key)`, `_KeyColumn()`, `_ViewsPresent()`. SysConfig settings: `TiqoraITSM::BaseURL` (string, default `https://tiqora.example.org`), `TiqoraITSM::AgentCIPath` (default the cmdb agent route, e.g. `/agent/cmdb/ci/` — verify in cmdb's router), `TiqoraITSM::CustomerCIPath`, `TiqoraITSM::ReadOnly::Force` (0), `TiqoraITSM::ReadOnly::Actions` (array, Task 27), `TiqoraITSM::Linking::Enabled` (0), `TiqoraITSM::APIKey` (string, empty), `Ticket::Frontend::AgentTicketZoom###Widgets###0350-TiqoraITSM` (`Module` `Kernel::Output::HTML::TicketZoom::TiqoraITSM`, `Location` `Sidebar`, `Async` `1`).

- [ ] **Step 1: Write the failing golden test** (pattern `tests/golden/test_tiqorasync_deploy.py`: module fixture copies every add-on file in with `znuny_copy_in`, rebuilds the config with `bin/console.pl Maint::Config::Rebuild`, creates the `tiqora_*` tables the golden DB lacks — cmdb tables via `TiqoraBase.metadata.create_all` on the golden DB URL + the views via `addon_views.create_views_sync` — seeds 2 classes (group `users` and a group `itim56-geheim`), 3 CIs and links to a seeded ticket, and drops everything again at teardown):

```python
def test_widget_lists_linked_cis_in_mode_b(addon) -> None:
    _set_setting("itsm.mode", "tiqora")
    out = _perl_widget_output(ticket_id=addon.ticket_id, user_login="root@localhost")
    assert "Betroffene CIs (Tiqora)" in out
    assert "itim56 Switch Etage 3" in out and "/agent/cmdb/ci/" in out


def test_widget_hides_ci_of_foreign_class(addon) -> None:
    out = _perl_widget_output(ticket_id=addon.ticket_id, user_login="itim56.agent")   # not in itim56-geheim
    assert "itim56 Geheimer Server" not in out


def test_widget_inactive_in_mode_a(addon) -> None:
    _set_setting("itsm.mode", "znuny_readthrough")
    assert _perl_widget_output(ticket_id=addon.ticket_id, user_login="root@localhost") == ""


def test_auto_mode_follows_configitem_table(addon) -> None:
    _delete_setting("itsm.mode")      # core peer: no configitem table -> Mode B -> active
    assert _perl_eval("print $Kernel::OM->Get('Kernel::System::TiqoraITSM')->Active();") == "1"


def test_views_missing_is_quiet(addon_without_views) -> None:
    assert _perl_widget_output(ticket_id=1, user_login="root@localhost") == ""
```

`_perl_widget_output` runs `Kernel::Output::HTML::TicketZoom::TiqoraITSM->new(UserID => …)->Run(Ticket => {TicketID => …}, Config => {})` through `znuny_perl_eval` with a `Kernel::Output::HTML::Layout` created for that user, printing `$Result->{Output} // ''`.

- [ ] **Step 2: Run** `GOLDEN=1 GOLDEN_PEER=znuny-6.5 just golden-test tests/golden/test_tiqoraitsm_addon.py` (or the cached-image recipe from `tests/golden/README-multi-peer.md`) → FAIL (files missing).
- [ ] **Step 3: Implement.** `.sopm`: name `TiqoraITSM`, version `1.0.0`, Frameworks 6.0.x–7.3.x (as TiqoraSync), descriptions en/de (German with "Sie"), `Filelist` of every file. `TiqoraITSM.pm`: DB reads with bound parameters only (`SELECT ci_id, link_type FROM tiqora_v1_itsm_ci_links WHERE object_kind = ? AND object_key = ?`, then `SELECT … FROM tiqora_v1_itsm_ci WHERE ci_id IN (?, ?, …)`, ≤ 100 CIs per ticket), permission filter with `Kernel::System::Group->PermissionUserGet(UserID => $UserID, Type => 'ro')` (group ids), `Active()` per D12 (table check via `$DBObject->Prepare(SQL => 'SELECT 1 FROM configitem', Limit => 1)` inside `eval`, cached per request), never dies (every DB call in `eval`, logs at `debug`/`notice`). Widget: returns nothing when inactive/empty; template with `WidgetSimple` markup like `MentionsTable.tt`, state dot colours by functionality (`operational` green, `warning` orange, `incident` red), every value `| html`, URLs `| url`-safe. Language file: German strings. `install/README.md`: build/install/verify/uninstall like TiqoraSync's, plus "Was das Paket nie tut: in die Datenbank schreiben".
- [ ] **Step 4: Run** the golden test on `znuny-6.5` **and** `znuny-7.3` → PASS. Record the result line in `tests/golden/README-multi-peer.md` (date, peers).
- [ ] **Step 5: Commit** — `git commit -m "feat(znuny-addon): TiqoraITSM package with the 'Betroffene CIs (Tiqora)' ticket widget"`

---

### Task 27: `TiqoraITSM` — read-only policy, customer page, optional linking (Perl + golden)

**Files:**
- Create: `packages/znuny-addon/TiqoraITSM/Kernel/Modules/TiqoraITSMReadOnly.pm`, `Kernel/Modules/CustomerTiqoraITSM.pm`, `Kernel/Output/HTML/Templates/Standard/CustomerTiqoraITSM.tt`, `Kernel/Modules/AgentTiqoraITSMLink.pm`
- Modify: `TiqoraITSM.sopm` (files, version stays 1.0.0 before release), `Kernel/Config/Files/XML/TiqoraITSM.xml`, `Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm` + template (link button when linking is enabled), `Kernel/Language/de_TiqoraITSM.pm`, `install/README.md`
- Test: `tests/golden/test_tiqoraitsm_addon.py` (extend)

**Interfaces:**
- Consumes: Task 26 system module; C10 (`POST /api/v1/cmdb/links`).
- Produces: SysConfig `PreApplicationModule###TiqoraITSMReadOnly` (`Kernel::Modules::TiqoraITSMReadOnly`), `TiqoraITSM::ReadOnly::Actions` (array; default list verified from ITSMConfigurationManagement/ITSMChangeManagement 6.5 + 7.3 `Frontend::Module###…` XML: every add/edit/delete/bulk/attachment-upload/class-definition screen; read screens excluded — write the verified list into the XML and into `install/README.md`), `CustomerFrontend::Module###CustomerTiqoraITSM` + `CustomerFrontend::Navigation###CustomerTiqoraITSM###002-TiqoraITSM` (both `Valid="0"`; verify the 6.x/7.x setting names for customer navigation in core `Kernel/Config/Files/XML/Framework.xml`), `Frontend::Module###AgentTiqoraITSMLink` (`Valid="1"`, does nothing unless `TiqoraITSM::Linking::Enabled`).

- [ ] **Step 1: Write the failing golden tests**

```python
def test_readonly_blocks_ci_edit_screen(addon) -> None:
    _set_setting("itsm.znuny_readonly", "1")
    out = _perl_prerun(action="AgentITSMConfigItemEdit", user_login="root@localhost")
    assert "Die CMDB wird jetzt in Tiqora gepflegt" in out


def test_readonly_lets_zoom_through(addon) -> None:
    _set_setting("itsm.znuny_readonly", "1")
    assert _perl_prerun(action="AgentITSMConfigItemZoom", user_login="root@localhost") == ""


def test_readonly_off_lets_edit_through(addon) -> None:
    _set_setting("itsm.znuny_readonly", "0")
    assert _perl_prerun(action="AgentITSMConfigItemEdit", user_login="root@localhost") == ""


def test_customer_page_lists_own_and_company_cis(addon) -> None:
    out = _perl_customer_page(customer_login="itim56.kunde@example.org")
    assert "itim56 Drucker 2. OG" in out and "itim56 Switch Etage 3" in out   # user link + company link
    assert "itim56 Geheimer Server" not in out


def test_link_module_refuses_when_disabled(addon) -> None:
    assert '"Success":0' in _perl_link_call(ticket_id=addon.ticket_id, ci_id=addon.ci_a)


def test_link_module_posts_with_bearer_key(addon, gi_sink) -> None:
    # TiqoraITSM::BaseURL -> the golden gi_sink (tests/golden/gi_sink.py), Linking enabled, APIKey set
    _perl_link_call(ticket_id=addon.ticket_id, ci_id=addon.ci_a)
    req = gisink_requests()[-1]
    assert req["path"] == "/api/v1/cmdb/links" and req["headers"]["authorization"] == "Bearer itim56-test-key"
    assert json.loads(req["body"]) == {"ci_id": addon.ci_a, "target_kind": "ticket",
                                       "target_id": str(addon.ticket_id), "link_type": "RelevantTo",
                                       "note": "Verknüpft in Znuny von root@localhost"}
```

(`_perl_prerun` constructs `Kernel::Modules::TiqoraITSMReadOnly->new(Action => …, UserID => …)` and prints `PreRun()`'s output or ''. The PreRun output is a full error page built with `$LayoutObject->ErrorScreen(Message => …, Comment => …)` — verify that call is safe in a PreApplication module on both peers, otherwise `Header + Warning + Footer`.)

- [ ] **Step 2: Run** golden on `znuny-6.5` → FAIL. **Step 3: Implement.** Read-only module: no DB writes; action list from SysConfig; message + link `TiqoraITSM::BaseURL`. Customer page: lists via `CustomerCIs`, links to `CustomerCIPath`. Link module: agent `rw` on the ticket (`TicketPermission(Type => 'rw')`), class group `rw` check on the CI's `class_group_id`, `Kernel::System::WebUserAgent->Request(Type => 'POST', URL => …, Data => JSON, Header => {Authorization => "Bearer $Key", 'Content-Type' => 'application/json'})`, the key never written to logs or output; JSON response `{Success, Message}` (`$LayoutObject->Attachment(ContentType => 'application/json; charset=utf8', …)`).
- [ ] **Step 4: Run** golden on `znuny-6.5` and `znuny-7.3` → PASS (+ README-multi-peer line).
- [ ] **Step 5: Commit** — `git commit -m "feat(znuny-addon): read-only CI screens after the switch, customer CI page, optional linking"`

---

### Task 28: Golden — importer, GI `ConfigItem*` and `ConfigItemUpdate` against Znuny ITSM peers (golden)

**Files:**
- Create: `tests/golden/test_itsm_import.py`, `tests/golden/test_gi_config_items.py`
- Modify (only if C12 peer variant is missing): `tests/golden/peers.yaml`, `tests/golden/Dockerfile.znuny` (install GeneralCatalog, ITSMCore, ITSMIncidentProblemManagement, ITSMConfigurationManagement, ITSMChangeManagement OPMs for the peer's major version behind build arg `ITSM=1`), `tests/golden/znuny-entrypoint.sh`, `tests/golden/README-multi-peer.md`

**Interfaces:**
- Consumes: everything above; golden helpers `znuny_perl_eval`, `znuny_console`, `gisink_*`.

- [ ] **Step 1: Write the golden tests.**
  - `test_itsm_import.py`: create in Znuny through Perl (`Kernel::System::ITSMConfigItem` API, so the data is what Znuny really writes): 2 classes (default `Computer` + a custom class with a nested `Sub` attribute, a `CI`-type reference and a `GeneralCatalog` value), 5 CIs with 1–3 versions each (`VersionAdd`), CI↔CI and CI→Ticket links (`LinkObject->LinkAdd`), one attachment (`ConfigItemAttachmentAdd`); then run `run_import` against the golden DB. Assert: Tiqora version count per CI == Znuny's `VersionList` count; latest attributes == Znuny `VersionGet(XMLDataGet => 1)` converted with `xmldata_to_attributes`; the CI reference resolves to the Tiqora id of the referenced CI; links == Znuny `LinkListWithData` (kinds/types); attachment bytes equal; a second run reports only `unchanged`; after `VersionAdd` in Znuny a third run adds exactly one version; a dry run before the first run creates nothing. Clean up by deleting the Znuny CIs (`ConfigItemDelete`) and the Tiqora rows.
  - `test_gi_config_items.py`: for the same CI, Znuny's own `GenericConfigItemConnectorREST` (imported with `Admin::WebService::Add` from the package's YAML, called on the peer's `/otrs/nph-genericinterface.pl/Webservice/…`) and Tiqora's `/znuny-compat/ConfigItem/<id>` (after import, mode `tiqora`, Tiqora id ↔ Znuny id via the import map) return equal `ConfigItemGet` bodies modulo ids/times; `ConfigItemSearch` with the same `Name` wildcard returns the same set (mapped ids); error bodies for a missing parameter are identical; `ConfigItemUpdate` with the same payload creates one version on each side with equal resulting attributes.
  - In `test_gi_config_items.py`, `test_config_item_update_action_matches_znuny`: run Znuny's `TransitionAction::ConfigItemUpdate->Run(...)` and Tiqora's `action_config_item_update` with the `.t` scenario 2 config on the same imported CI pair; compare resulting attributes.
- [ ] **Step 2: Run** on `znuny-6.5-itsm` and `znuny-7.3-itsm` (ids as itsm1 defined them): `GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm just golden-test tests/golden/test_itsm_import.py tests/golden/test_gi_config_items.py` → expect failures only where the earlier tasks' "verify" notes were wrong; fix those in the owning module (with a unit-test vector added there), not in the golden test.
- [ ] **Step 3: Run** both peers → PASS; record results (date, peer, image) in `tests/golden/README-multi-peer.md`.
- [ ] **Step 4: Commit** — `git commit -m "test(golden): ITSM importer, ConfigItem operations and ConfigItemUpdate against Znuny ITSM 6.5/7.3"`

---

### Task 29: Docs (coexistence chapter) + full verification (docs)

**Files:**
- Modify/Create: `docs/itsm.md` (this plan's chapters per C11), `docs/parallel-operation.md` (short pointer section "ITSM: Modus A und B" → `itsm.md#koexistenz-mit-znuny`), `docs/guide/znuny-to-tiqora.md` (one stage line: "ITSM übernehmen und umstellen" with link), `docs/compatibility.md` (rows: ITSM importer, `ConfigItemUpdate` action now implemented, TiqoraITSM add-on; the "Never taken over: ITSM statistics" line unchanged), `docs/architecture.md` (one paragraph: importer ports, views as the add-on contract), `packages/znuny-addon/README.md`
- Test: `backend/tests/test_docs_itsm_links.py` (anchors referenced from the frontend and other docs exist)

Content of `## Koexistenz mit Znuny` (German, "Sie", plain words):
1. **Die drei Modi** — table: Modus | Wer pflegt CIs | Was Agenten in Tiqora sehen | Was Agenten in Znuny sehen | Was Kunden sehen. Rows: "ITSM ausgeschaltet", "Znuny führt das ITSM (Modus A)", "Tiqora führt das ITSM (Modus B)". State the automatic default (Znuny ITSM gefunden → Modus A, sonst Modus B) and the recommendation: Modus B ist das Ziel.
2. `### Was Sie in welchem Modus sehen` — screens per mode, incl. the add-on widget and the customer page.
3. `### Umstellung auf Tiqora (Ablauf)` — runbook: (1) Probelauf, Bericht lesen; (2) Übernehmen, beliebig oft wiederholen; (3) TiqoraITSM in Znuny installieren (Verweis auf `install/README.md`), `TiqoraITSM::BaseURL` setzen; (4) Znuny-Webservices mit `ConfigItem*`-Operationen auf Tiqora umstellen (Znuny-GI kann nicht gesperrt werden); (5) "Auf Tiqora umstellen"; (6) prüfen: Widget, gesperrte Bearbeitungsmasken, Drift-Hinweis = 0.
4. `### Was bleibt, was entfällt (je Znuny-Bildschirm)` — table over Znuny ITSM screens (CI-Übersicht, CI-Zoom, CI bearbeiten/anlegen/löschen, Massenaktion, Klassen-Definition (Admin), Import/Export, Change-/Arbeitsauftrag-Masken, Kundenansicht der CIs, Service-Zoom, ITSM-Statistiken): after the switch "bleibt (nur lesen)", "gesperrt durch TiqoraITSM", "in Tiqora: <Ort>", "entfällt (siehe Phase 3)".
5. `### Zurück zu Modus A` — rollback, what is lost (Tiqora-side changes invisible in Znuny), how to re-import afterwards.
6. Known gaps: Znuny GI/console/ImportExport not blocked; change data counted only; CI attachments in the FS backend.

`## ITSM-Daten aus Znuny übernehmen` — the admin page, what each run kind does, report reading (link to `itsm-import.md`), CLI (`tiqora itsm import --dry-run`, `tiqora itsm status`).
`## Znuny-Erweiterung TiqoraITSM` — what it shows/does, settings table (all `TiqoraITSM::*` keys with defaults), linking opt-in and its permission model (open question 7), customer page opt-in.

- [ ] **Step 1: Write the failing test**: `test_docs_itsm_links.py` asserts that `docs/itsm.md` contains the headings this plan owns and that every `docs/itsm.md#…` anchor used in `frontend/src` and `docs/` resolves (GitHub slug rules: lowercase, umlauts kept, spaces → `-`, punctuation dropped).
- [ ] **Step 2: Run** → FAIL. **Step 3: Write the docs.** **Step 4: Run** the test → PASS.
- [ ] **Step 5: Full verification** (all must pass, paste the summary lines into the commit message body):
  - `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (whole suite)
  - `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`
  - `cd backend && uv run alembic heads` → one primary head
  - openapi regenerated and unchanged by a second regeneration (`git diff --exit-code packages/api-client/openapi.json docs/api/openapi.json`)
  - `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`
  - golden (manual, record in README-multi-peer): `test_tiqoraitsm_addon.py` on `znuny-6.5`, `znuny-7.3`; `test_itsm_import.py`, `test_gi_config_items.py` on the two ITSM peers
  - `grep -rnE "@(gmail|web|gmx)\.|cygnus" docs/itsm.md packages/znuny-addon/TiqoraITSM backend/tests/*itsm* tests/golden/*itsm*` → no real addresses (PII hook runs anyway)
- [ ] **Step 6: Commit** — `git commit -m "docs(itsm): coexistence with Znuny, importer and TiqoraITSM add-on"`

---

## Self-review notes (for the executor)

- **Spec coverage:** importer (catalog, classes + 6.x/7.x definitions, CIs + all versions, links, cip_allocate + service meta via itsm1, change_* counted) → Tasks 3–14; dry run / re-runnable / async job / report → 6, 12, 13, 15–17; explicit switch + read-only policy → 14, 18, 27; GI `ConfigItemCreate/Get/Search/Update/Delete` → 5, 21–24; `ConfigItemUpdate` action with Znuny's keys → 19–20; MCP `cmdb_search/get/impact` → 25; add-on widgets + customer view + optional linking + read-only → 26–27; views → 2; golden ITSM peers → 28; `docs/itsm.md` coexistence chapter + compare pointers → 29. Legacy change archive deferred (Deviation 1).
- **Type consistency:** `ImportSource`, `CmdbWritePort`, `CmdbReadPort`, `ItsmModePort`, `Ports`, `get_ports` (Task 1) are the only names other tasks use for itsm1/cmdb code; `StepContext`, `MapStore`, `ImportReport`, `DryRunWriter` (Task 6); `ClassMaps`/`DefinitionTarget` (Task 8) feed Tasks 9–12; `run_import` (Task 12) is used by Tasks 13–14 and the CLI; `ISSUE_CODES` (Task 16) by Tasks 15/17; `addon_views.view_sql` (Task 2) by Tasks 26–27 fixtures.
- **Order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 … 16 strictly; 17–18 after 15; 19–20 after 4; 21–24 after 5 and 1; 25 after 1 and 21 (Mode A read facade); 26–27 after 2; 28 after everything backend; 29 last. Tasks 19–20, 21–24, 25, 26–27 can run in parallel worktrees once 1–6 are merged.
- **Things that are "verify in source" by design** (the ITSM packages are not local): definition location/format (Task 3), GI shapes and error texts (Tasks 5, 21–24), attachment storage (Task 11), link propagation SysConfig key (Task 10), class permission preference key (Task 8), read-only action list and customer navigation setting names (Task 27). Each one is pinned by a test before the implementation, and Task 28 proves them against the real peers.
