# Tiqora documentation index

**Product site & live demo:** [cygnusnetworks.github.io/tiqora](https://cygnusnetworks.github.io/tiqora/)
([interactive demo](https://cygnusnetworks.github.io/tiqora/demo/)).

## Getting started — three ways to run

| Path | When | Doc |
|---|---|---|
| **Fresh standalone** | Empty database, no Znuny — greenfield install via `tiqora bootstrap` | [guide/fresh-install.md](guide/fresh-install.md) |
| **Parallel to Znuny** | Co-run with an existing OTRS/Znuny **6.0–7.3** database (additive `tiqora_*` only) | [support-matrix.md](support-matrix.md), [parallel-operation.md](parallel-operation.md), [guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md) |
| **Migrate away** | After parallel operation: schema ownership, cutover checklist | [cutover.md](cutover.md) |

## Getting started

| Document | Content |
|---|---|
| [guide/fresh-install.md](guide/fresh-install.md) | Greenfield install: empty DB → `tiqora bootstrap` → login → optional building blocks (ITSM mode, reports, identity providers, web chat, plugins) |
| [architecture.md](architecture.md) | System components and data flow |
| [development.md](development.md) | Local development workflow + release checklist |
| [testing.md](testing.md) | Test suite layout, golden-master tests, testcontainers |
| [deployment.md](deployment.md) | Production-oriented deployment notes |
| [releases/v0.19.0.md](releases/v0.19.0.md) | Release notes of v0.19.0 (wave 4); upgrade steps in [deployment.md](deployment.md#upgrading-to-v0190-wave-4). There is no notes file for v0.18.0 (waves 0-3): its content is described by the feature-area documents below, and its tag message lists the areas |
| [deploy/docker-compose.md](deploy/docker-compose.md) | Full Docker Compose walkthrough: services, env vars, external DB, reverse proxy, TLS, MCP streaming |

## API reference

| Document | Content |
|---|---|
| [api/README.md](api/README.md) | Overview of the API surfaces, auth, pagination/error conventions |
| [api/rest-v1.md](api/rest-v1.md) | Guided `/api/v1` reference with curl examples |
| [api/openapi.json](api/openapi.json) | Generated OpenAPI schema (exhaustive; also served live at `GET /openapi.json`) |
| [api/compat.md](api/compat.md) | `/znuny-compat` GenericInterface emulation — quick pointer into [compatibility.md](compatibility.md) |
| [api/mcp.md](api/mcp.md) | MCP server: transport, auth, tool list, prompt-injection warning |
| [ai-integration.md](ai-integration.md) | Webhook schema, MCP interface, external agent patterns, and the **built-in AI subsystem** (§5: providers, model catalog/profiles/task routing, cost/tool-round budgets, per-queue policies, drafts, summaries, triage, refine, auto-reply, human handoff, per-ticket pause, origin traces, PII masking, audit) |

## Znuny parallel operation and migration

| Document | Content |
|---|---|
| [guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md) | Operator playbook: backup → read-only deploy → TiqoraSync + writes → daemon takeover → reports, saved searches and ITSM import → cutover |
| [support-matrix.md](support-matrix.md) | **Supported peers** OTRS/Znuny 6.0–7.3 × MariaDB/PostgreSQL; add-ons TiqoraSync 1.2.0 and TiqoraITSM 1.0.0; Znuny ITSM versions for Mode A and the importer; validation layers |
| [parallel-operation.md](parallel-operation.md) | Behavioural invariants Tiqora maintains while co-running with Znuny; per-function daemon-takeover procedures |
| [cutover.md](cutover.md) | Detailed, checklist-driven cutover runbook with rollback per stage; inventory of responsibilities incl. GenericInterface invokers, processes, ITSM/CMDB and reports |
| [compatibility.md](compatibility.md) | GenericInterface compatibility layer: scope, gotchas, golden-master validation |

## Feature areas

| Document | Content |
|---|---|
| [i18n.md](i18n.md) | UI languages: **49** locales vs Znuny’s **48** `.po` files, RTL, tooling |
| [channels.md](channels.md) | SMS, WhatsApp Business, Telegram (chat composer), and Phone/CTI (phone tickets, call logging, incoming-call popup) channel plugins |
| [crypto.md](crypto.md) | PGP and S/MIME: key stores shared with Znuny, settings with Znuny precedence, key admin, verify/decrypt inbound, sign/encrypt outbound with per-queue defaults, customer keys, signed/encrypted notifications (off by default) |
| [gdpr.md](gdpr.md) | Customer anonymization and retention-policy tooling, ownership write-gate |
| [dashboards.md](dashboards.md) | Configurable agent dashboards: widgets, layout, sharing, admin role templates (incl. mandatory), permissions, keyboard/phone use, adding widgets and providers (German) |
| [guide/webservices.md](guide/webservices.md) | Admin → Webservices: outgoing GenericInterface calls, protocol (debugger), dry run, resend, settings and the takeover from Znuny (German) |
| [process-management.md](process-management.md) | BPM ticket processes: reused `pm_*` tables, engine flow, process designer, deploy (also through TiqoraSync while Znuny runs), agent + portal CustomerInterface, REST API, supported/deferred scope |
| [itsm.md](itsm.md) | ITSM: modes (off / A = Znuny leads, read-only / B = Tiqora leads), incident, problem and major incident, service and priority matrix, link types and link graph, coexistence with Znuny ITSM, switching with the importer, the `TiqoraITSM` Znuny add-on (**German**, English summary) |
| [cmdb.md](cmdb.md) | Tiqora CMDB (ITSM Mode B): classes and designer, CIs and versions, search, links, impact analysis, CSV/XLSX import and export, bulk edit, customer portal view "Meine Geräte und Systeme", MCP tools `cmdb_*` (**German**, English summary) |
| [itsm-import.md](itsm-import.md) | Znuny ITSM schema survey (6.5 and 7.3), Mode A read rules, importer mapping and report, golden validation (**German**, English summary) |
| [reporting.md](reporting.md) | Reports: built-in catalogue, report builder, saved reports and sharing, Znuny statistics import, PDF and schedules, report data and rights (English) |
| [plugins.md](plugins.md) | Plugin system: install and operate (Part 1, **German**), developer guide for SDK 0.2.0, signed packages and repositories (Part 2, English), limits (Part 3, **German**) |
| [guide/identity-providers.md](guide/identity-providers.md) | Sign-in with OIDC, SAML 2.0 and LDAP, multi-IdP, JIT provisioning, role sync, customer SSO, SCIM 2.0, step-up MFA, session lifetime (**German**, English summary) |
| [guide/webchat.md](guide/webchat.md) | Web chat for administrators and agents: set-up, embedding, AI first line, recognised visitors, portal chat, data protection (**German**) |
| [webchat.md](webchat.md) | Web chat internals: assistant loop, claims and handoff, identity paths (English) |

## Design and specs

| Document | Content |
|---|---|
| [specs/2026-07-19-tiqora-design.md](specs/2026-07-19-tiqora-design.md) | Historical design specification (phases reflect original delivery plan) |

---

Documentation conventions: all docs are vendor-neutral (generic placeholders
like `tickets.example.com`, `db.example.internal` — no real deployment
hostnames, IPs, or credentials). Screenshots come from the demo mode only.
The published container images (`ghcr.io/cygnusnetworks/tiqora`,
`docker.io/cygnusnetworks/tiqora`) and the GitHub repository
(`CygnusNetworks/tiqora`) are the actual public artifacts and are fine to
reference directly.

**Language.** English is the default for new documents and for reference and
developer material. Some user and admin guides are written in **German**
because the UI default language is German and the guard tests pin the German
headings: they are marked "(German)" in the tables above, and each of them
starts with an English summary block, except [guide/webchat.md](guide/webchat.md)
and [guide/webservices.md](guide/webservices.md), which are German only.
[plugins.md](plugins.md) is mixed (Parts 1 and 3 German, Part 2 English). When
a guide describes the UI it quotes the exact German label and adds an English
gloss. Existing German guides are extended in German with an English summary
rather than translated wholesale.
