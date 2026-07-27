# Testing: multi-version schema matrix and golden-master

Tiqora reimplements large parts of Znuny ticket-write behaviour
(history formats, ticket numbering, escalation math, the GenericInterface
compat surface). Tests are layered by cost and what they prove.

## Layer A — multi-version schema matrix (real DDL, no peer app)

**Goal:** prove Tiqora **detects, migrates, and runs** against real OTRS/Znuny
fresh-install schemas (MariaDB **and** PostgreSQL).

| Piece | Location |
|-------|----------|
| Fixtures | `backend/tests/fixtures/legacy-schema/<profile_id>/` |
| Tests | `backend/tests/test_legacy_schema_matrix.py` (`-m schema_matrix`) |
| CI | `.github/workflows/schema-matrix.yml` (release tags, nightly, `workflow_dispatch`) |

**Release anchors** (default matrix): `otrs-znuny-6.0`, `znuny-6.3`,
`znuny-6.5`, `znuny-7.0`, `znuny-7.3` × `{mysql, postgresql}`.

**Full set** (`SCHEMA_MATRIX_FULL=1`, nightly CI): also 6.1, 6.2, 6.4, 7.1, 7.2.

Tests included under `-m schema_matrix`:

- `test_legacy_schema_matrix.py` — detect + migrate + ticket write
- `test_schema_conformance_profile.py` — profile-aware column/table checks

```sh
cd backend
SCHEMA_MATRIX=1 uv run pytest -q -m schema_matrix              # release anchors
SCHEMA_MATRIX=1 SCHEMA_MATRIX_FULL=1 uv run pytest -q -m schema_matrix  # all fixtures
```

Day-to-day `pytest -q` / PR CI still use the single **Znuny 6.5** bootstrap
schema under `tiqora.bootstrap.schema` for hundreds of `db` tests (fast
default). Multi-version DDL coverage is opt-in via `-m schema_matrix`
([support-matrix.md](support-matrix.md)).

Profile detection IDs: see `docs/parallel-operation.md` and
`tiqora.db.legacy.profile.SchemaProfileId`.

TiqoraSync multi-framework install: `packages/znuny-addon/TiqoraSync/install/README.md`.

Multi-peer golden toolkit: `tests/golden/peers.yaml` and
`tests/golden/README-multi-peer.md`.

## Layer B — golden-master (real peer container vs Tiqora)

The **golden-master suite** in `tests/golden/` goes one step further and runs
a **real** OTRS/Znuny peer container against the SAME MariaDB database Tiqora
uses, then diffs the resulting rows/JSON directly.

This is heavy (Apache+mod_perl+peer source tree) and **manual / opt-in** —
it does not run as part of `just test`, PR CI, or any nightly schedule.

Default peer is **Znuny 6.5.22** (`GOLDEN_PEER=znuny-6.5`). Further peers
(6.0–7.3) are selected via `GOLDEN_PEER` once the matching release tree is
present under the repo root (see `peers.yaml`).

## What gets validated

| Area | Test file |
|---|---|
| Ticket number uniqueness under interleaved Znuny/Tiqora writers | `test_ticket_number_interleaving.py` |
| `ticket_history` row name-format parity | `test_history_diff.py` |
| Escalation column math + zero-on-close | `test_escalation.py` |
| GenericInterface compat conformance (SessionCreate/TicketSearch/StateType/empty-search) | `test_compat_conformance.py` |
| `DateChecksum` ticket-number checksum digit | `test_date_checksum.py` |

## Infrastructure

`tests/golden/Dockerfile.znuny` builds a peer image from a release tree
(`SOURCE_DIR` build-arg, default `znuny-6.5.22/`) — **no official
`znuny/znuny` image exists on Docker Hub**. Layout inside the image is always
`/opt/otrs`; Console is auto-detected (`znuny.Console.pl` /
`otrs.Console.pl`). `tests/golden/znuny-entrypoint.sh` renders
`Kernel/Config.pm` from env vars, waits for MariaDB, loads schema on first
boot (`schema` / `otrs-schema` + `initial_insert` + `schema-post` — see
docs/parallel-operation.md), fixes permissions (incl. `var/tmp` for
FileStorable), and starts Apache in the foreground.

`tests/golden/docker-compose.golden.yml` starts MariaDB 10.11 (host port
3307) and the peer container (host port 8180). Compose project name is
`tiqora-golden-<peer>` (dots → hyphens, e.g. `tiqora-golden-znuny-6-5`)
so multi-peer stacks stay isolated (one peer at a time on those host
ports).

`tests/golden/peer_env.py` resolves `GOLDEN_PEER` → source path, compose
project, DB URLs.

## Running locally

```sh
just golden-up              # build + start MariaDB + default peer (znuny-6.5)
just golden-seed            # seed admin agent, queue, customer user
just golden-test            # GOLDEN=1 pytest -m golden
just golden-down            # stop (keeps the DB volume)
just golden-clean           # stop and drop the DB volume

GOLDEN_PEER=znuny-6.5 just golden-run   # up + seed + test + clean
just golden-all-peers                   # sequential matrix for every peer with source on disk
just golden-peers-ready                 # which peers have a release tree present
```

Place release trees as real directories at the paths in `peers.yaml` (Docker
`COPY` rejects external symlinks). Trees are gitignored (`/znuny-*/`).

Tiqora itself is **not started** by `golden-up` — point your local Tiqora
`DATABASE_URL` at the same MariaDB
(`mysql+aiomysql://znuny:znuny@127.0.0.1:3307/znuny`) if you want to drive
Tiqora through its own HTTP API instead of calling `tiqora.domain.*` /
`tiqora.api.compat.operations.*` functions directly (which is what the
golden tests do, to keep the harness simple and avoid a second running
process).

The suite is skipped by default; set `GOLDEN=1` to un-skip
(`tests/golden/conftest.py`), matching the `db`/`search` marker pattern used
by `backend/tests/conftest.py` for testcontainers-based tests.
`just golden-test` sets `GOLDEN=1` for you.

## CI

`.github/workflows/golden.yml` is **workflow_dispatch only** (no push, no
nightly):

| Input | Default | Meaning |
|-------|---------|---------|
| `peer` | `znuny-6.5` | Single peer id from `peers.yaml` |
| `run_all` | `false` | Sequential run of every peer with source present |

The runner must already have the release tree(s); otherwise the job fails
the source check. The usual full multi-peer path is local
`just golden-all-peers`.

## Extending the suite

Each test module drives Znuny either via `otrs.Console.pl` sub-commands
(`_helpers.znuny_console`) or an inline Perl one-liner using
`Kernel::System::ObjectManager` (`_helpers.znuny_perl_eval`), and drives
Tiqora via its real domain/compat functions (`tiqora.domain.ticket_write_service`,
`tiqora.api.compat.operations`) against `golden_session_factory` — never a
re-implementation of the assertion logic, so a passing test is evidence the
*actual* production code paths agree with Znuny, not that two independent
descriptions of Znuny's behaviour agree with each other.

When a divergence is found: fix the Tiqora side (`backend/src/tiqora/znuny/*`
or `backend/src/tiqora/domain/ticket_write_service.py`) unless it is one of
the deviations explicitly documented in `docs/compatibility.md` /
`docs/parallel-operation.md` "uncertainties" sections, in which case the test
should assert the documented behaviour instead of Znuny parity.
