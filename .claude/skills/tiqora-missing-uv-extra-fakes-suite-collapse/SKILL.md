---
name: tiqora-missing-uv-extra-fakes-suite-collapse
description: |
  A Tiqora backend `pytest` run reports hundreds of failures and errors that
  look like cross-module DB contamination, but the real cause is a venv synced
  without the `crypto` extra. Use when: (1) `uv run python -m pytest -q` reports
  something like "338 failed, 1343 passed, 171 errors" while each failing module
  passes when run on its own, (2) the failure count changes between otherwise
  identical runs, (3) `ModuleNotFoundError: No module named 'gnupg'` appears
  anywhere in the output, (4) `uv run python -m pytest` says "No module named
  pytest" in a fresh worktree, (5) you are about to blame the session-scoped
  testcontainer or module ordering for a mass failure.
author: Claude Code
version: 1.0.0
date: 2026-09-08
---

# A missing uv extra looks exactly like test contamination

## Problem

The Tiqora backend suite collapses with hundreds of failures and errors. Every
instinct points at the known shared-testcontainer problem (see
`pytest-session-container-id-band-collisions`): modules pass alone and fail
together, and the count wobbles between runs. Chasing that costs a full
debugging cycle.

The actual cause is a venv missing an optional dependency group. `gnupg` is
absent, `test_crypto_postmaster` errors at import, and the resulting collection
errors cascade into unrelated modules.

## Context / Trigger Conditions

- A **fresh worktree**: `uv run python -m pytest` fails with `No module named
  pytest` even though the repo's own `Makefile`/`justfile` say `uv sync`.
  Plain `uv sync` is not enough, and `uv sync --group dev` silently no-ops here
  (the dev group is under `[project.optional-dependencies]`, not
  `[dependency-groups]`) — it prints "Audited N packages" and installs nothing.
- A mass failure whose first concrete error, found with `-x --tb=line`, is
  `ModuleNotFoundError: No module named 'gnupg'`.
- One or two *genuine* failures hide inside the noise, so a plain count
  comparison misleads.

## Solution

Sync exactly what CI syncs (`.github/workflows/ci.yml`, `working-directory:
backend`):

```bash
cd backend
uv sync --extra dev --extra crypto
uv run python -m pytest -q          # 1749 passed, 25 skipped
```

`uv run` resolves against the workspace root `.venv`, not a `backend/.venv`, so
the sync must happen with `backend/` as the working directory.

## Verification

`grep -c "^FAILED"` before and after. With both extras the suite is fully green;
anything left over is a real defect.

## Diagnosing a mass failure without guessing

Run `uv run python -m pytest -q --tb=line -x` and read the **first** error. One
import error at collection time is worth more than the summary line, which
reports the cascade rather than the cause. Only after that error is understood
does "modules pass alone but fail together" mean contamination.

To separate a real regression from environmental noise, take a **set diff of
failing test ids**, not a count:

```bash
git stash push -u -m "<unique-tag>"     # shared stash stack: tag it
git stash list --format='%H %gs'        # capture your SHA immediately
uv run python -m pytest -q | grep '^FAILED' | sort > /tmp/base.txt
git stash apply <sha>
uv run python -m pytest -q | grep '^FAILED' | sort > /tmp/branch.txt
comm -13 /tmp/base.txt /tmp/branch.txt  # regressions you introduced
```

In the real case this reduced "5 failures" to one genuine regression (four
parametrisations of a single SPNEGO test) plus one environmental artefact.

## Notes

- **The real regression it uncovered is worth knowing separately.** Adding a
  `user_preferences` write to the login path broke `test_spnego.py`, whose seed
  helper did `DELETE FROM users WHERE login = 'alice'` without first removing
  the agent's `user_preferences` rows — `FK_user_preferences_user_id_id` blocks
  it. Test fixtures that delete a Znuny agent must delete children first, the
  way `tiqora.domain.user_delete.delete_user_rows` does.
- **MariaDB died on the obvious fix.** `DELETE FROM user_preferences WHERE
  user_id IN (SELECT id FROM users WHERE login = :login)` produced
  `pymysql.err.OperationalError (2013, 'Lost connection to MySQL server during
  query')`, reproducibly and on MariaDB only. Two plain statements — select the
  id, then delete by id — work fine. Suspect the subquery before suspecting the
  container.
- Related: `uv-run-pytest-broken-use-python-m` (always `uv run python -m pytest`
  in `backend/`), `pytest-session-container-id-band-collisions` (the real
  contamination class this one imitates).
