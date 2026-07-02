"""Add dismissed_at to notifications.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-02 00:00:00.000000 UTC

Notification hygiene hardening round — Step 1 (backend soft-dismiss
infrastructure).

``notifications`` gains one column:

``dismissed_at`` DateTime(timezone=True), nullable
    In-app soft-dismiss state.  NULL = visible in the inbox (current
    behaviour); stamped = hidden from the inbox.  Independent of ``read_at``
    (a dismissed row may be read or unread).

Soft-dismiss deliberately does **not** remove the row and is **not** consulted
by the dedup lookup (``_get_by_dedup`` / ``create_if_absent``) or the
low-stock episode helpers (``open_low_stock_opener`` et al.) — a dismissed row
must keep anchoring its dedup key and keep holding live episode state so a
still-active source does not re-spam the user and a rescan does not open a
duplicate low-stock episode.  See ``app/models/notification.py`` and
``app/repositories/notification.py`` for the full contract.

No backfill: existing rows stay NULL (= all currently-visible rows remain
visible — zero-disruption migration).

Plain ``op.add_column`` for upgrade (nullable ADD COLUMN is safe on SQLite
without batch mode — matches the established idiom for nullable, no-default
column additions in this project, e.g. 0016, 0017, 0025, 0026).  Downgrade
uses batch mode to drop the column (SQLite cannot ALTER TABLE DROP COLUMN
directly in all versions; batch mode rebuilds the table safely).

No new index: household scale, and the inbox query is already user-scoped +
limited, so an index on ``dismissed_at`` is not warranted.

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add dismissed_at (nullable DateTime(timezone=True)) to notifications."""
    op.add_column(
        "notifications",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop dismissed_at from notifications (batch mode for SQLite)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly in all versions;
    # batch mode rebuilds the table to safely remove the column.
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_column("dismissed_at")
