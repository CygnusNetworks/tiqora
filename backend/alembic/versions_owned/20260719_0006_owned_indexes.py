"""Additive helper indexes on Znuny tables (Phase 5, subtask 2).

Revision ID: 20260719_0006
Revises: 20260720_0007
Create Date: 2026-07-19

Branches off ``20260720_0007`` as its own ``owned`` branch — deliberately NOT
rebased onto the latest tiqora revision. Rebasing made every later tiqora
revision an *ancestor* of this one, so a database already stamped at
``20260719_0006`` looked "at head" and silently skipped them. With a fixed
branch point the two chains advance independently; ``tiqora migrate upgrade``
targets ``heads`` — see ``tests/test_migration_gate.py``.

**Gated**: only reachable once schema ownership is active (see
``tiqora.domain.ownership`` and ``alembic/env.py``'s dynamic
``version_locations``). Index-only — a strict no-op on data, reversible, and
safe to run against a live dataset (``CREATE INDEX`` does not touch row
content). Znuny already indexes the most obvious single-column lookups
(``ticket.customer_user_id``, ``article_data_mime.a_message_id_md5``, etc. —
see ``scripts/database/schema.xml``); the composite indexes below cover
multi-column filter patterns exercised by Tiqora's own query code that Znuny
never needed:

- ``ticket(customer_user_id, archive_flag)`` — customer-portal ticket list
  (``tiqora.domain.portal_ticket_service``) always filters by both.
- ``ticket(queue_id, ticket_state_id)`` — agent queue view
  (``tiqora.domain.ticket_service``) filters queue set + optional state.
- ``dynamic_field_value(object_id, field_id)`` — per-object per-field value
  lookup (``tiqora.domain.ticket_service``), currently only single-column
  indexed on each side separately.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_0006"
down_revision: str | None = "20260720_0007"
branch_labels: str | Sequence[str] | None = ("owned",)
depends_on: str | Sequence[str] | None = None

_INDEXES = [
    ("ix_owned_ticket_customer_archive", "ticket", ["customer_user_id", "archive_flag"]),
    ("ix_owned_ticket_queue_state", "ticket", ["queue_id", "ticket_state_id"]),
    (
        "ix_owned_dynamic_field_value_object_field",
        "dynamic_field_value",
        ["object_id", "field_id"],
    ),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
