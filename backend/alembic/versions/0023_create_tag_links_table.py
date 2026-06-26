"""Create tag_links table.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 2 — polymorphic tag-to-owner join table.

``tag_links`` is the polymorphic join between a ``tags`` row and an owner
entity identified by ``(model_type, model_id)``.  No hard FK on ``model_id``
(it can reference any of the allowed owner tables); existence and cascade are
enforced in the service layer.

``tag_id`` FK has ``ondelete=CASCADE``: deleting a tag row drops all its links
automatically at the DB level.

The unique constraint ``(tag_id, model_type, model_id)`` prevents tagging the
same owner twice with the same tag.  The index on ``(model_type, model_id)``
supports the primary "list tags for this owner" access pattern.

See M5.md §3.3 for the full schema rationale.

Migration is fully reversible: upgrade creates the table + indexes, downgrade
drops the table.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the tag_links table with indexes."""
    op.create_table(
        "tag_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey(
                "tags.id",
                name="fk_tag_links_tag_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("model_type", sa.String(32), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tag_id", "model_type", "model_id", name="uq_tag_links_tag_owner"),
    )

    # Index for listing all tags attached to an owner (the primary access pattern).
    op.create_index(
        "ix_tag_links_owner",
        "tag_links",
        ["model_type", "model_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the tag_links table and its indexes."""
    op.drop_index("ix_tag_links_owner", table_name="tag_links")
    op.drop_table("tag_links")
