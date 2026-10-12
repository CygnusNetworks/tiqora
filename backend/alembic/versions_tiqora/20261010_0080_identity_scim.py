"""Identity phase 3: SCIM provisioning, step-up MFA and session lifetime columns/tables.

Revision ID: 20261010_0080
Revises: 20261010_0077
Create Date: 2026-10-12

Adds six provider columns (step-up flag + policy, per-IdP session lifetimes, SCIM switches)
and the tables ``tiqora_scim_token``, ``tiqora_scim_user``, ``tiqora_scim_group``,
``tiqora_scim_group_member``, ``tiqora_scim_pending_sync`` and ``tiqora_scim_event``.
Case-insensitive lookups use the sha256 ``*_fold_hash`` columns (no collation-dependent
equality). ``tiqora_scim_event`` has no FK: the log outlives a deleted provider.
Timestamps are written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0080"
down_revision: str | None = "20261010_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_identity_provider",
        sa.Column("require_mfa_claim", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tiqora_identity_provider", sa.Column("mfa_policy", sa.Text(), nullable=True))
    op.add_column(
        "tiqora_identity_provider", sa.Column("session_idle_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "tiqora_identity_provider",
        sa.Column("session_absolute_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tiqora_identity_provider",
        sa.Column("scim_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tiqora_identity_provider",
        sa.Column("scim_adopt_existing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "tiqora_scim_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("user_kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_scim_token_provider",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="uq_tiqora_scim_token_hash"),
    )
    op.create_index("ix_tiqora_scim_token_provider", "tiqora_scim_token", ["provider_id"])

    op.create_table(
        "tiqora_scim_user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("user_kind", sa.String(16), nullable=False),
        sa.Column("scim_id", sa.CHAR(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("user_name_fold", sa.String(255), nullable=False),
        sa.Column("user_name_fold_hash", sa.CHAR(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("external_id_hash", sa.CHAR(64), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("changed", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_scim_user_provider",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("scim_id", name="uq_tiqora_scim_user_scim_id"),
        sa.UniqueConstraint("user_kind", "user_id", name="uq_tiqora_scim_user_account"),
    )
    op.create_index(
        "ix_tiqora_scim_user_name",
        "tiqora_scim_user",
        ["provider_id", "user_kind", "user_name_fold_hash"],
    )
    op.create_index(
        "ix_tiqora_scim_user_ext",
        "tiqora_scim_user",
        ["provider_id", "user_kind", "external_id_hash"],
    )

    op.create_table(
        "tiqora_scim_group",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("scim_id", sa.CHAR(36), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("display_name_fold", sa.String(255), nullable=False),
        sa.Column("display_name_fold_hash", sa.CHAR(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("external_id_hash", sa.CHAR(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("changed", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_scim_group_provider",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("scim_id", name="uq_tiqora_scim_group_scim_id"),
    )
    op.create_index(
        "ix_tiqora_scim_group_name",
        "tiqora_scim_group",
        ["provider_id", "display_name_fold_hash"],
    )
    op.create_index(
        "ix_tiqora_scim_group_ext", "tiqora_scim_group", ["provider_id", "external_id_hash"]
    )

    op.create_table(
        "tiqora_scim_group_member",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "member_id"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["tiqora_scim_group.id"],
            name="fk_tiqora_scim_group_member_group",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["tiqora_scim_user.id"],
            name="fk_tiqora_scim_group_member_member",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tiqora_scim_group_member_member", "tiqora_scim_group_member", ["member_id"])

    op.create_table(
        "tiqora_scim_pending_sync",
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("provider_id", "user_id"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_identity_provider.id"],
            name="fk_tiqora_scim_pending_sync_provider",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tiqora_scim_pending_sync_queued", "tiqora_scim_pending_sync", ["queued_at"])

    op.create_table(
        "tiqora_scim_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("token_id", sa.BigInteger(), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("resource", sa.String(8), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("scim_id", sa.CHAR(36), nullable=True),
        sa.Column("user_kind", sa.String(16), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_scim_event_provider", "tiqora_scim_event", ["provider_id", "id"])
    op.create_index("ix_tiqora_scim_event_created", "tiqora_scim_event", ["created"])


def downgrade() -> None:
    op.drop_table("tiqora_scim_event")
    op.drop_table("tiqora_scim_pending_sync")
    op.drop_table("tiqora_scim_group_member")
    op.drop_table("tiqora_scim_group")
    op.drop_table("tiqora_scim_user")
    op.drop_table("tiqora_scim_token")
    for col in (
        "scim_adopt_existing",
        "scim_enabled",
        "session_absolute_seconds",
        "session_idle_seconds",
        "mfa_policy",
        "require_mfa_claim",
    ):
        op.drop_column("tiqora_identity_provider", col)
