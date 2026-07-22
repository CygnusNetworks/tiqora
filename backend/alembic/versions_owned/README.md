# Schema-ownership migration chain

This directory holds Alembic revisions that may **add constraints, indexes, and
orphan-reporting tables** to (or around) former Znuny tables.

## Gate

**Do not enable or run these migrations during parallel operation with Znuny.**

Activation requires:

1. Explicit cutover completion.
2. Config / DB marker: `TIQORA_SCHEMA_OWNERSHIP=true` (and matching DB flag).
3. Operator confirmation in the cutover runbook.

Until then, only `versions_tiqora/` is active. That chain creates exclusively
`tiqora_*` tables and never alters Znuny DDL.

## First owned migration

- `20260719_0006_owned_indexes.py` — three additive composite indexes
  (`ticket(customer_user_id, archive_flag)`, `ticket(queue_id, ticket_state_id)`,
  `dynamic_field_value(object_id, field_id)`) covering multi-column filter
  patterns Tiqora's own query code uses that Znuny's single-column indexes
  don't. Index-only: a strict no-op on data, fully reversible.

The orphan-FK report (`tiqora ownership orphan-report`) is **not** a
migration — it is a read-only query (`tiqora.domain.orphan_report`) run
on-demand against the ~15 most important Znuny relations. No destructive
cleanup is implemented in v1.

All changes must remain reverse-friendly for the documented rollback probe.
