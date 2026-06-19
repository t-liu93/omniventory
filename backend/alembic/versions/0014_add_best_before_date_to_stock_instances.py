"""Add best_before_date to stock_instances.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-19 00:00:00.000000 UTC

M3 Step 2 — per-lot best-before date.

``stock_instances`` gains one column:

``best_before_date`` Date, nullable
    Per-lot / batch best-before date.  ``NULL`` means no expiry is tracked
    (non-perishable, or perishable without a known date).
    Mode-independent: settable for ``exact``/``level``/``none`` lots alike,
    mirroring ``warranty_expires``.
    Set explicitly on create OR auto-computed by the service layer from the
    definition's ``default_best_before_days`` (M3 Step 2).
    Editable via ``PATCH /instances/{id}``; subsequent ``intake`` movements
    into an existing lot do NOT touch it (one lot = one batch = one expiry).
    The FEFO primary sort key (M3 Step 3) and the expiring-read filter
    (M3 Step 4).

Plain ``op.add_column`` (nullable plain add — no batch table rebuild needed
on SQLite, matching ``0013``).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add best_before_date (nullable Date) to stock_instances."""
    op.add_column(
        "stock_instances",
        sa.Column("best_before_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    """Drop best_before_date from stock_instances (batch mode for SQLite)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly in all versions;
    # batch mode rebuilds the table to safely remove the column.
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        batch_op.drop_column("best_before_date")
