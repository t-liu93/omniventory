"""Create categories table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16 00:00:00.000000 UTC

This migration creates the ``categories`` self-referential tree table.

``categories``
    Each row is a category node for classifying item definitions.
    ``parent_id`` is a self-referential FK (NULL = root node); the tree is
    arbitrary depth.  Cycle prevention is enforced in the application service
    layer, not by a DB trigger (roadmap §2.11).  The tree pattern mirrors
    ``locations`` (M1.md §3.2 / Step 2).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the categories table."""
    op.create_table(
        "categories",
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
            ["categories.id"],
            name="fk_categories_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])


def downgrade() -> None:
    """Drop the categories table."""
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_table("categories")
