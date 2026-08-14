"""Add ``tiqora_queue_customer_link`` — per-queue external customer-tool link.

Ticket-zoom header gets a second, per-queue-configurable button next to the
existing internal "Kunde" link, pointing at an external customer-management
tool (e.g. a NetAdmin diagnosis page). ``admin_url_template`` lets admins see
a privileged variant (e.g. a Kerberos-auth host) of the same link;
``visibility`` can additionally hide the button from non-admins entirely.
See ``tiqora.domain.customer_link`` for template resolution.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_0039"
down_revision = "20260814_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tiqora_queue_customer_link",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("url_template", sa.String(1024), nullable=False),
        sa.Column("admin_url_template", sa.String(1024), nullable=True),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="all"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column(
            "create_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column(
            "change_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("queue_id", name="uq_tiqora_queue_customer_link_queue_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_queue_customer_link")
