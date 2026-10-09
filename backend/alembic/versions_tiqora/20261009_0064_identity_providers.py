"""Create identity provider, link and managed-assignment tables.

Revision ID: 20261009_0064
Revises: 20261009_0063
Create Date: 2026-10-09

``metadata_xml`` is ``MEDIUMTEXT`` on MariaDB (SAML IdP metadata can exceed
64 KiB). Booleans and timestamps are written by the application (naive UTC);
no server defaults, in particular none on TEXT columns (MariaDB).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261009_0064"
down_revision: str | None = "20261009_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_identity_provider",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("button_label", sa.String(200), nullable=True),
        sa.Column("button_icon", sa.String(32), nullable=True),
        sa.Column("preset", sa.String(40), nullable=True),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("secrets_enc", sa.Text(), nullable=True),
        sa.Column("match_by", sa.String(16), nullable=False),
        sa.Column("require_sso_opt_in", sa.Boolean(), nullable=False),
        sa.Column("attribute_map", sa.Text(), nullable=False),
        sa.Column("jit_enabled", sa.Boolean(), nullable=False),
        sa.Column("jit_defaults", sa.Text(), nullable=False),
        sa.Column("role_rules", sa.Text(), nullable=False),
        sa.Column("sync_mode", sa.String(16), nullable=False),
        sa.Column(
            "metadata_xml",
            sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb"),
            nullable=True,
        ),
        sa.Column("metadata_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_error", sa.String(500), nullable=True),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tiqora_identity_provider_slug"),
    )
    op.create_index(
        "ix_tiqora_identity_provider_enabled", "tiqora_identity_provider", ["enabled", "sort"]
    )

    op.create_table(
        "tiqora_identity_link",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("user_kind", sa.String(16), nullable=False),
        sa.Column("subject_hash", sa.CHAR(64), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_login", sa.String(200), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_identity_link_provider",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider_id", "user_kind", "subject_hash", name="uq_tiqora_identity_link_subject"
        ),
    )
    op.create_index(
        "ix_tiqora_identity_link_user", "tiqora_identity_link", ["user_kind", "user_id"]
    )

    op.create_table(
        "tiqora_identity_managed_assignment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("permission_key", sa.String(20), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_identity_managed_provider",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "user_id",
            "kind",
            "target_id",
            "permission_key",
            name="uq_tiqora_identity_managed",
        ),
    )
    op.create_index(
        "ix_tiqora_identity_managed_user", "tiqora_identity_managed_assignment", ["user_id"]
    )


def downgrade() -> None:
    op.drop_table("tiqora_identity_managed_assignment")
    op.drop_table("tiqora_identity_link")
    op.drop_table("tiqora_identity_provider")
