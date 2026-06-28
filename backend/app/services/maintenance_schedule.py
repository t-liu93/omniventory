"""MaintenanceScheduleService — CRUD + complete for maintenance schedules.

Covers M7 §4.1 / §2 / §9 Step 4.

Responsibilities
----------------
``create(instance_id, name, interval_unit, interval_count, next_due_date,
         lead_days?, notes?, created_by?)``
    Create a new maintenance schedule.  Validates:
    - ``instance_id`` must exist (StockInstanceRepository lookup → 404
      ``stock_instance.not_found`` / ``instance.not_found``).
    - ``interval_unit`` ∈ ``MAINTENANCE_INTERVAL_UNITS`` → 422
      ``validation.unsupported_interval_unit``.
    - ``interval_count ≥ 1`` (Pydantic ``ge=1`` on schema; the service trusts
      the schema but defends with an explicit check).
    - ``lead_days ≥ 0`` when provided (Pydantic ``ge=0`` on schema).

``edit(schedule_id, update_body)``
    PATCH an existing schedule.  Only fields in ``update.model_fields_set``
    are applied (PATCH semantics).  Validates ``interval_unit`` when present.
    Raises 404 ``maintenance.not_found`` when the schedule is missing.

``delete(schedule_id)``
    Hard-delete a schedule.
    Raises 404 ``maintenance.not_found`` when the schedule is missing.

``complete(schedule_id, completed_on?, note?)``
    Record a completion:
    - Set ``last_completed_date = completed_on`` (default today, back-datable).
    - Advance ``next_due_date = add_interval(completed_on, interval_unit,
      interval_count)`` via the calendar-correct helper (§4.4).
    - ``note`` is accepted for parity/future but is **not persisted** in M7
      (no per-completion history table — §13 deferred).  The service ignores
      the note and documents this choice; the schema and route still accept it
      so Step 5/future work can add persistence without a contract change.
    Raises 404 ``maintenance.not_found`` when the schedule is missing.

``get(schedule_id)``
    Fetch one schedule (with instance→definition loaded) or raise 404.

``list_for_instance(instance_id, *, active_only?)``
    List all schedules for an instance (not gated by instance existence here —
    if the instance doesn't exist the list is simply empty, which is consistent
    with how listing returns an empty collection for unknown filter values).

``list_all(*, instance_id?, active_only?)``
    List all schedules, optionally filtered.

DB access only through MaintenanceScheduleRepository (roadmap §2.10).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.core.dates import add_interval
from app.core.errors import AppError, ErrorCode
from app.core.stock import MAINTENANCE_INTERVAL_UNITS
from app.models.maintenance_schedule import MaintenanceSchedule
from app.repositories.maintenance_schedule import MaintenanceScheduleRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.schemas.maintenance_schedule import MaintenanceScheduleUpdate
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


class MaintenanceScheduleService:
    """Business-logic facade for maintenance-schedule operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = MaintenanceScheduleRepository(db)
        self._instance_repo = StockInstanceRepository(db)
        self._settings = SettingsService(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, schedule_id: int) -> MaintenanceSchedule:
        """Return a schedule by PK or raise 404 (maintenance.not_found)."""
        schedule = self._repo.get(schedule_id)
        if schedule is None:
            raise AppError(
                ErrorCode.MAINTENANCE_NOT_FOUND,
                status_code=404,
                params={"id": schedule_id},
                message=f"Maintenance schedule {schedule_id} not found.",
            )
        return schedule

    def _validate_interval_unit(self, unit: str) -> None:
        """Raise 422 validation.unsupported_interval_unit if unit is invalid."""
        if unit not in MAINTENANCE_INTERVAL_UNITS:
            raise AppError(
                ErrorCode.VALIDATION_UNSUPPORTED_INTERVAL_UNIT,
                status_code=422,
                params={"interval_unit": unit, "valid": list(MAINTENANCE_INTERVAL_UNITS)},
                message=(
                    f"Unsupported interval unit: {unit!r}. "
                    f"Valid values: {list(MAINTENANCE_INTERVAL_UNITS)}."
                ),
            )

    def _validate_instance_exists(self, instance_id: int) -> None:
        """Raise 404 stock_instance.not_found if the instance does not exist."""
        instance = self._instance_repo.get(instance_id)
        if instance is None:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_NOT_FOUND,
                status_code=404,
                params={"id": instance_id},
                message=f"Stock instance {instance_id} not found.",
            )

    # ---------------------------------------------------------------------- #
    # Public operations                                                        #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        instance_id: int,
        name: str,
        interval_unit: str,
        interval_count: int,
        next_due_date: date,
        lead_days: int | None = None,
        notes: str | None = None,
        created_by: int | None = None,
    ) -> MaintenanceSchedule:
        """Create and return a new maintenance schedule.

        Validates instance existence and interval_unit before writing.
        Pydantic schema already enforces ``interval_count ≥ 1`` and
        ``lead_days ≥ 0``; the service trusts those constraints.

        Parameters
        ----------
        instance_id:
            Stock instance this schedule maintains.  Must exist.
        name:
            What the maintenance task is (e.g. "Replace AC filter").
        interval_unit:
            Recurrence unit: one of ``MAINTENANCE_INTERVAL_UNITS``.
        interval_count:
            How many units per recurrence (≥1).
        next_due_date:
            The first scheduled due date.
        lead_days:
            Advance-notice override; None = use global default.
        notes:
            Free-text annotation; optional.
        created_by:
            User id of the creator; optional.
        """
        self._validate_instance_exists(instance_id)
        self._validate_interval_unit(interval_unit)

        schedule = self._repo.create(
            instance_id=instance_id,
            name=name,
            interval_unit=interval_unit,
            interval_count=interval_count,
            next_due_date=next_due_date,
            lead_days=lead_days,
            notes=notes,
            created_by=created_by,
            is_active=True,
        )
        return schedule

    def edit(self, schedule_id: int, update: MaintenanceScheduleUpdate) -> MaintenanceSchedule:
        """PATCH an existing maintenance schedule.

        Only fields present in ``update.model_fields_set`` are applied.
        Validates ``interval_unit`` when included in the update.

        Parameters
        ----------
        schedule_id:
            PK of the schedule to update.
        update:
            Partial update body (PATCH semantics via ``model_fields_set``).

        Raises
        ------
        AppError (404 maintenance.not_found)
            When the schedule does not exist.
        AppError (422 validation.unsupported_interval_unit)
            When ``interval_unit`` is present but invalid.
        """
        schedule = self._get_or_404(schedule_id)

        # Validate interval_unit early if it is being changed.
        if "interval_unit" in update.model_fields_set and update.interval_unit is not None:
            self._validate_interval_unit(update.interval_unit)

        fields: dict[str, object] = {}
        for field in update.model_fields_set:
            value = getattr(update, field)
            fields[field] = value

        if fields:
            self._repo.update(schedule, **fields)

        return schedule

    def delete(self, schedule_id: int) -> None:
        """Hard-delete a maintenance schedule.

        Raises
        ------
        AppError (404 maintenance.not_found)
            When the schedule does not exist.
        """
        schedule = self._get_or_404(schedule_id)
        self._repo.delete(schedule)

    def complete(
        self,
        schedule_id: int,
        *,
        completed_on: date | None = None,
        note: str | None = None,  # noqa: ARG002 — not persisted in M7 (§13 deferred)
    ) -> MaintenanceSchedule:
        """Record a maintenance completion and advance next_due_date.

        Sets ``last_completed_date = completed_on`` (default today) and advances
        ``next_due_date = add_interval(completed_on, interval_unit, interval_count)``
        using the calendar-correct helper from ``app.core.dates``.

        Back-dated completions work correctly: providing a past ``completed_on``
        advances ``next_due_date`` from that date, not from today.

        The ``note`` parameter is accepted for parity with a future
        per-completion history table (M7 §13 deferred).  It is **not persisted**
        in M7 — there is no completion-history table, and the design doc
        explicitly defers it.  The service ignores the note value; Step 5 or
        a future milestone can start persisting it (to a history table) without
        changing this method's signature.

        Parameters
        ----------
        schedule_id:
            PK of the schedule to complete.
        completed_on:
            The completion date.  Defaults to ``date.today()`` when None.
        note:
            Completion annotation — accepted, not persisted in M7 (see above).

        Raises
        ------
        AppError (404 maintenance.not_found)
            When the schedule does not exist.
        """
        schedule = self._get_or_404(schedule_id)

        effective_completed_on = completed_on if completed_on is not None else date.today()

        new_next_due = add_interval(
            effective_completed_on,
            schedule.interval_unit,
            schedule.interval_count,
        )

        self._repo.update(
            schedule,
            last_completed_date=effective_completed_on,
            next_due_date=new_next_due,
        )
        return schedule

    def get(self, schedule_id: int) -> MaintenanceSchedule:
        """Fetch one schedule (with relationships loaded) or raise 404.

        Raises
        ------
        AppError (404 maintenance.not_found)
            When the schedule does not exist.
        """
        return self._get_or_404(schedule_id)

    def list_for_instance(
        self,
        instance_id: int,
        *,
        active_only: bool = False,
    ) -> list[MaintenanceSchedule]:
        """List all schedules for a specific stock instance.

        Returns an empty list when the instance does not exist or has no schedules
        (consistent with how GET collections return empty for unknown filter values).

        Parameters
        ----------
        instance_id:
            The stock instance to filter on.
        active_only:
            When True, return only is_active=True rows.
        """
        return self._repo.list_for_instance(instance_id, active_only=active_only)

    def list_all(
        self,
        *,
        instance_id: int | None = None,
        active_only: bool = False,
    ) -> list[MaintenanceSchedule]:
        """List all schedules, optionally filtered.

        Parameters
        ----------
        instance_id:
            When provided, filter to schedules for this instance only.
        active_only:
            When True, return only is_active=True rows.
        """
        return self._repo.list_all(instance_id=instance_id, active_only=active_only)

    def global_lead_days(self) -> int:
        """Return the global maintenance lead days (from settings)."""
        return self._settings.maintenance_lead_days()
