"""SQLAlchemy model for one-time user tokens (M6 Step 3).

Backs invitations (``purpose="invite"``) and admin-initiated password-reset
links (``purpose="password_reset"``).

Design notes
------------
- The raw token is **never stored**.  Only the sha256 hex digest
  (``token_hash``) is persisted.  A DB leak never exposes a live link.
- ``purpose`` is an app-validated string — no DB CHECK constraint (roadmap
  §2.11).
- ``consumed_at`` NULL means the token is still usable; once set it is
  permanently spent.
- Both FK columns use ``ondelete`` actions: ``user_id`` → CASCADE (invite
  token for a user account that was deleted is also gone), ``created_by`` →
  SET NULL (keep the token row but lose the issuer reference).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserToken(Base):
    """A one-time-use token row for invitations and password resets.

    Columns
    -------
    id              Auto-increment surrogate PK.
    purpose         ``"invite"`` or ``"password_reset"`` (app-validated).
    email           Invitee email (invites only; lower-cased). NULL for resets.
    role            Invited role (invites only). NULL for resets.
    user_id         FK → users.id; the target user (resets only). NULL for
                    invites.  CASCADE-deleted when the user is deleted.
    token_hash      sha256 hex of the raw token (64 chars, unique).  The raw
                    token is never stored.
    expires_at      Hard expiry; tokens past this time are rejected.
    consumed_at     NULL = still usable; set to the accept timestamp on use.
    created_by      FK → users.id; the admin who issued the token.  SET NULL
                    if that admin is later deleted.
    created_at      Row-creation timestamp (set by the DB server default).
    """

    __tablename__ = "user_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_user_tokens_token_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_user_tokens_user_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_user_tokens_created_by",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"UserToken(id={self.id!r}, purpose={self.purpose!r}, "
            f"email={self.email!r}, consumed_at={self.consumed_at!r})"
        )
