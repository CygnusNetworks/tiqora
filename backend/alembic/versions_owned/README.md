# Schema-ownership migration chain

This directory holds Alembic revisions that may **add constraints, indexes, and
orphan-reporting tables** to (or around) former Znuny tables.

## Gate

**Do not enable or run these migrations during parallel operation with Znuny.**

Activation requires:

1. Explicit cutover completion (Phase 5).
2. Config / DB marker: `TIQORA_SCHEMA_OWNERSHIP=true` (and matching DB flag).
3. Operator confirmation in the cutover runbook.

Until then, only `versions_tiqora/` is active. That chain creates exclusively
`tiqora_*` tables and never alters Znuny DDL.

## Intended first owned migrations (post-cutover)

- Additive foreign keys where safe
- Helpful indexes for Tiqora query patterns
- Orphan report / cleanup scaffolding

All changes must remain reverse-friendly for the documented rollback probe.
