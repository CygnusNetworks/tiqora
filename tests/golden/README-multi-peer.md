# Multi-peer golden (Layer B) — status and how to extend

## Layers

| Layer | What | Proves |
|-------|------|--------|
| **A** | Real DDL fixtures + Tiqora only (`SCHEMA_MATRIX=1`) | Detect, migrate, R/W on schema |
| **B** | Real peer app container + Tiqora on same DB | Behavioural parity (TN, history, escalation, …) |

Layer A is implemented for all profile fixtures under
`backend/tests/fixtures/legacy-schema/`. Layer B remains **Znuny 6.5.22-ready**;
additional peers are **scaffolded** in `peers.yaml` until a peer image build
is wired and validated.

## Default (ready)

```sh
just golden-up
GOLDEN=1 just golden-test
```

Uses `tests/golden/Dockerfile.znuny` + `docker-compose.golden.yml` and the
vendored `znuny-6.5.22/` tree.

## Adding a peer (checklist)

1. Add/adjust entry in `peers.yaml` (`status: ready` only after green run).
2. Provide source tree at `source_dir` (or image) — same installer order:
   `schema` → `initial_insert` → `schema-post`.
3. Parameterise Dockerfile build-args if install home differs (`/opt/otrs` vs
   `/opt/znuny`) and Console name (`otrs.Console.pl` vs `znuny.Console.pl`).
4. Install TiqoraSync (Framework tags cover 6.0–7.3) and seed via peer console.
5. Run existing golden modules with `GOLDEN=1` and `GOLDEN_PEER=<id>`; fix
   intentional behavioural deltas in test expectations only with documented
   pins.
6. Optionally add a `workflow_dispatch` matrix row in `.github/workflows/golden.yml`.

## Env hooks (reserved)

| Variable | Purpose |
|----------|---------|
| `GOLDEN=1` | Un-skip golden tests |
| `GOLDEN_PEER` | Peer id from `peers.yaml` (default `znuny-6.5`) |
| `GOLDEN_DB_URL` | Shared MariaDB URL |

Peer selection is documented first; compose profiles per peer land as each
peer moves from `scaffold` → `ready`.
