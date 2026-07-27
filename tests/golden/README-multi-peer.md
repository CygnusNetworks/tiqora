# Multi-peer golden (Layer B) — manual toolkit

**Manual only.** No nightly, no PR gate. Full matrix via local
`just golden-all-peers` or GitHub Actions `workflow_dispatch` with
`run_all=true`.

## Layers

| Layer | What | Proves |
|-------|------|--------|
| **A** | Real DDL fixtures + Tiqora only (`SCHEMA_MATRIX=1`) | Detect, migrate, R/W on schema |
| **B** | Real peer app container + Tiqora on same DB | Behavioural parity (TN, history, escalation, …) |

Layer A covers all profile fixtures under
`backend/tests/fixtures/legacy-schema/`. Layer B runs a **real** OTRS/Znuny
container from a release tree listed in `peers.yaml`.

## Peers

See `peers.yaml` for ids (`otrs-znuny-6.0` … `znuny-7.3`). Default:
`znuny-6.5` → source dir `znuny-6.5.22/` at the repo root (gitignored).

```sh
just golden-peers          # all ids
just golden-peers-ready    # ids with source present
GOLDEN_PEER=znuny-6.5 just golden-peer-env
```

## Source trees

Place a **real directory** (not an external symlink — Docker `COPY` rejects
those) at the path given by `source_dir` in `peers.yaml`:

```text
znuny-6.5.22/          # primary peer
znuny-6.0.45/          # optional further peers
…
```

Each tree needs `scripts/database/schema.mysql.sql` or
`scripts/database/otrs-schema.mysql.sql` (pre-6.4 naming).

## Local recipes

```sh
# Default peer (znuny-6.5)
just golden-up
just golden-seed
GOLDEN=1 just golden-test   # or: just golden-test  (exports GOLDEN=1)
just golden-down            # keep volume
just golden-clean           # drop volume

# One-shot
GOLDEN_PEER=znuny-6.5 just golden-run

# Other peer (source must exist)
GOLDEN_PEER=znuny-7.0 just golden-run

# Every peer with source on disk (sequential, tear-down between peers)
just golden-all-peers
```

Compose project name is `tiqora-golden-<peer>` with dots replaced by
hyphens (e.g. `tiqora-golden-znuny-6-5`) so volumes/images stay isolated
per peer. Host ports stay `3307` (MariaDB) and `8180` (HTTP) — only one
peer stack at a time.

Low-level:

```sh
eval "$(python3 tests/golden/peer_env.py znuny-6.5)"
docker compose -p "$GOLDEN_COMPOSE_PROJECT" \
  -f tests/golden/docker-compose.golden.yml up -d --build --wait
```

## Env

| Variable | Purpose |
|----------|---------|
| `GOLDEN=1` | Un-skip golden pytest modules |
| `GOLDEN_PEER` | Peer id from `peers.yaml` (default `znuny-6.5`) |
| `GOLDEN_DB_URL` | Shared MariaDB URL (sync) |
| `GOLDEN_DB_ASYNC_URL` | Shared MariaDB URL (async / Alembic) |
| `GOLDEN_COMPOSE_PROJECT` | Set by `peer_env.py` |
| `GOLDEN_SOURCE_DIR` | Build-arg path relative to repo root |

## CI (manual)

`.github/workflows/golden.yml` — **workflow_dispatch only**:

| Input | Meaning |
|-------|---------|
| `peer` | Single peer id (default `znuny-6.5`) |
| `run_all` | Sequential run of all peers with source present |

Runner must have the release tree(s); otherwise the job fails the source
check. Local `just golden-all-peers` is the usual full-matrix path.

## Adding / validating a peer

1. Add entry in `peers.yaml` (`source_dir`, `schema_profile`, …).
2. Extract release tree at `source_dir` (real dir, not external symlink).
3. `GOLDEN_PEER=<id> just golden-run` and fix any intentional behavioural
   deltas only with documented pins.
4. TiqoraSync Framework tags already cover 6.0–7.3
   (`packages/znuny-addon/TiqoraSync/`).

## Implementation notes

- One Dockerfile (`Dockerfile.znuny`) with `SOURCE_DIR` build-arg; install
  home inside the image is always `/opt/otrs`.
- Entrypoint auto-detects `schema.mysql.sql` vs `otrs-schema.mysql.sql` and
  Console (`znuny.Console.pl` / `otrs.Console.pl`).
- `_helpers.py` / `seed.sh` honour `GOLDEN_COMPOSE_PROJECT` so multi-peer
  stacks do not cross-talk.
