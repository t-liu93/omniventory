"""Create tags table.

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 2 — flat colour-able tags.

``tags`` stores unique tag names (case-insensitive uniqueness enforced in the
service layer; the DB unique constraint is on the raw ``name`` column).

See M5.md §3.3 for the full schema rationale.

Migration is fully reversible: upgrade creates the table + unique constraint,
downgrade drops the table.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the tags table."""
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )


def downgrade() -> None:
    """Drop the tags table."""
    op.drop_table("tags")
