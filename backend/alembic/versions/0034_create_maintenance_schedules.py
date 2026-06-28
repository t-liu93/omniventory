"""Create maintenance_schedules table.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-28 00:00:00.000000 UTC

M7 Step 4 — recurring maintenance tasks on durable stock instances.

``maintenance_schedules`` stores user-defined recurring maintenance tasks
(e.g. "Replace AC filter every 3 months") attached to a specific stock
instance.  Completion records the last completion date and rolls forward
``next_due_date`` by the configured interval.

Columns
-------
id               Integer PK.
instance_id      FK → stock_instances.id (ondelete=CASCADE); the durable being
                 maintained.  Schedule dies with the instance.
name             String(255) NOT NULL — what the task is.
interval_unit    String(8) NOT NULL — ``day`` / ``week`` / ``month`` / ``year``.
                 App-validated against ``MAINTENANCE_INTERVAL_UNITS``; no DB
                 CHECK (roadmap §2.11).
interval_count   Integer NOT NULL — how many units per recurrence (≥1, Pydantic-
                 validated in the service/schema layer).
next_due_date    Date NOT NULL — the upcoming scheduled date.
lead_days        Integer NULL — advance-notice override (≥0).  NULL means inherit
                 the global ``reminders.maintenance.lead_days`` setting.
last_completed_date
                 Date NULL — when last completed (NULL = never done).
notes            String(1000) NULL — free-text annotation.
is_active        Boolean NOT NULL server_default=true — False = paused (engine
                 skips it; kept for history).
created_by       FK → users.id (ondelete=SET NULL) NULL — author.
created_at       DateTime(tz) NOT NULL server_default=now().
updated_at       DateTime(tz) NOT NULL server_default=now(); refreshed on update.

Indexes
-------
ix_maintenance_schedules_instance_id
    Non-unique on (instance_id) — instance-detail listing and cascade lookup.
ix_maintenance_schedules_next_due_date
    Non-unique on (next_due_date) — the engine's due-window scan.
ix_maintenance_schedules_is_active
    Non-unique on (is_active) — filter active vs paused schedules.

Migration is fully reversible: upgrade creates the table and indexes;
downgrade drops the indexes then the table.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the maintenance_schedules table and its indexes."""
    op.create_table(
        "maintenance_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "instance_id",
            sa.Integer(),
            sa.ForeignKey(
                "stock_instances.id",
                name="fk_maintenance_schedules_instance_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("interval_unit", sa.String(8), nullable=False),
        sa.Column("interval_count", sa.Integer(), nullable=False),
        sa.Column("next_due_date", sa.Date(), nullable=False),
        sa.Column("lead_days", sa.Integer(), nullable=True),
        sa.Column("last_completed_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_maintenance_schedules_created_by",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Non-unique indexes for common filter/scan operations.
    op.create_index(
        "ix_maintenance_schedules_instance_id",
        "maintenance_schedules",
        ["instance_id"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_schedules_next_due_date",
        "maintenance_schedules",
        ["next_due_date"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_schedules_is_active",
        "maintenance_schedules",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the maintenance_schedules table and its indexes."""
    op.drop_index("ix_maintenance_schedules_is_active", table_name="maintenance_schedules")
    op.drop_index("ix_maintenance_schedules_next_due_date", table_name="maintenance_schedules")
    op.drop_index("ix_maintenance_schedules_instance_id", table_name="maintenance_schedules")
    op.drop_table("maintenance_schedules")
