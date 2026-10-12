"""Dashboards phase 2: widget pin and template-link columns, wallboard link tables.

Revision ID: 20261010_0075
Revises: 20261010_0074
Create Date: 2026-10-11

Pre-assigned number 0075 (the controller re-chains at merge). Znuny ids (users) are mirrored
without foreign keys; every FK stays inside the ``tiqora_*`` tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0075"
down_revision: str | None = "20261010_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_dashboard_widget",
        sa.Column("pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "tiqora_dashboard_widget", sa.Column("template_widget_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "tiqora_dashboard_widget", sa.Column("added_in_version", sa.Integer(), nullable=True)
    )

    op.create_table(
        "tiqora_dashboard_kiosk_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("rotate_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("show_details", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_tiqora_dashboard_kiosk_token_hash"),
    )
    op.create_index(
        "ix_tiqora_dashboard_kiosk_token_creator", "tiqora_dashboard_kiosk_token", ["created_by"]
    )

    op.create_table(
        "tiqora_dashboard_kiosk_item",
        sa.Column("token_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("dashboard_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["tiqora_dashboard_kiosk_token.id"],
            name="fk_tiqora_dashboard_kiosk_item_token",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            ["tiqora_dashboard.id"],
            name="fk_tiqora_dashboard_kiosk_item_dashboard",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_id", "dashboard_id"),
    )
    op.create_index(
        "ix_tiqora_dashboard_kiosk_item_dashboard",
        "tiqora_dashboard_kiosk_item",
        ["dashboard_id"],
    )


def downgrade() -> None:
    op.drop_table("tiqora_dashboard_kiosk_item")
    op.drop_table("tiqora_dashboard_kiosk_token")
    op.drop_column("tiqora_dashboard_widget", "added_in_version")
    op.drop_column("tiqora_dashboard_widget", "template_widget_id")
    op.drop_column("tiqora_dashboard_widget", "pinned")
