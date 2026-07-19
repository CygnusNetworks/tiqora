# Deployment

> Tiqora is **not production ready**. Treat this document as a draft of the
> intended deployment shape, not an operational runbook.

## Images

Multi-stage `Dockerfile` produces a single runtime image with the API code and
built frontend static assets. Process role is selected at start:

| Command / env | Process |
|---|---|
| `api` (default) | FastAPI (uvicorn) |
| `worker` | taskiq worker |
| `mcp` | FastMCP server |

Registry targets (CI):

- `ghcr.io/cygnusnetworks/tiqora`
- `docker.io/cygnusnetworks/tiqora`

Tags: `latest` (main), semver on release tags, `sha-<shortsha>`.

## Compose example

See [docker-compose.example.yml](../docker-compose.example.yml) for a commented
deployment skeleton with:

- `tiqora-api`, `tiqora-worker`, `tiqora-mcp`
- Choice of **PostgreSQL or MariaDB** (comments show both; enable one)
- Redis, Meilisearch
- Optional Mailpit for non-production mail sinks

Copy to `docker-compose.yml`, set secrets via environment or a secrets manager,
and never commit real credentials.

## Required configuration

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@db:5432/tiqora` | Or `mysql+aiomysql://…` |
| `REDIS_URL` | `redis://redis:6379/0` | Sessions + taskiq |
| `MEILI_URL` | `http://meilisearch:7700` | |
| `MEILI_MASTER_KEY` | strong secret | Production required |
| `TIQORA_SECRET_KEY` | random 32+ bytes | Cookie/session material |
| `TIQORA_LOG_LEVEL` | `INFO` | |
| `TIQORA_CORS_ORIGINS` | `https://helpdesk.example.com` | Comma-separated |

Full settings live in `tiqora.config.Settings` (pydantic-settings).

## Parallel operation deployment

When co-running with Znuny:

1. Point Tiqora at the **same** database Znuny uses (read/write credentials).
2. Do **not** run `versions_owned` migrations.
3. Install the `TiqoraSync` OPM on Znuny (or lower cache TTLs).
4. Keep Znuny daemon jobs enabled until Phase 4 feature flags say otherwise.
5. Terminate TLS at a reverse proxy; route `/api`, `/mcp`, and static UI paths
   to Tiqora; leave Znuny paths as needed during migration.

## Reverse proxy sketch

```
https://helpdesk.example.com/          → tiqora-api (static + SPA)
https://helpdesk.example.com/api/      → tiqora-api
https://helpdesk.example.com/mcp       → tiqora-mcp
https://znuny.example.com/             → existing Znuny (until cutover)
```

WebSocket/SSE: ensure proxy timeouts allow long-lived MCP SSE and UI event
streams (no aggressive idle kills).

## Observability

- Scrape `GET /metrics` with Prometheus or Zabbix HTTP agent.
- A Zabbix template will live under `deploy/zabbix/` (placeholder for now).
- Ship stdout JSON logs (structlog) to your log stack.

## Backups

- Database: same RPO/RTO as Znuny today (shared DB during parallel operation).
- Meilisearch: rebuildable from DB (prefer rebuild over fragile index backups
  until documented otherwise).
- Redis: sessions are disposable; job queues should use durable Redis config
  once workers are critical path.

## Security checklist (draft)

- [ ] TLS everywhere
- [ ] Strong `MEILI_MASTER_KEY` and DB passwords
- [ ] Network isolation: DB not public
- [ ] MCP API keys rotated and scoped
- [ ] AGPL obligations understood for network-facing deployments
