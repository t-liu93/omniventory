"""Create stock_instances table and add item_instance_id to locations.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-16 00:00:00.000000 UTC

Step 4 of M1 — this migration handles the circular-FK ordering problem
described in M1.md §3.6:

1. Create ``stock_instances`` first.  Its ``location_id`` FK → ``locations.id``
   is valid because ``locations`` already exists from migration 0004.

2. Add ``locations.item_instance_id`` (nullable, unique FK → ``stock_instances.id``)
   via Alembic **batch mode** (``with op.batch_alter_table("locations") as batch``).
   SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT`` directly; batch mode
   rebuilds the table transparently.

DB constraints in ``stock_instances``:
- ``CHECK (serial IS NULL OR quantity = 1)`` — the serial constraint.
- Partial unique index on ``(definition_id, serial) WHERE serial IS NOT NULL``.

Both upgrade and downgrade are fully reversible.  Downgrade batch-drops the
``item_instance_id`` column from ``locations`` first (avoiding the FK
dependency), then drops ``stock_instances``.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create stock_instances, then batch-alter locations to add item_instance_id."""
    # 1. Create stock_instances (location_id FK → locations is safe; locations exists).
    op.create_table(
        "stock_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("definition_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column(
            "quantity",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="1",
        ),
        sa.Column("serial", sa.String(255), nullable=True),
        sa.Column("model_number", sa.String(255), nullable=True),
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("warranty_expires", sa.Date(), nullable=True),
        sa.Column("warranty_details", sa.String(1000), nullable=True),
        sa.Column("purchase_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_source", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["item_definitions.id"],
            name="fk_stock_instances_definition_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_stock_instances_location_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "serial IS NULL OR quantity = 1",
            name="ck_stock_instances_serial_qty_1",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: same (definition_id, serial) rejected; NULLs coexist.
    op.create_index(
        "uq_stock_instances_definition_serial",
        "stock_instances",
        ["definition_id", "serial"],
        unique=True,
        sqlite_where=sa.text("serial IS NOT NULL"),
    )
    op.create_index("ix_stock_instances_definition_id", "stock_instances", ["definition_id"])
    op.create_index("ix_stock_instances_location_id", "stock_instances", ["location_id"])

    # 2. Add item_instance_id to locations via batch mode (SQLite cannot ALTER TABLE
    #    ADD CONSTRAINT directly — batch mode rebuilds the table).
    with op.batch_alter_table("locations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("item_instance_id", sa.Integer(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_locations_item_instance_id",
            "stock_instances",
            ["item_instance_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_locations_item_instance_id",
            ["item_instance_id"],
        )


def downgrade() -> None:
    """Batch-drop item_instance_id from locations, then drop stock_instances."""
    # 1. Remove item_instance_id from locations first (drops the FK dependency).
    with op.batch_alter_table("locations", schema=None) as batch_op:
        batch_op.drop_constraint("uq_locations_item_instance_id", type_="unique")
        batch_op.drop_constraint("fk_locations_item_instance_id", type_="foreignkey")
        batch_op.drop_column("item_instance_id")

    # 2. Drop stock_instances (FK from locations is gone now).
    op.drop_index("ix_stock_instances_location_id", table_name="stock_instances")
    op.drop_index("ix_stock_instances_definition_id", table_name="stock_instances")
    op.drop_index("uq_stock_instances_definition_serial", table_name="stock_instances")
    op.drop_table("stock_instances")
