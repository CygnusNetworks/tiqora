# Golden-master testing (real Znuny vs Tiqora)

Tiqora reimplements large parts of Znuny 6.5's ticket-write behaviour
(history formats, ticket numbering, escalation math, the GenericInterface
compat surface). Unit/DB-integration tests cover the ported logic in
isolation; the **golden-master suite** in `tests/golden/` goes one step
further and runs a REAL Znuny 6.5.22 container against the SAME MariaDB
database Tiqora uses, then diffs the resulting rows/JSON directly.

This is heavy (a real Apache+mod_perl+Znuny container) and **opt-in** — it
does not run as part of `just test` / the normal CI pipeline.

## What gets validated

| Area | Test file |
|---|---|
| Ticket number uniqueness under interleaved Znuny/Tiqora writers | `test_ticket_number_interleaving.py` |
| `ticket_history` row name-format parity | `test_history_diff.py` |
| Escalation column math + zero-on-close | `test_escalation.py` |
| GenericInterface compat conformance (SessionCreate/TicketSearch/StateType/empty-search) | `test_compat_conformance.py` |
| `DateChecksum` ticket-number checksum digit | `test_date_checksum.py` |

## Infrastructure

`tests/golden/Dockerfile.znuny` builds a Znuny 6.5.22 image from the
vendored source tree (`znuny-6.5.22/` at the repo root) — **no official
`znuny/znuny` image exists on Docker Hub**, so this is a from-source build:
Debian bookworm-slim + apache2 + mod_perl + the exact Perl module package
set `bin/otrs.CheckModules.pl --package-list` reports for a MySQL-backed
install. `tests/golden/znuny-entrypoint.sh` renders `Kernel/Config.pm` from
env vars, waits for MariaDB, loads the schema in the known-good installer
order on first boot (`schema.mysql.sql` → `initial_insert.mysql.sql` →
`schema-post.mysql.sql` — see docs/parallel-operation.md "Foreign keys and
orphans"), fixes permissions, and starts Apache in the foreground.

`tests/golden/docker-compose.golden.yml` starts MariaDB 10.11 (port 3307 on
the host, to not collide with `docker-compose.dev.yml`'s 3306) and the Znuny
container (port 8180) on a shared network.

## Running locally

```sh
just golden-up      # build + start MariaDB + Znuny (first boot loads schema, can take minutes)
just golden-seed     # seed admin agent, queue, customer user via Znuny console commands
GOLDEN=1 just golden-test   # run the golden-master pytest suite
just golden-down     # stop (keeps the DB volume)
just golden-clean    # stop and drop the DB volume
```

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

## CI

A manual-only (`workflow_dispatch`) job, `.github/workflows/golden.yml`, runs
the same `just golden-up && just golden-seed && GOLDEN=1 just golden-test`
sequence. It is **not** wired into the on-push pipeline — building and
booting a full Znuny container on every push would be prohibitively slow for
day-to-day CI turnaround.

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
