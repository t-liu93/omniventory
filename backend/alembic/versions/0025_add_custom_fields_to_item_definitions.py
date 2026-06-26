"""Add custom_fields to item_definitions.

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 4 — custom key/value fields on item definitions.

``item_definitions`` gains one column:

``custom_fields`` Text, nullable
    JSON object string holding a flat map ``str → (str|int|float|bool|null)``.
    NULL = no custom fields set.  (De)serialized and validated entirely in the
    service/schema layer; **no** DB JSON functions (portable, roadmap §2.11).

Plain ``op.add_column`` for upgrade (nullable ADD COLUMN is safe on SQLite
without batch mode — SQLite can add nullable columns directly).  Downgrade
uses batch mode to drop the column (SQLite cannot ALTER TABLE DROP COLUMN
directly in all versions).

No backfill: existing rows stay NULL (correct — "no custom fields" is the
right default for all existing item definitions).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add custom_fields (nullable Text) to item_definitions."""
    op.add_column(
        "item_definitions",
        sa.Column("custom_fields", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop custom_fields from item_definitions (batch mode for SQLite)."""
    # SQLite cannot ALTER TABLE DROP COLUMN directly in all versions;
    # batch mode rebuilds the table to safely remove the column.
    with op.batch_alter_table("item_definitions", schema=None) as batch_op:
        batch_op.drop_column("custom_fields")
