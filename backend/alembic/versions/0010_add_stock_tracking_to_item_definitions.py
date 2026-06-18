"""Add stock_tracking_mode and min_stock to item_definitions.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-18 00:00:00.000000 UTC

M2 Step 1 — per-definition stock-tracking mode and minimum-stock threshold.

``item_definitions`` gains two columns:

``stock_tracking_mode`` String(16), NOT NULL, server_default='exact'
    Validated app-layer against STOCK_TRACKING_MODES = ("exact","level","none").
    No DB CHECK constraint — the set may grow; roadmap §2.11.
    Default 'exact' so every pre-existing definition is a regular quantity-
    tracked item without any data migration.

``min_stock`` Numeric(18,6), nullable
    Reorder point / low-stock threshold; meaningful only for 'exact' mode.
    NULL means no threshold is set.

Both columns are added with op.add_column (nullable / server-defaulted —
no batch table rebuild needed on SQLite).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add stock_tracking_mode and min_stock columns to item_definitions."""
    op.add_column(
        "item_definitions",
        sa.Column(
            "stock_tracking_mode",
            sa.String(16),
            nullable=False,
            server_default="exact",
        ),
    )
    op.add_column(
        "item_definitions",
        sa.Column("min_stock", sa.Numeric(18, 6), nullable=True),
    )


def downgrade() -> None:
    """Drop stock_tracking_mode and min_stock from item_definitions (batch mode)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly; batch mode rebuilds the table.
    with op.batch_alter_table("item_definitions", schema=None) as batch_op:
        batch_op.drop_column("min_stock")
        batch_op.drop_column("stock_tracking_mode")
