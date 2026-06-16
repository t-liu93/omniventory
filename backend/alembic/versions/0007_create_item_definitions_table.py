"""Create item_definitions table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16 00:00:00.000000 UTC

This migration creates the ``item_definitions`` table — the "what kind of
thing" records that capture product identity (name, category, kind, unit,
default location) without tracking specific physical units.

``item_definitions``
    - ``kind_id`` is a real FK → ``item_kinds.id`` (NOT a string enum or
      CHECK constraint) — M1.md §3.4 / §2 "kind" locked decision.
    - ``category_id`` → ``categories.id`` (nullable); ``default_location_id``
      → ``locations.id`` (nullable).
    - ``min_stock`` and ``default_best_before_days`` are intentionally absent
      (added by M2/M3 respectively — M1.md §2 "Definition defaults timing").

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the item_definitions table."""
    op.create_table(
        "item_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False, server_default="pcs"),
        sa.Column("default_location_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_item_definitions_category_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["item_kinds.id"],
            name="fk_item_definitions_kind_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["default_location_id"],
            ["locations.id"],
            name="fk_item_definitions_default_location_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_item_definitions_category_id", "item_definitions", ["category_id"])
    op.create_index("ix_item_definitions_kind_id", "item_definitions", ["kind_id"])


def downgrade() -> None:
    """Drop the item_definitions table."""
    op.drop_index("ix_item_definitions_kind_id", table_name="item_definitions")
    op.drop_index("ix_item_definitions_category_id", table_name="item_definitions")
    op.drop_table("item_definitions")
