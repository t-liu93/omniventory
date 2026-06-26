"""Create attachments table.

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 1 — polymorphic attachment references with ref-counting.

``attachments`` is the polymorphic join table: one row is one reference from an
owner entity ``(model_type, model_id)`` to a ``media_files`` row.  The same
``media_files`` row may be referenced by multiple attachment rows (de-dup /
shared content).  Reference count = COUNT(attachments WHERE media_file_id = x).

No hard FK on ``model_id`` — the owner is polymorphic (item_definition,
stock_instance, location).  Existence and cascade are enforced in the service
layer.

See M5.md §3.2 for the full schema rationale.

Migration is fully reversible: upgrade creates the table + indexes, downgrade
drops the table.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the attachments table with indexes."""
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "media_file_id",
            sa.Integer(),
            sa.ForeignKey(
                "media_files.id",
                name="fk_attachments_media_file_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("model_type", sa.String(32), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "uploaded_by",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_attachments_uploaded_by",
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
    )

    # Index for listing an owner's attachments (the primary access pattern).
    op.create_index(
        "ix_attachments_owner",
        "attachments",
        ["model_type", "model_id"],
        unique=False,
    )

    # Index for ref-count queries (COUNT WHERE media_file_id = x).
    op.create_index(
        "ix_attachments_media_file_id",
        "attachments",
        ["media_file_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the attachments table and its indexes."""
    op.drop_index("ix_attachments_media_file_id", table_name="attachments")
    op.drop_index("ix_attachments_owner", table_name="attachments")
    op.drop_table("attachments")
