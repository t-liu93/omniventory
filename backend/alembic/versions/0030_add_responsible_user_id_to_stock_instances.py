"""Add responsible_user_id to stock_instances.

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-28 00:00:00.000000 UTC

M6 Step 4 — responsible-party assignment on stock instances.

``stock_instances`` gains one column:

``responsible_user_id``  Integer FK → users.id, nullable, ondelete=SET NULL.
    Per-lot override of the definition's default responsible party.

    Effective responsible party for a lot:
      1. lot.responsible_user_id          (this column — per-lot override)
      2. lot.definition.responsible_user_id  (definition default, migration 0029)
      3. None → fallback to all active users (M4 behaviour)

    When the referenced user is deleted, ``ON DELETE SET NULL`` clears this
    column automatically, collapsing to the next level of the chain without
    dropping any reminder.

No backfill: existing rows stay NULL (= inherit definition → fallback).

Upgrade uses SQLite batch mode (``op.batch_alter_table``) to add the column
together with the FK constraint in one table rebuild.  Index is created
outside the batch context.

Downgrade drops the index first, then rebuilds the table without the column
and constraint via batch mode.

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add responsible_user_id (FK → users.id, SET NULL, nullable) to stock_instances."""
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        batch_op.add_column(sa.Column("responsible_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_stock_instances_responsible_user_id",
            "users",
            ["responsible_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_stock_instances_responsible_user_id",
        "stock_instances",
        ["responsible_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop responsible_user_id from stock_instances."""
    op.drop_index(
        "ix_stock_instances_responsible_user_id",
        table_name="stock_instances",
    )
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_stock_instances_responsible_user_id",
            type_="foreignkey",
        )
        batch_op.drop_column("responsible_user_id")
