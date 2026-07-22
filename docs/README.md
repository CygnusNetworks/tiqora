# Tiqora documentation index

**Product site & live demo:** [cygnusnetworks.github.io/tiqora](https://cygnusnetworks.github.io/tiqora/)
([interactive demo](https://cygnusnetworks.github.io/tiqora/demo/)).

> Still under active development. Production use is not yet recommended.

## Getting started — three ways to run

| Path | When | Doc |
|---|---|---|
| **Fresh standalone** | Empty database, no Znuny — greenfield install via `tiqora bootstrap` | [guide/fresh-install.md](guide/fresh-install.md) |
| **Parallel to Znuny** | Co-run with an existing Znuny 6.5 database (same schema, additive `tiqora_*` only) | [parallel-operation.md](parallel-operation.md), [guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md) |
| **Migrate away** | After parallel operation: schema ownership, cutover checklist | [cutover.md](cutover.md) |

## Getting started

| Document | Content |
|---|---|
| [guide/fresh-install.md](guide/fresh-install.md) | Greenfield install: empty DB → `tiqora bootstrap` → login |
| [architecture.md](architecture.md) | System components and data flow |
| [development.md](development.md) | Local development workflow + release checklist |
| [testing.md](testing.md) | Test suite layout, golden-master tests, testcontainers |
| [deployment.md](deployment.md) | Production-oriented deployment notes |
| [deploy/docker-compose.md](deploy/docker-compose.md) | Full Docker Compose walkthrough: services, env vars, external DB, reverse proxy, TLS, MCP streaming |

## API reference

| Document | Content |
|---|---|
| [api/README.md](api/README.md) | Overview of all four API surfaces, auth, pagination/error conventions |
| [api/rest-v1.md](api/rest-v1.md) | Guided `/api/v1` reference with curl examples |
| [api/openapi.json](api/openapi.json) | Generated OpenAPI schema (exhaustive; also served live at `GET /openapi.json`) |
| [api/compat.md](api/compat.md) | `/znuny-compat` GenericInterface emulation — quick pointer into [compatibility.md](compatibility.md) |
| [api/mcp.md](api/mcp.md) | MCP server: transport, auth, tool list, prompt-injection warning |
| [ai-integration.md](ai-integration.md) | Webhook payload schema, MCP as the primary AI interface, recommended agent patterns |

## Znuny parallel operation and migration

| Document | Content |
|---|---|
| [guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md) | Operator playbook: backup → read-only deploy → TiqoraSync + writes → daemon takeover → cutover |
| [parallel-operation.md](parallel-operation.md) | Behavioural invariants Tiqora maintains while co-running with Znuny; per-function daemon-takeover procedures |
| [cutover.md](cutover.md) | Detailed, checklist-driven cutover runbook with rollback per stage |
| [compatibility.md](compatibility.md) | GenericInterface compatibility layer: scope, gotchas, golden-master validation |

## Feature areas

| Document | Content |
|---|---|
| [channels.md](channels.md) | SMS, WhatsApp Business, and Phone/CTI channel plugins |
| [gdpr.md](gdpr.md) | Customer anonymization and retention-policy tooling, ownership write-gate |
| [process-management.md](process-management.md) | BPM ticket processes: reused `pm_*` tables, engine flow, REST API, supported/deferred scope |

## Design and specs

| Document | Content |
|---|---|
| [specs/2026-07-19-tiqora-design.md](specs/2026-07-19-tiqora-design.md) | Historical design specification (phases reflect original delivery plan) |

---

Documentation conventions: all docs are vendor-neutral (generic placeholders
like `tickets.example.com`, `db.example.internal` — no real deployment
hostnames, IPs, or credentials) and written in English. The published
container images (`ghcr.io/cygnusnetworks/tiqora`,
`docker.io/cygnusnetworks/tiqora`) and the GitHub repository
(`CygnusNetworks/tiqora`) are the actual public artifacts and are fine to
reference directly.
