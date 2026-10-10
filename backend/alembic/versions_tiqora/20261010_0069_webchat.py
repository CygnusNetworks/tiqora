"""Web chat: tiqora_chat_config, _session, _message, _agent_state.

Revision ID: 20261010_0069
Revises: 20261009_0067
Create Date: 2026-10-10

All structured columns are JSON stored as ``TEXT`` (house style). No foreign keys:
``ticket_id`` and the user ids point into Znuny tables. Timestamps are written by the
application as naive UTC. ``ux_tiqora_chat_message_client`` relies on NULL ``client_id``
values never colliding in a unique index (true on MariaDB and PostgreSQL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0069"
down_revision: str | None = "20261009_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_chat_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_key", sa.String(40), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allowed_origins", sa.Text(), nullable=False),
        sa.Column("topics", sa.Text(), nullable=False),
        sa.Column("greeting", sa.String(500), nullable=True),
        sa.Column("privacy_url", sa.String(500), nullable=True),
        sa.Column("offline_mode", sa.String(10), nullable=False, server_default="form"),
        sa.Column("business_calendar", sa.String(10), nullable=True),
        sa.Column("wait_timeout_seconds", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("prechat_fields", sa.Text(), nullable=False),
        sa.Column("upload_policy", sa.Text(), nullable=False),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("rate_limits", sa.Text(), nullable=False),
        sa.Column("default_customer_user", sa.String(200), nullable=True),
        sa.Column("show_agent_names", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("changed", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_tiqora_chat_config_name", "tiqora_chat_config", ["name"], unique=True)
    op.create_index("ux_tiqora_chat_config_key", "tiqora_chat_config", ["public_key"], unique=True)

    op.create_table(
        "tiqora_chat_session",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.String(40), nullable=True),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("end_reason", sa.String(20), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("visitor_id", sa.String(64), nullable=False),
        sa.Column("visitor_name", sa.String(200), nullable=True),
        sa.Column("visitor_email", sa.String(250), nullable=True),
        sa.Column("customer_user_login", sa.String(200), nullable=True),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("waiting_since", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("visitor_last_seen", sa.DateTime(), nullable=False),
        sa.Column("page_url", sa.String(500), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("ip_hash", sa.String(32), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_chat_session_status", "tiqora_chat_session", ["status", "config_id"])
    op.create_index("ix_tiqora_chat_session_ticket", "tiqora_chat_session", ["ticket_id"])
    op.create_index(
        "ix_tiqora_chat_session_assigned", "tiqora_chat_session", ["assigned_user_id", "status"]
    )
    op.create_index("ix_tiqora_chat_session_visitor", "tiqora_chat_session", ["visitor_id"])

    op.create_table(
        "tiqora_chat_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sender", sa.String(10), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("attachments", sa.Text(), nullable=True),
        sa.Column("article_id", sa.BigInteger(), nullable=True),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_tiqora_chat_message_seq", "tiqora_chat_message", ["session_id", "seq"], unique=True
    )
    op.create_index(
        "ux_tiqora_chat_message_client",
        "tiqora_chat_message",
        ["session_id", "client_id"],
        unique=True,
    )
    op.create_index("ix_tiqora_chat_message_ticket", "tiqora_chat_message", ["ticket_id"])
    op.create_index("ix_tiqora_chat_message_article", "tiqora_chat_message", ["article_id"])

    op.create_table(
        "tiqora_chat_agent_state",
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("topics", sa.Text(), nullable=False),
        sa.Column("max_concurrent", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("changed", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_chat_agent_state")
    op.drop_table("tiqora_chat_message")
    op.drop_table("tiqora_chat_session")
    op.drop_table("tiqora_chat_config")
