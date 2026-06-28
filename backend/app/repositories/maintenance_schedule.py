"""Repository for the MaintenanceSchedule table (M7 §4.1 / §9 Step 4).

All DB access to the ``maintenance_schedules`` table goes through this class.
Route handlers and services must not issue raw queries; they call
``MaintenanceScheduleRepository`` methods (roadmap §2.10).

Public methods
--------------
``create(...)``
    Insert a new MaintenanceSchedule row and flush.

``get(schedule_id)``
    Return a MaintenanceSchedule by PK (with instance→definition joinedloaded),
    or None.

``list_for_instance(instance_id, *, active_only)``
    Return all schedules for one stock instance.

``list_all(*, instance_id, active_only)``
    Return schedules across all instances, optionally filtered.

``list_active()``
    Return all is_active=True schedules with instance and instance.definition
    joinedloaded for the engine pass (Step 5).  No horizon parameter — the
    engine applies the due-window in Python so no long-lead schedule is
    silently dropped (M7 §4.1 / §4.5 rationale).

``update(schedule, **fields)``
    Apply field updates to an existing row and flush.

``delete(schedule)``
    Delete a MaintenanceSchedule row and flush.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.maintenance_schedule import MaintenanceSchedule
from app.models.stock_instance import StockInstance


class MaintenanceScheduleRepository:
    """Data-access object for the maintenance_schedules table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, schedule_id: int) -> MaintenanceSchedule | None:
        """Return a MaintenanceSchedule by PK with instance→definition loaded, or None."""
        stmt = (
            select(MaintenanceSchedule)
            .options(joinedload(MaintenanceSchedule.instance).joinedload(StockInstance.definition))
            .where(MaintenanceSchedule.id == schedule_id)
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_for_instance(
        self,
        instance_id: int,
        *,
        active_only: bool = False,
    ) -> list[MaintenanceSchedule]:
        """Return all schedules for a specific stock instance.

        Parameters
        ----------
        instance_id:
            The instance to filter on.
        active_only:
            When True, return only ``is_active=True`` rows.
        """
        stmt = (
            select(MaintenanceSchedule)
            .options(joinedload(MaintenanceSchedule.instance).joinedload(StockInstance.definition))
            .where(MaintenanceSchedule.instance_id == instance_id)
            .order_by(MaintenanceSchedule.next_due_date.asc(), MaintenanceSchedule.id.asc())
        )
        if active_only:
            stmt = stmt.where(MaintenanceSchedule.is_active.is_(True))
        return list(self._db.execute(stmt).scalars().unique().all())

    def list_all(
        self,
        *,
        instance_id: int | None = None,
        active_only: bool = False,
    ) -> list[MaintenanceSchedule]:
        """Return schedules across all instances, optionally filtered.

        Parameters
        ----------
        instance_id:
            When provided, filter to schedules for this instance only.
        active_only:
            When True, return only ``is_active=True`` rows.
        """
        stmt = (
            select(MaintenanceSchedule)
            .options(joinedload(MaintenanceSchedule.instance).joinedload(StockInstance.definition))
            .order_by(MaintenanceSchedule.next_due_date.asc(), MaintenanceSchedule.id.asc())
        )
        if instance_id is not None:
            stmt = stmt.where(MaintenanceSchedule.instance_id == instance_id)
        if active_only:
            stmt = stmt.where(MaintenanceSchedule.is_active.is_(True))
        return list(self._db.execute(stmt).scalars().unique().all())

    def list_active(self) -> list[MaintenanceSchedule]:
        """Return all is_active=True schedules with instance→definition joinedloaded.

        Used by the reminder engine (Step 5) to retrieve all candidate schedules
        for the maintenance evaluation pass.  The due-window check (today ≥
        next_due_date − lead) is applied in Python — **no scalar horizon** is
        accepted because a per-schedule ``lead_days`` is unbounded and any fixed
        DB-side horizon would silently drop a long-lead schedule whose window is
        already open (M7 §4.1 / §4.5 rationale).

        The ``joinedload`` on ``instance.definition`` avoids N+1 queries when
        the engine resolves ``instance_name`` and the responsible party.
        """
        stmt = (
            select(MaintenanceSchedule)
            .options(joinedload(MaintenanceSchedule.instance).joinedload(StockInstance.definition))
            .where(MaintenanceSchedule.is_active.is_(True))
            .order_by(MaintenanceSchedule.next_due_date.asc(), MaintenanceSchedule.id.asc())
        )
        return list(self._db.execute(stmt).scalars().unique().all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        instance_id: int,
        name: str,
        interval_unit: str,
        interval_count: int,
        next_due_date: object,  # datetime.date
        lead_days: int | None = None,
        notes: str | None = None,
        created_by: int | None = None,
        is_active: bool = True,
    ) -> MaintenanceSchedule:
        """Insert a new MaintenanceSchedule row and flush.

        The caller (service layer) is responsible for validating ``interval_unit``
        against ``MAINTENANCE_INTERVAL_UNITS`` and ``interval_count ≥ 1`` before
        calling this method.
        """
        schedule = MaintenanceSchedule(
            instance_id=instance_id,
            name=name,
            interval_unit=interval_unit,
            interval_count=interval_count,
            next_due_date=next_due_date,
            lead_days=lead_days,
            notes=notes,
            created_by=created_by,
            is_active=is_active,
        )
        self._db.add(schedule)
        self._db.flush()
        return schedule

    def update(self, schedule: MaintenanceSchedule, **fields: object) -> MaintenanceSchedule:
        """Apply field updates to an existing MaintenanceSchedule and flush.

        Only the keys present in ``fields`` are updated.  Pass keyword
        arguments for each column you want to change.

        SQLAlchemy's ``onupdate=func.now()`` on ``updated_at`` ensures the
        timestamp is refreshed when the row is flushed.
        """
        for key, value in fields.items():
            setattr(schedule, key, value)
        self._db.flush()
        return schedule

    def delete(self, schedule: MaintenanceSchedule) -> None:
        """Delete a MaintenanceSchedule row and flush."""
        self._db.delete(schedule)
        self._db.flush()
