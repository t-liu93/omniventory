"""Add responsible_user_id to item_definitions.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-28 00:00:00.000000 UTC

M6 Step 4 — responsible-party assignment on item definitions.

``item_definitions`` gains one column:

``responsible_user_id``  Integer FK → users.id, nullable, ondelete=SET NULL.
    The default responsible party for all lots belonging to this definition.
    NULL = unassigned (reminder engine falls back to all active users — M4
    parity, zero-disruption migration).

    When the referenced user is deleted, ``ON DELETE SET NULL`` clears this
    column automatically, which restores the fallback-to-all behaviour without
    leaving a dangling FK.

No backfill: existing rows stay NULL (= unassigned = M4 behaviour preserved).

Upgrade uses SQLite batch mode (``op.batch_alter_table``) to add the column
together with the FK constraint in one table rebuild.  Plain ``op.add_column``
works for nullable columns but does not wire up the FK on SQLite; batch mode
is the M0 convention for column deltas that include FK constraints.

Index ``ix_item_definitions_responsible_user_id`` is created outside the
batch context (non-unique lookups; batch mode does not carry it forward from
the old table schema).

Downgrade drops the index first (outside the batch), then rebuilds the table
without the column and constraint via batch mode.

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add responsible_user_id (FK → users.id, SET NULL, nullable) to item_definitions."""
    with op.batch_alter_table("item_definitions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("responsible_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_item_definitions_responsible_user_id",
            "users",
            ["responsible_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_item_definitions_responsible_user_id",
        "item_definitions",
        ["responsible_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop responsible_user_id from item_definitions."""
    op.drop_index(
        "ix_item_definitions_responsible_user_id",
        table_name="item_definitions",
    )
    with op.batch_alter_table("item_definitions", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_item_definitions_responsible_user_id",
            type_="foreignkey",
        )
        batch_op.drop_column("responsible_user_id")
