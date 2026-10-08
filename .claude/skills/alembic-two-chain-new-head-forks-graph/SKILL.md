---
name: alembic-two-chain-new-head-forks-graph
description: |
  Two Alembic chains in separate version_locations (a primary chain plus a
  gated/secondary one): how to keep the graph sound when adding migrations,
  and why "rebase the gated chain onto the newest primary head" is WRONG.
  Use when: (1) adding a migration to a repo with versions_tiqora +
  versions_owned (Tiqora) or any primary/gated chain split, (2) CI reports
  "Multiple head revisions are present for given argument 'head'" /
  alembic.script.revision.MultipleHeads, (3) a test hardcodes the head id
  (TIQORA_HEAD, version_num asserts) and fails "assert '<new>' == '<old>'",
  (4) you are tempted to bump the gated chain's down_revision to the new
  primary head, (5) a DB stamped at the gated head does not get newer primary
  migrations (unknown column errors after deploy). Answer: the gated chain is
  its own branch (branch_labels) on a FIXED old branch point, upgrades target
  `heads`. Tiqora switched to this on 2026-09-24.
author: Claude Code
version: 2.0.0
date: 2026-09-24
---

# Two-chain Alembic: branch the gated chain, never rebase it

## Problem

A repo keeps two Alembic chains in separate `version_locations` — a primary
chain (always active) and a gated chain (only appended to the Config when a
flag is on; see `alembic-env-cannot-gate-version-locations` for why the gate
must live at the Config layer).

The tempting way to keep the combined graph single-headed is to set the gated
chain's first revision `down_revision = <current primary head>` and re-point
it every time a primary migration is added. **That is broken, not just
tedious**:

```
DB with the gate on, upgraded while the graph was:
  ...0044 → 0006(gated)                      version table: {0006}

Later, after someone "rebases" 0006 onto 0047:
  ...0044 → 0045 → 0046 → 0047 → 0006(gated)
```

The DB's version table still says `0006`, and in the new graph 0045–0047 are
*ancestors* of 0006 — Alembic treats them as applied. `upgrade head` is a
no-op, the columns never get created, every query on them fails with "unknown
column". Nothing errors at migration time.

The rebasing also causes the familiar CI symptom whenever someone forgets it:
`MultipleHeads` (new primary rev and the gated rev both children of the old
head), visible only on the gated code path.

## Solution (Tiqora, commit "keep the owned migration chain on a fixed branch point")

1. The gated chain's first revision gets its own branch on a fixed, old parent:
   ```python
   down_revision = "20260720_0007"          # fixed forever — never bump it
   branch_labels = ("owned",)
   ```
2. The gated upgrade path targets `heads`, not `head`
   (`tiqora migrate upgrade` defaults to `heads`; `test_owned_migrations.py`
   calls `command.upgrade(cfg, "heads")`). Un-gated paths (bootstrap,
   default alembic.ini) still see one chain and can keep `head`.
3. The gate test asserts `heads == {PRIMARY_HEAD, GATED_HEAD}` with the gate on,
   plus a regression test that `PRIMARY_HEAD` is not an ancestor of the gated
   head (`script.iterate_revisions(GATED_HEAD, "base")`).

Adding a primary migration is now a normal one-file change **plus** the
hardcoded head literals in tests:

```sh
rg -n "<old_head_rev>" backend/tests   # TIQORA_HEAD in test_migration_gate.py,
                                       # version_num assert in test_migrate_cli.py
```

Do **not** touch `versions_owned/*` when adding a tiqora migration.

## Existing databases from the rebase era

A DB whose version table holds only the gated revision (stamped under the old
linear graph) is ambiguous under the branched graph: Alembic now thinks the
primary chain sits at the branch point and would re-run everything after it.
Such a DB needs a manual `alembic stamp` of both heads (primary head it
actually has + gated head) before the first `upgrade heads`. As of 2026-09-24
no Tiqora install had schema ownership enabled (it requires the Znuny cutover),
so this was not needed in practice — check `tiqora_alembic_version` first.

## Verification

```sh
uv run python -m pytest -q tests/test_migration_gate.py          # graph-only, <1 s
uv run python -m pytest -q tests/test_migrate_cli.py tests/test_owned_migrations.py
```

With the gate on, the version table afterwards holds **two** rows (one per
head) — that is expected, not a leak.

## Notes

- Run DB-backed chain tests in CI's collection order (alphabetical); a
  session-scoped container shared with a CLI test that asserts the primary
  head fails in reverse order for unrelated reasons.
- Hardcoded head literals in tests are a useful tripwire — keep them.

## See also

- `alembic-env-cannot-gate-version-locations` — why the gate must be applied to
  the `Config` before the command runs (env.py is too late).

## References

- Alembic branches: https://alembic.sqlalchemy.org/en/latest/branches.html
