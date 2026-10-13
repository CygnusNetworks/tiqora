# Plugin system — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Status: proposed 2026-10-08. Phase 1 implemented 2026-10-10 — see plan
> [2026-10-10-wave2-plugin-system](../plans/2026-10-10-wave2-plugin-system.md) and
> [plugins.md](../../plugins.md). Phase 2a implemented 2026-10-13 — see plan
> [2026-10-12-wave4-plugins-extension-points](../plans/2026-10-12-wave4-plugins-extension-points.md). Decisions below are fixed unless marked "open".

## Goal

Everything a Znuny OPM package can do, a Tiqora plugin can do — through
**stable, documented extension points** instead of overriding core files, so
plugins survive Tiqora upgrades. On top: frontend extensions that look
native, sandboxed "apps" for third parties, signed packages, a repository
with updates, and a developer kit that makes a first plugin a one-hour job.

**This reverses a documented non-goal** ("Znuny package manager / OPM
marketplace", `docs/architecture.md` → Non-goals). It does not bring back
OPM: Perl packages still cannot run. The plugin system is Tiqora-native.

## What OPM can do (the bar)

From Znuny's package format and module registries:

- Lifecycle: install/upgrade/uninstall/reinstall from file or online
  repositories, framework-version and package dependencies, `CodeInstall/
  CodeUpgrade/CodeUninstall` hooks, `DatabaseInstall/Upgrade/Uninstall`
  (DDL), file deployment with integrity check ("modified files"), package
  signature verification (historically), console commands for all of it.
- Configuration: new SysConfig settings and overrides of core settings.
- Extension registries: frontend modules (new screens, nav entries), ticket
  menu and article actions, dashboard backends, output filters, template
  overrides, JS/CSS loader, preferences modules, event handlers
  (`Ticket::EventModulePost` & co.), postmaster filter modules, auth and
  customer-auth modules, customer user/company backends, dynamic field
  drivers, GenericInterface operations/invokers/mappings/transports, process
  transition actions and validation modules, ACL modules, notification
  transports, stats modules, ticket number generators, daemon tasks/cron jobs,
  console commands, translations.
- And — the source of most upgrade pain — **overriding any core file**.

## Current state (2026-10-08)

- No plugin mechanism. Extensibility today: `/api/v1` (OpenAPI, typed client
  `packages/api-client`), HMAC-signed webhooks (`tiqora_webhook`), MCP server
  (`tiqora-mcp`) and calling external MCP tools from the AI.
- Internal seams that already look like extension points: channel drivers
  (`channels/*`), auth backends (`domain/auth*`), worker loops registered in
  `worker/services.py`, AI tools (`ai/tools.py`), feature grants
  (`tiqora_feature_grant`), event outbox + SSE event types.
- Deployment: one image, four entrypoints (`api`, `worker`, `ai-worker`,
  `mcp`), `restart: unless-stopped` in the compose examples.

## Fixed decisions

### 1. Two tiers

| | **Plugins** (in-process) | **Apps** (out-of-process) |
|---|---|---|
| For | Site operators, partners, Tiqora itself | Third parties, SaaS integrations |
| Runs | Inside api/worker/mcp processes (Python) + browser (ES modules) | Anywhere; talks to Tiqora over HTTP |
| Can | Everything in the extension catalogue below | REST API with scoped token, webhooks, MCP, UI extensions in sandboxed iframes |
| Trust | Full trust (like OPM) → admin-only install, signatures | Least privilege, scopes enforced |
| Languages | Python + TypeScript | Any |

The OPM bar is met by tier 1; tier 2 is what Zendesk/Atlassian-style
ecosystems offer and Znuny does not.

### 2. Plugin package

- File `<id>-<version>.tqplugin` (zip):
  - `tiqora-plugin.toml` — id (reverse-DNS, e.g. `com.example.assets`),
    name, version (semver), publisher, `tiqora = ">=1.4,<2"` (core
    compatibility), plugin dependencies, permissions/capabilities it declares
    (tables, routes, outbound hosts, feature keys, extension points used),
    settings schema reference, entry points.
  - `python/` — one wheel for the plugin. **Runtime install** accepts only
    pure-Python plugins whose dependencies are already in the core image
    (checked against the core lockfile at install). Anything with additional
    or native dependencies goes through the baked-image path.
  - `web/` — built ES modules + `manifest.json` of UI contributions.
  - `i18n/` — locale JSON (same key format as core; missing locales fall
    back to English).
  - `migrations/` — Alembic scripts on the plugin's own branch.
  - `SIGNATURE` — ed25519 signature over the package hash.
- Python side: entry point group `tiqora.plugins`, the plugin object exposes
  `register(registry)` and receives a typed `PluginContext` (settings,
  logger, DB session factory, domain services, HTTP client with SSRF guard,
  scheduler, cache, i18n).

### 3. Stable SDK, no core overrides

- `tiqora-sdk` (Python package inside this repo, versioned with semver
  independent of the app) and `@tiqora/plugin-sdk` (npm workspace package)
  are the **only** supported API. Everything else is internal.
- Stability policy: SDK minor releases are additive; removals only in a
  major after two minor releases of deprecation warnings; a compatibility CI
  job builds and tests all first-party and registered example plugins
  against core `main`.
- Core-file overrides are not offered. Where OPM sites override files today,
  the catalogue below provides a *replace* or *filter* point (ticket number
  generator, customer backend, templates, mail filters …). Python can
  technically monkeypatch — unsupported, and the plugin validator warns.
- **Ground rules hold for plugins**: writes to Znuny tables only through SDK
  domain services (`sdk.tickets`, `sdk.articles`, `sdk.customers`, …), which
  keep history/escalation/cache invalidation Znuny-identical; plugin state
  only in its own tables. The validator flags raw SQL against Znuny tables.

### 4. Extension catalogue

Backend (registered in `register(registry)`):

| Extension point | Znuny equivalent |
|---|---|
| Event subscribers — async (from the outbox, at-least-once, both Znuny and Tiqora writes) and sync "before" hooks on selected domain operations (validate/veto/enrich; strict time budget) | `Ticket::EventModulePost`, other event registries |
| HTTP routes under `/api/plugins/{id}/…` with core auth/permission dependencies | Frontend modules (server side) |
| Settings schema (Pydantic) → generated admin form, per-queue/per-group scopes optional | SysConfig XML |
| Worker jobs: interval, daily, cron, job-table handlers | Daemon tasks, cron |
| CLI commands `tiqora plugin <id> <cmd>` | Console commands |
| Mail: inbound filters (pre/post ticket match), outbound pre-send filters | Postmaster filter modules |
| Channels: channel driver (inbound, outbound, composer capabilities) | — (Znuny has none) |
| Auth providers (agent/customer), customer user + company backends | Auth/CustomerAuth modules, CustomerUser backends |
| Dynamic field types (validation, storage mapping onto Znuny value columns, search, frontend widget) | DF drivers |
| Process: transition actions, condition modules | TransitionAction / TransitionValidation |
| GenericInterface: operations, invokers, mappings | GI modules |
| ACL/permission modules (additional option filtering, never widening core permissions) | ACL modules |
| Notification transports (Teams, Slack, Matrix, SMS …) and placeholders | Notification transports, smart tags |
| Ticket number generator, ticket title/subject builders | TicketNumberGenerator |
| Reporting: sources, dimensions, measures, built-in reports, export formats | Stats modules |
| Dashboard widget data providers | Dashboard backends |
| AI: tools for the agent loop, context providers, PII recognisers | — |
| MCP tools (same permission engine) | — |
| Search: index document enrichers | — |
| Webhook event types | — |
| Feature keys (feature grants) and permission keys | Group/permission registrations |

Frontend (`@tiqora/plugin-sdk`, contributions declared in `web/manifest.json`):

| Slot | Examples |
|---|---|
| Routes (agent, admin, portal) + navigation entries | New screens |
| Ticket zoom: sidebar panels, header actions, article actions, composer toolbar buttons, tabs | Ticket menu modules, article actions |
| Ticket list: columns, bulk actions, row badges | — |
| Dashboard widgets | Dashboard backends |
| Customer/company panels, CI panels (ITSM) | Customer info widgets |
| Settings pages, user preference sections | Preferences modules |
| Dynamic field widgets, process dialog field widgets | DF frontend |
| Webchat widget extensions (pre-chat fields, message renderers) | — |

- Loading: plugin bundles are ES modules built against the SDK with `react`,
  `react-dom`, `@tanstack/react-query`, `i18next` as **externals** provided by
  the host (import map), lazy-loaded per slot with `import()`. A plugin
  crash is contained by an error boundary per slot ("Erweiterung X konnte
  nicht geladen werden").
- The SDK exports Tiqora's UI kit (tokens, buttons, inputs, dialogs, tables,
  field engine, chart components) so plugins look native in light/dark/RTL
  without copying styles.

### 5. Database

- Plugin tables are prefixed `tqp_<shortid>_` and live on the plugin's own
  Alembic branch (`branch_labels=("plugin_<id>",)`, base at a fixed core
  revision; upgrades target `heads`) — same pattern as the gated owned chain
  (see skill `alembic-two-chain-new-head-forks-graph`).
- Never Znuny DDL (ground rule 1). Uninstall offers "Daten behalten" (default)
  or "Daten löschen" (drops the branch's tables after a backup export).

### 6. Install and run

- **Baked image (recommended for production):** `tiqora plugins bake
  plugins.txt` produces a Dockerfile `FROM tiqora:<ver>` that installs the
  listed packages (native dependencies allowed) — reproducible, scanner-friendly.
- **Runtime install (convenience):** admin uploads or picks from a repository;
  Tiqora verifies signature + compatibility, installs the wheel with
  `--no-deps --target` (all dependencies are already in the image, see §2) into a volume (`/var/lib/tiqora/plugins/<id>/<ver>`),
  serves `web/` from there, runs migrations as an async job, then asks for a
  coordinated restart of api/worker/ai-worker/mcp (processes exit cleanly
  one after another; compose `restart: unless-stopped` brings them back).
- Enable/disable without uninstall; state in `tiqora_plugin` (`id, version,
  enabled, installed_at, installed_by, source, signature_status, settings
  JSON (secrets encrypted), health JSON`).
- **Safe mode**: `TIQORA_PLUGINS_SAFE_MODE=1` starts without any plugin
  (recovery after a bad plugin).
- **Isolation of failures**: async subscribers and jobs run with timeouts
  and error counters; a circuit breaker disables a failing hook and shows it
  on the Daemons page; sync hooks have a hard time budget and fail open or
  closed as the hook declares.

### 7. Trust and signatures

- Install and configuration require admin (`rw` on `admin`).
- ed25519 signatures; trust levels *offiziell* (Tiqora key), *verifizierter
  Herausgeber* (keys registered in the repository index), *lokal signiert*
  (site's own key, configured by env), *unsigniert* (only with
  `TIQORA_PLUGINS_ALLOW_UNSIGNED=1`).
- The install dialog shows the declared capabilities in plain words ("legt
  2 Tabellen an", "ruft api.example.com auf", "fügt eine Spalte zur
  Ticketliste hinzu"). For tier-1 plugins this is information, not a sandbox
  — said explicitly in the dialog.

### 8. Repository

- Repository index (signed JSON): plugins, versions, compatibility, hashes,
  publisher keys, changelog. Official repository + any number of custom ones
  (like OPM online repositories), configurable in the admin UI or by env for
  air-gapped sites (file:// or internal HTTPS mirror).
- Update notifications on the plugin page and the Daemons/System page.
- **Decision 2026-10-08: start with custom repositories only** — no public
  marketplace run by the project for now. Sites configure their own
  repositories (internal HTTPS, file://); first-party plugins ship in the
  image or from a project repository URL that is just another entry.

### 8a. Marketplace later — what keeps it a pure add-on

A public marketplace must be addable without changing plugins, packages or
installed sites. Therefore, from phase 2 on:

- The repository index format is versioned (`index_version`) and already
  carries everything a marketplace needs: publisher id + keys, categories,
  screenshots/icon URLs, licence, homepage, support contact, per-version
  changelog and compatibility. Unused fields are allowed to be empty.
- Publisher identity and trust levels (§7) are part of the package and the
  index, not of a central service — a marketplace is "a repository whose
  publisher keys are verified by us".
- The admin plugin page lists plugins **per configured repository** with
  search and categories; a marketplace is one more repository URL plus an
  optional "Entdecken" tab on the same page.
- Package ids are reverse-DNS from day one, so no renaming when a central
  catalogue appears.
- Nothing in Tiqora phones home; adding the marketplace is an opt-in setting.
- Marketplace-only concerns (publisher sign-up, review process, web catalogue,
  paid plugins) live outside the Tiqora codebase.

### 9. Apps (tier 2)

- App manifest (JSON): name, publisher, base URL, requested scopes (existing
  API key scopes extended: `tickets:read`, `tickets:write`, `customers:read`,
  …, queue restriction), webhook subscriptions, UI extensions (slot + iframe
  URL), optional MCP endpoint.
- Install creates a dedicated agent-less API identity with scoped token and
  webhook subscriptions; uninstall revokes everything.
- UI extensions render in sandboxed iframes (`sandbox="allow-scripts
  allow-forms"`, own origin) in the same slots as plugins; a small bridge
  (`@tiqora/app-bridge`, postMessage with origin checks) gives context
  (ticket id, user locale/theme) and a few actions (open ticket, insert text
  into composer, show toast, resize). Theme tokens are passed so apps can
  match the design.

### 10. Developer experience

- `tiqora plugin new <id>` scaffold (backend + frontend + tests + CI
  workflow), `tiqora plugin dev` (loads a plugin from a path, hot reload for
  Python and Vite HMR for the frontend against a running dev stack),
  `tiqora plugin build|sign|validate`.
- Test kit: pytest fixtures (registry, DB with core schema, fake domain
  services, outbox driver), Vitest helpers (slot rendering with SDK mocks).
- Documentation site: one page per extension point with a runnable example;
  example plugins: "Slack-Benachrichtigung", "Zusatzspalte Vertragsstatus",
  "Dashboard-Widget Wetter/Status", "Eigener Kanal (Matrix)",
  "Ticketnummer-Schema".
- **Dogfooding:** at least two existing features move to first-party plugins
  (candidates: SMS gateway, WhatsApp, CTI, netadmin customer link) to prove
  the extension points before the SDK is declared stable (1.0).

### 11. Migration help from Znuny

- `Admin → Plugins → Znuny-Pakete`: reads Znuny's `package_repository`
  table, lists installed OPM packages, maps known ones to Tiqora features or
  plugins (ITSM* → ITSM, FAQ → KB, TimeAccounting, Survey → CSAT, …), and
  marks unknown ones with a checklist of what they register (parsed from the
  package's SOPM in the table: SysConfig, frontend modules, event modules) —
  the input for writing a replacement plugin.

## Tests

- Loader: incompatible core range, missing dependency, signature failure,
  safe mode, failing `register()` → plugin disabled, core keeps running.
- Each extension point: contract test with a fixture plugin in the repo
  (`backend/tests/plugins/fixture_*`), also run against MariaDB and PostgreSQL.
- Frontend: slot rendering, error boundary, externals resolution.
- Apps: scope enforcement on every API route class, iframe bridge origin
  checks, revoke on uninstall.

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 | Registry + `tiqora-sdk`/`@tiqora/plugin-sdk` skeleton, manifest, entry-point loading, settings schema → admin form, async event subscribers, routes, worker jobs, MCP tools, frontend loader + slots (nav/route, ticket sidebar, dashboard widget), plugin admin page, safe mode, baked image | 3–4 weeks |
| 2 | Remaining backend points (channels, auth, customer backends, DF types, process, GI, reporting, notification transports, mail filters, placeholders, number generator), sync hooks, plugin migrations, runtime install, signatures, repository + updates | 4–5 weeks |
| 3 | Apps tier (manifest, scoped identities, iframe slots + bridge), dev kit (scaffold, dev mode, test kit), docs site + examples, Znuny package assistant | 3–4 weeks |
| 4 | Dogfooding (move 2+ features to first-party plugins), SDK 1.0 | 2–3 weeks |

## Risks / open

- In-process plugins can degrade performance or security — mitigated by
  signatures, admin-only install, health/circuit breaker; not eliminated.
  Untrusted code belongs in the apps tier.
- Shared frontend externals pin React/TanStack major versions for plugins;
  a core React major upgrade becomes an SDK major.
- Runtime install writes to a volume; sites with a read-only root keep using
  the baked-image path only (documented).
- Marketplace: decided — custom repositories only for now; §8a keeps the
  marketplace a later add-on.
