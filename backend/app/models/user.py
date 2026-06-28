"""SQLAlchemy model for User accounts.

M0 bootstraps exactly one admin user.  Multi-user (invitations, roles beyond
"is active admin") is deferred to M6.

Design notes
------------
- ``password_hash`` stores the argon2 hash via ``app.auth.passwords``.
  Plaintext passwords are never stored.
- ``role`` is a freeform string now (e.g. ``"admin"``); a proper enum / role
  table comes in M6.
- ``is_active`` lets an admin deactivate an account without deleting it.
- ``created_at`` is filled by the DB server default.
- ``preferred_language`` is nullable; NULL means "never explicitly chosen" and
  the client falls back to its own resolution chain (localStorage → navigator → en).
  Added in M1.5 Step 2.
- ``notify_in_app`` / ``notify_email_digest`` — per-user channel opt-outs
  (M6 Step 5).  Both default to True (= M4 behaviour preserved).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    """A user account in the household.

    Columns
    -------
    id                                  Auto-increment surrogate PK.
    email                               Unique login identifier; lower-cased on write.
    password_hash                       Argon2 hash via ``app.auth.passwords.hash_password``.
    role                                Role label (``"admin"`` in M0); expanded in M6.
    is_active                           False → account is disabled; login is rejected.
    created_at                          Row-creation timestamp (UTC, set by DB on insert).
    preferred_language                  BCP-47 language code chosen by the user (nullable).
                                        NULL = "never explicitly chosen" → client resolves.
                                        M1.5 values: ``'en'`` / ``'zh'``.
    reminder_best_before_lead_days      Per-user best-before lead-time override (M4). ``≥ 0``
                                        (Pydantic-validated). NULL = inherit global default
                                        (§4.3 resolution chain: per-item > per-user > global).
    reminder_warranty_lead_days         Per-user warranty-expiry lead-time override (M4). ``≥ 0``
                                        (Pydantic-validated). NULL = inherit global default.
    notify_in_app                       True → include this user in the in-app notification
                                        inbox (default).  False → the inbox returns [] for this
                                        user; rows may still be created to feed the email digest.
                                        M6 Step 5.
    notify_email_digest                 True → include this user in the daily email digest
                                        (default).  False → the email channel skips building /
                                        sending the digest for this user.  M6 Step 5.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    preferred_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reminder_best_before_lead_days: Mapped[int | None] = mapped_column(nullable=True, default=None)
    reminder_warranty_lead_days: Mapped[int | None] = mapped_column(nullable=True, default=None)
    notify_in_app: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )
    notify_email_digest: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    # Back-reference to sessions (lazy-loaded on demand).
    sessions: Mapped[list["Session"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!r})"
