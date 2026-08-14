"""Make the customer-login suffix strip configurable per link entry.

``{customer_user}`` previously always cut the login at ``#`` — a
site-specific Znuny convention (``z50test#3`` = contract disambiguator),
not a generic rule. ``login_suffix_separator`` now controls it per entry:
NULL/empty = use the login verbatim; e.g. ``#`` = cut at the first ``#``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_0040"
down_revision = "20260814_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tiqora_queue_customer_link",
        sa.Column("login_suffix_separator", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_queue_customer_link", "login_suffix_separator")
