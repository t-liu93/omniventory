"""Add default_best_before_days to item_definitions.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-19 00:00:00.000000 UTC

M3 Step 1 — per-definition shelf-life default.

``item_definitions`` gains one column:

``default_best_before_days`` Integer, nullable
    Default shelf life in days; ``≥ 0`` (validated app-layer via Pydantic
    ``ge=0`` — no DB CHECK constraint per roadmap §2.11).
    ``NULL`` means no default shelf life is configured.
    Consumed by M3 Step 2's auto-compute on intake.
    Editing it is non-retroactive: existing lots' ``best_before_date``
    is not touched (M3.md §2).

Plain ``op.add_column`` (nullable plain add — no batch table rebuild needed
on SQLite, matching the same approach as ``0010`` and the ``op.add_column``
calls in ``0012``).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add default_best_before_days (nullable Integer) to item_definitions."""
    op.add_column(
        "item_definitions",
        sa.Column("default_best_before_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop default_best_before_days from item_definitions (batch mode for SQLite)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly in all versions;
    # batch mode rebuilds the table to safely remove the column.
    with op.batch_alter_table("item_definitions", schema=None) as batch_op:
        batch_op.drop_column("default_best_before_days")
