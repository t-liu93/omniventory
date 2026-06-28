"""SQLAlchemy model for the AuditLog table (M6 Step 6).

Append-only security/admin event log.  The application never updates or
deletes rows from this table; the repository exposes only ``append`` + ``list``.

Design notes
------------
- ``actor_user_id`` FK → users.id with ``ondelete=SET NULL`` so that historical
  audit rows survive the deletion of the actor.
- ``actor_email`` is a **denormalized snapshot** taken at write time.  This
  makes failed-login rows (no actor user) and post-deletion rows still readable
  without joining users.
- ``target_id`` carries **no hard FK** — it is polymorphic (could point to a
  user id, an invitation id, etc.) and must survive target-row deletion.
- ``params`` stores a JSON-encoded ``dict`` with event-specific structured
  detail (e.g. ``{"old_role": "member", "new_role": "admin"}``).
- Three non-unique indexes cover the common query patterns:
  ``ix_audit_log_created_at``, ``ix_audit_log_event_type``,
  ``ix_audit_log_actor_user_id``.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """An append-only security/admin audit-log row.

    Columns
    -------
    id              Auto-increment surrogate PK.
    event_type      Stable event identifier, e.g. ``auth.login_failed``.
    actor_user_id   FK → users.id (SET NULL on delete).  NULL for anonymous
                    events (failed logins, invitation accepts).
    actor_email     Denormalized email snapshot so the row reads sensibly even
                    after the actor is deleted or when no actor exists.
    target_type     Polymorphic target kind: ``"user"`` / ``"invitation"`` /
                    ``"setting"`` / ``None``.
    target_id       PK of the affected entity (no hard FK — polymorphic).
    params          JSON object string with structured event detail (nullable).
    ip_address      Client IP at the time of the event (nullable).
    created_at      Row-creation timestamp (UTC, set by DB server default).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_audit_log_actor_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    actor_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id!r}, event_type={self.event_type!r}, "
            f"actor_email={self.actor_email!r})"
        )
