"""Create tiqora_avatar for uploaded customer photos and company logos.

Revision ID: 20261007_0060
Revises: 20261007_0059
Create Date: 2026-10-07

One row per picture an agent uploaded: ``kind = "email"`` keyed by the SHA-256
of the customer's e-mail address, ``kind = "domain"`` keyed by the company's
website domain. Uploads take precedence over pictures fetched from Gravatar or
the company website (those live only in Redis). ``data`` is ``MEDIUMBLOB`` on
MariaDB, since a 256 px PNG can exceed plain ``BLOB``'s 64 KiB.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261007_0060"
down_revision: str | None = "20261007_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_avatar",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("owner_ref", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column(
            "data",
            sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql", "mariadb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "subject_key", name="uq_tiqora_avatar_kind_subject"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_avatar")
