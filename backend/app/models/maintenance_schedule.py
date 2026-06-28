"""SQLAlchemy model for the MaintenanceSchedule table (M7 §3.2).

A ``maintenance_schedule`` is a recurring maintenance task attached to a
specific durable stock instance (e.g. "Replace AC filter every 3 months on
this specific air conditioner").

Design notes
------------
- ``instance_id`` FK → ``stock_instances.id`` with ``ondelete=CASCADE``:
  deleting an instance removes all its schedules.  Non-nullable — every
  schedule belongs to exactly one instance.
- ``interval_unit`` is validated app-layer against ``MAINTENANCE_INTERVAL_UNITS``
  (``'day'``, ``'week'``, ``'month'``, ``'year'``); no DB CHECK (roadmap §2.11).
- ``interval_count`` ≥ 1 is Pydantic-validated in the schema layer.
- ``next_due_date`` is the upcoming scheduled date that the reminder engine scans.
- ``lead_days`` NULL means inherit the global ``reminders.maintenance.lead_days``
  setting (default 7); a non-NULL value overrides the global default for this
  specific schedule.
- ``last_completed_date`` NULL = never completed.  Completion sets this field and
  advances ``next_due_date`` via ``add_interval`` — no per-completion history
  table (M7 §13 deferred).
- ``is_active`` False = paused (engine skips; kept for history).  Schedules are
  deactivated rather than deleted when you want to suspend without losing history.
- ``created_by`` FK → ``users.id`` with ``ondelete=SET NULL``: deleting a user
  NULLs the author field but keeps the schedule.
- Indexes:
    ``ix_maintenance_schedules_instance_id``  — instance-detail listing.
    ``ix_maintenance_schedules_next_due_date`` — engine due-window scan.
    ``ix_maintenance_schedules_is_active``    — active/paused filter.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.stock_instance import StockInstance
    from app.models.user import User

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MaintenanceSchedule(Base):
    """A recurring maintenance task on a durable stock instance.

    Columns
    -------
    id                  Auto-increment surrogate PK.
    instance_id         FK → stock_instances.id (CASCADE); non-nullable.
    name                Task name (e.g. "Replace AC filter").
    interval_unit       ``day`` / ``week`` / ``month`` / ``year``.  App-validated.
    interval_count      How many units per recurrence (≥1, schema-validated).
    next_due_date       The next upcoming due date (the engine's scan target).
    lead_days           Advance-notice override (≥0); NULL = global default.
    last_completed_date When last completed; NULL = never.
    notes               Free-text annotation; nullable.
    is_active           True = active (default); False = paused / skipped by engine.
    created_by          FK → users.id (SET NULL); the user who created the schedule.
    created_at          Row-creation timestamp (UTC, set by DB on insert).
    updated_at          Last-update timestamp (UTC); refreshed on every ORM flush
                        that modifies the row via ``onupdate=func.now()``.
    """

    __tablename__ = "maintenance_schedules"

    __table_args__ = (
        # Non-unique index for instance-detail listing and cascade lookups.
        Index("ix_maintenance_schedules_instance_id", "instance_id"),
        # Non-unique index for the engine's due-window scan.
        Index("ix_maintenance_schedules_next_due_date", "next_due_date"),
        # Non-unique index for active/paused filter.
        Index("ix_maintenance_schedules_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "stock_instances.id",
            name="fk_maintenance_schedules_instance_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    last_completed_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    created_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_maintenance_schedules_created_by",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship to StockInstance — joinedloaded by the repository when
    # listing schedules so the service can resolve instance_name and the
    # engine can route via the instance's responsible party.
    instance: Mapped[StockInstance] = relationship(
        "StockInstance",
        foreign_keys=[instance_id],
        lazy="select",
    )

    # Relationship to User (author) — lazy by default.
    creator: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"MaintenanceSchedule(id={self.id!r}, instance_id={self.instance_id!r}, "
            f"name={self.name!r}, next_due_date={self.next_due_date!r}, "
            f"is_active={self.is_active!r})"
        )
