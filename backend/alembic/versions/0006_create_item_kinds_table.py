"""Create item_kinds table and seed system kinds.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-16 00:00:00.000000 UTC

This migration creates the ``item_kinds`` lookup table and seeds it with the
three system kinds (``durable`` / ``consumable`` / ``perishable``), all with
``is_system = 1``.

``item_kinds``
    A small reference table so that ``kind`` on ``item_definitions`` is a real
    FK — not a baked-in string enum — chosen in M1 so that future references
    never need a breaking contract change (M1.md §2 / §3.3).

Seed is idempotent: uses SQLite-specific ``INSERT OR IGNORE`` so that
re-running the migration does not fail if the rows already exist (same pattern
as the M0 ``households`` seed in migration 0001).

Both upgrade and downgrade are fully reversible.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the item_kinds table and seed system kinds."""
    op.create_table(
        "item_kinds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_item_kinds_code"),
    )

    # Seed the three system kinds.  INSERT OR IGNORE is idempotent (SQLite).
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO item_kinds (code, name, is_system) "
            "VALUES "
            "('durable', 'Durable', 1), "
            "('consumable', 'Consumable', 1), "
            "('perishable', 'Perishable', 1)"
        )
    )


def downgrade() -> None:
    """Drop the item_kinds table (and the seeded rows with it)."""
    op.drop_table("item_kinds")
