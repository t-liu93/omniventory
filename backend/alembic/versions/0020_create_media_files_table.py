"""Create media_files table.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-26 00:00:00.000000 UTC

M5 Step 1 — content-addressed media file registry.

``media_files`` is the physical-file registry.  One row per unique byte-content
(keyed by sha256 hash).  The on-disk path is derived from the hash:
``DATA_DIR/media/<sha256[:2]>/<sha256>`` (sharded, no extension — type is in the
row).

See M5.md §3.1 for the full schema rationale.

Migration is fully reversible: upgrade creates the table + unique index, downgrade
drops the table.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the media_files table."""
    op.create_table(
        "media_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Unique constraint: one row per unique sha256 hash.
    op.create_index(
        "uq_media_files_sha256",
        "media_files",
        ["sha256"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the media_files table and its indexes."""
    op.drop_index("uq_media_files_sha256", table_name="media_files")
    op.drop_table("media_files")
