"""Create locations table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16 00:00:00.000000 UTC

This migration creates the ``locations`` self-referential tree table.

``locations``
    Each row is a physical location in the household.  ``parent_id`` is a
    self-referential FK (NULL = root node); the tree is arbitrary depth.
    Cycle prevention is enforced in the application service layer, not by a
    DB trigger (roadmap §2.11).

    Note: ``item_instance_id`` (the container-as-item bridge FK to
    ``stock_instances``) is intentionally absent here — it is added in Step 4
    / migration 0008 after ``stock_instances`` is created (§3.6 of M1.md).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the locations table."""
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["locations.id"],
            name="fk_locations_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locations_parent_id", "locations", ["parent_id"])


def downgrade() -> None:
    """Drop the locations table."""
    op.drop_index("ix_locations_parent_id", table_name="locations")
    op.drop_table("locations")
