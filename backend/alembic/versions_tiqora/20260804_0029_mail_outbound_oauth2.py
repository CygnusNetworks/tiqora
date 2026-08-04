"""Add oauth2_token_config_name to tiqora_mail_outbound.

Revision ID: 20260804_0029
Revises: 20260728_0028
Create Date: 2026-08-04

Znuny binds outbound SMTP OAuth via SysConfig
``SendmailModule::OAuth2TokenConfigName`` (config *name*). Store the same
reference on the Tiqora singleton so a shared legacy ``oauth2_token_config``
row can be used for XOAUTH2 sends in parallel operation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0029"
down_revision: str | None = "20260728_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_mail_outbound",
        sa.Column(
            "oauth2_token_config_name",
            sa.String(length=250),
            server_default="",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tiqora_mail_outbound", "oauth2_token_config_name")
