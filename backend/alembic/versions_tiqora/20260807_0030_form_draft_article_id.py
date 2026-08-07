"""Add article_id to tiqora_form_draft and make the upsert key unique.

Revision ID: 20260807_0030
Revises: 20260804_0029
Create Date: 2026-08-07

Reply drafts autosave per *article*, not per ticket: an agent can have an
unsent reply open on several articles of the same ticket. Znuny's own
``form_draft`` cannot express that — its drafts hang off (object, action)
and are told apart by a user-supplied ``title``, because there they are
explicit, manually named drafts rather than an autosave. ``tiqora_form_draft``
is Tiqora-owned (no Znuny mirror: ``ticket_id`` instead of
``object_type``/``object_id``, JSON instead of Perl Storable), so we add the
column we actually need.

The unique constraint also makes the ``PUT /tickets/{id}/drafts/{action}``
upsert race-safe; before it, two tabs of the same agent could interleave
SELECT-then-INSERT and end up with duplicate rows.

Note the NULL semantics: MySQL and PostgreSQL both treat NULLs in a unique
constraint as distinct, so this enforces *one* autosave per article while
still allowing any number of rows with ``article_id IS NULL`` — the place
for Znuny-style named drafts, should we add them. It also means the
constraint cannot fail on existing data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0030"
down_revision: str | None = "20260804_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UQ = "uq_tiqora_form_draft_ticket_user_action_article"


def upgrade() -> None:
    op.add_column(
        "tiqora_form_draft",
        sa.Column("article_id", sa.BigInteger(), nullable=True),
    )
    op.create_unique_constraint(
        _UQ,
        "tiqora_form_draft",
        ["ticket_id", "user_id", "action", "article_id"],
    )


def downgrade() -> None:
    op.drop_constraint(_UQ, "tiqora_form_draft", type_="unique")
    op.drop_column("tiqora_form_draft", "article_id")
