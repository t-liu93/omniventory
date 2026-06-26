"""Add custom_fields to stock_instances.

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 4 — custom key/value fields on stock instances.

``stock_instances`` gains one column:

``custom_fields`` Text, nullable
    JSON object string holding a flat map ``str → (str|int|float|bool|null)``.
    NULL = no custom fields set.  (De)serialized and validated entirely in the
    service/schema layer; **no** DB JSON functions (portable, roadmap §2.11).

Plain ``op.add_column`` for upgrade (nullable ADD COLUMN is safe on SQLite
without batch mode).  Downgrade uses batch mode to drop the column.

No backfill: existing rows stay NULL.

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add custom_fields (nullable Text) to stock_instances."""
    op.add_column(
        "stock_instances",
        sa.Column("custom_fields", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop custom_fields from stock_instances (batch mode for SQLite)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly in all versions;
    # batch mode rebuilds the table to safely remove the column.
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        batch_op.drop_column("custom_fields")
