"""Create audit_log table.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-28 00:00:00.000000 UTC

M6 Step 6 — append-only security/admin event log.

``audit_log`` records every security-relevant and admin action (login success /
failure / logout, role change, user deactivation / deletion, password change /
reset, invitation issued / accepted / revoked, settings change).

Design notes
------------
- Append-only: the app never updates or deletes rows from this table.
- ``actor_user_id`` FK → users.id uses ``ondelete=SET NULL`` so that deleting
  a user preserves their historical audit rows (the ``actor_email`` snapshot
  keeps them readable).
- ``target_id`` carries no hard FK — it is polymorphic (could point to a user
  id, an invitation id, etc.) and must survive target-row deletion.
- ``params`` is a JSON text blob with event-specific structured detail.
- Three indexes cover the common query patterns: newest-first list, filter by
  event type, filter by actor.

Upgrade: plain ``op.create_table`` (reversible).
Downgrade: plain ``op.drop_table``.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the audit_log table with three non-unique indexes."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_audit_log_actor_user_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(254), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("params", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])


def downgrade() -> None:
    """Drop the audit_log table and its indexes."""
    op.drop_index("ix_audit_log_actor_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
