"""Add notify_in_app and notify_email_digest to users.

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-28 00:00:00.000000 UTC

M6 Step 5 — per-user notification-preference columns.

``users`` gains two boolean columns:

``notify_in_app``
    Boolean NOT NULL, server_default=1 (true).
    False → the in-app inbox returns [] and unread-count 0 for this user;
    notification rows may still be created to feed the email digest.

``notify_email_digest``
    Boolean NOT NULL, server_default=1 (true).
    False → the email channel skips building / sending the daily digest for
    this user.

Existing rows default to true / true (= M4 behaviour preserved; zero-disruption
migration regardless of how many users already exist in the DB).

No backfill needed: the server default handles every existing row.

Upgrade uses SQLite batch mode (``op.batch_alter_table``) to add both columns
inside a single table rebuild.  Batch mode is needed to apply server defaults
on an existing SQLite table.  Both columns are added in the same batch context
to minimise the number of table rebuilds.

Downgrade drops both columns via a second batch rebuild.

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add notify_in_app and notify_email_digest (Boolean NOT NULL, default true) to users."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "notify_in_app",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_email_digest",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    """Drop notify_in_app and notify_email_digest from users."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("notify_email_digest")
        batch_op.drop_column("notify_in_app")
