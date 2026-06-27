"""Create user_tokens table.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-27 00:00:00.000000 UTC

M6 Step 3 — one-time tokens for invitations and admin password resets.

``user_tokens`` stores short-lived, single-use tokens that back:
- ``purpose="invite"``         — a pending invitation to join the household.
- ``purpose="password_reset"`` — an admin-initiated password-reset link.

The raw token is returned to the caller exactly once and never stored; only
the sha256 hex digest (``token_hash``, 64 chars) is persisted.  A DB leak
therefore never exposes a live link.

Columns
-------
id              Integer PK.
purpose         String(32) NOT NULL — ``invite`` or ``password_reset``
                (app-validated; no DB CHECK — roadmap §2.11).
email           String(254) nullable — the invitee email (invites only).
role            String(64) nullable — the invited role (invites only).
user_id         FK → users.id nullable, ondelete=CASCADE — the target user
                (password_reset only).
token_hash      String(64) NOT NULL UNIQUE (``uq_user_tokens_token_hash``) —
                sha256 hex of the raw token.
expires_at      DateTime(tz) NOT NULL — hard expiry.
consumed_at     DateTime(tz) nullable — NULL = still usable; set on accept.
created_by      FK → users.id nullable, ondelete=SET NULL — the admin who
                issued the token.
created_at      DateTime(tz) NOT NULL server_default now().

Indexes
-------
``uq_user_tokens_token_hash``   Unique constraint / index on token_hash.
``ix_user_tokens_email``        Non-unique index on email for
                                ``get_pending_invite_by_email`` lookups.

Migration is fully reversible: upgrade creates the table, downgrade drops it.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the user_tokens table."""
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("role", sa.String(64), nullable=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_user_tokens_user_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_user_tokens_created_by",
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
        sa.UniqueConstraint("token_hash", name="uq_user_tokens_token_hash"),
    )
    op.create_index("ix_user_tokens_email", "user_tokens", ["email"], unique=False)


def downgrade() -> None:
    """Drop the user_tokens table."""
    op.drop_index("ix_user_tokens_email", table_name="user_tokens")
    op.drop_table("user_tokens")
