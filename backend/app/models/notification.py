"""SQLAlchemy model for the Notification table (M4 §3.2).

A ``notification`` is the unified in-app inbox record, dedup ledger entry, and
(for low-stock sources) episode record.  Every reminder the engine fires — for
best_before, warranty, or low_stock — lands here as exactly one row.

Design notes
------------
- ``user_id`` FK → ``users.id`` with ``ondelete=CASCADE``: deleting a user
  removes all their notifications.
- ``subject_id`` carries NO hard FK (§3.2 rationale: survives lot/definition
  deletion as a historical record; the engine checks existence before acting).
- ``dedup_key`` is unique together with ``user_id`` (see
  ``uq_notifications_user_dedup``); the engine uses this to make rescans a
  no-op.
- ``params`` is a JSON-encoded text blob that the frontend uses to localise the
  ``message_code`` template.  Never rendered server-side for in-app (only for
  external channels in Phase C).
- Low-stock episode columns (``episode_started_on``, ``offset_days``,
  ``resolved_at``) are NULL for date sources (best_before / warranty).  They are
  used by the low-stock evaluator in Step 4.
- ``read_at`` is NULL while unread; stamped on mark-read (Step 6).
- ``dismissed_at`` is NULL while visible in the inbox; stamped on soft-dismiss
  (notification hygiene hardening round, Step 1).  Dismiss hides a row from the
  inbox ONLY — it is deliberately invisible to ``dismissed_at`` in the dedup
  lookup (``_get_by_dedup`` / ``create_if_absent``) and the low-stock episode
  helpers (``open_low_stock_opener`` et al.), so a dismissed row still anchors
  its dedup key and still holds live episode state.  See
  ``app/repositories/notification.py`` module docstring for the full contract.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Notification(Base):
    """An in-app notification row (inbox entry + dedup key + episode record).

    Columns
    -------
    id                  Auto-increment surrogate PK.
    user_id             FK → users.id (CASCADE on delete). The notification recipient.
    source              ``best_before`` / ``warranty`` / ``low_stock``.
    subject_type        ``instance`` (date sources) / ``definition`` (low-stock).
    subject_id          PK of the referenced lot or definition (no hard FK).
    dedup_key           Idempotency key; unique with ``user_id``.
    message_code        i18n code (e.g. ``reminder.best_before``).
    params              JSON render params (nullable).
    episode_started_on  Low-stock only: the episode anchor date.  NULL for date sources.
    offset_days         Low-stock only: which repeat offset this row is (0 = opener).
                        NULL for date sources.
    resolved_at         Low-stock only: stamped when the definition recovers.
                        NULL = open / not applicable.
    read_at             In-app read state.  NULL = unread.
    dismissed_at        In-app soft-dismiss state.  NULL = visible in the inbox;
                        stamped = hidden from the inbox.  Independent of
                        ``read_at`` (a dismissed row may be read or unread).
    created_at          Row-creation timestamp (DB server default).
    """

    __tablename__ = "notifications"

    __table_args__ = (
        # Unique idempotency constraint: one notification per (user, dedup_key).
        Index("uq_notifications_user_dedup", "user_id", "dedup_key", unique=True),
        # Non-unique index for unread-count / inbox queries.
        Index("ix_notifications_user_read_at", "user_id", "read_at", unique=False),
        # No index on dismissed_at: household scale, and the inbox query is
        # already user-scoped + limited, so a full index is not warranted here.
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_notifications_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    message_code: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Low-stock episode columns (NULL for date sources)
    episode_started_on: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    offset_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # In-app read state
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # In-app soft-dismiss state (notification hygiene hardening round, Step 1)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship to User (lazy; used when needed, not eager-loaded by default)
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"Notification(id={self.id!r}, user_id={self.user_id!r}, "
            f"source={self.source!r}, dedup_key={self.dedup_key!r})"
        )
