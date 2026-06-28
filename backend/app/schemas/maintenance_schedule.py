"""Pydantic request/response schemas for maintenance-schedule endpoints (M7 Step 4).

Schemas are thin wire DTOs; business logic lives in the service layer.

MaintenanceScheduleResponse
    Public representation of a maintenance schedule.  Includes all DB columns
    plus server-computed derived fields:
    - ``instance_name``      — the durable instance's definition name (resolved
                               live from the joined instance.definition).
    - ``status``             — server-computed ``"overdue"`` / ``"due_soon"`` /
                               ``"ok"`` so the client needn't know the global lead.
    - ``effective_lead_days`` — resolved lead: ``lead_days ?? global default``.

    The ``status`` computation uses ``date.today()`` (server local date).  If a
    timezone-aware household-local date helper were available it would be
    preferred for consistency, but ``date.today()`` is correct for a
    single-timezone household (the setting introduces no skew for most users).

MaintenanceScheduleCreate
    Body for ``POST /maintenance-schedules``.

MaintenanceScheduleUpdate
    Body for ``PATCH /maintenance-schedules/{id}`` (PATCH semantics via
    ``model_fields_set``).

MaintenanceComplete
    Body for ``POST /maintenance-schedules/{id}/complete``.
    ``note`` is accepted in the schema for parity/future use but is **not
    persisted in M7** — there is no per-completion history table (M7 §13).
    This is documented here so reviewers understand the deliberate choice.
    The field is silently ignored by the service in M7; when the completion
    history table ships (§13) the service can start persisting it without a
    schema change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.models.maintenance_schedule import MaintenanceSchedule


class MaintenanceScheduleResponse(BaseModel):
    """Public representation of one maintenance schedule.

    Derived fields (not stored in the DB column):
    - ``instance_name``       Resolved from ``schedule.instance.definition.name``.
    - ``effective_lead_days`` ``schedule.lead_days`` if not None, else the global
                              ``reminders.maintenance.lead_days`` default.
    - ``status``              Server-computed from today, next_due_date, and
                              effective_lead_days:
                              - ``overdue``  : today > next_due_date
                              - ``due_soon`` : (next_due_date - lead) <= today <= next_due_date
                              - ``ok``       : today < (next_due_date - lead)

    Use ``MaintenanceScheduleResponse.from_schedule(schedule, global_lead)`` to
    build this from an ORM object with instance and definition already loaded.
    """

    id: int
    instance_id: int
    instance_name: str
    name: str
    interval_unit: str
    interval_count: int
    next_due_date: date
    lead_days: int | None
    effective_lead_days: int
    last_completed_date: date | None
    notes: str | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    status: Literal["overdue", "due_soon", "ok"]

    @classmethod
    def from_schedule(
        cls,
        schedule: MaintenanceSchedule,
        global_lead: int,
    ) -> MaintenanceScheduleResponse:
        """Build a response from a MaintenanceSchedule ORM object.

        The ``instance`` and ``instance.definition`` relationships must be
        loaded (either eagerly via joinedload or already accessed) before
        calling this.

        Parameters
        ----------
        schedule:
            The ORM object.  ``schedule.instance.definition`` must be loaded.
        global_lead:
            The global ``reminders.maintenance.lead_days`` default, for
            resolving ``effective_lead_days`` when ``schedule.lead_days`` is None.
        """
        from datetime import timedelta

        effective_lead = schedule.lead_days if schedule.lead_days is not None else global_lead
        today = date.today()
        ndd = schedule.next_due_date
        window_start = ndd - timedelta(days=effective_lead)

        if today > ndd:
            status: Literal["overdue", "due_soon", "ok"] = "overdue"
        elif window_start <= today:
            status = "due_soon"
        else:
            status = "ok"

        # Resolve instance name from the joined definition.
        instance_name: str = ""
        if schedule.instance is not None and schedule.instance.definition is not None:
            instance_name = schedule.instance.definition.name

        return cls(
            id=schedule.id,
            instance_id=schedule.instance_id,
            instance_name=instance_name,
            name=schedule.name,
            interval_unit=schedule.interval_unit,
            interval_count=schedule.interval_count,
            next_due_date=schedule.next_due_date,
            lead_days=schedule.lead_days,
            effective_lead_days=effective_lead,
            last_completed_date=schedule.last_completed_date,
            notes=schedule.notes,
            is_active=schedule.is_active,
            created_by=schedule.created_by,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
            status=status,
        )


class MaintenanceScheduleCreate(BaseModel):
    """Body for POST /maintenance-schedules.

    Required fields: ``instance_id``, ``name``, ``interval_unit``,
    ``interval_count``, ``next_due_date``.  Optional: ``lead_days``,
    ``notes``.

    Validation
    ----------
    - ``interval_count ≥ 1`` (Pydantic ``ge=1``).
    - ``lead_days ≥ 0`` (Pydantic ``ge=0``) when provided.
    - ``interval_unit`` is validated app-layer against
      ``MAINTENANCE_INTERVAL_UNITS`` by the service; a Pydantic failure there
      would produce ``validation.invalid_input``, but the *stable* error code
      for this field is ``validation.unsupported_interval_unit`` (422) which is
      raised as an ``AppError`` by the service layer after receiving any value.
    """

    instance_id: int
    name: str = Field(max_length=255)
    interval_unit: str = Field(max_length=8)
    interval_count: int = Field(ge=1)
    next_due_date: date
    lead_days: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class MaintenanceScheduleUpdate(BaseModel):
    """Body for PATCH /maintenance-schedules/{id}.

    PATCH semantics: only fields present in the request body are applied
    (checked via ``model_fields_set`` in the service layer).  Fields absent
    from the body leave the row unchanged.

    All fields are optional (None = not provided / leave unchanged).

    Validation
    ----------
    - ``interval_count ≥ 1`` when provided.
    - ``lead_days ≥ 0`` when provided.
    - ``interval_unit`` validated app-layer by the service.
    """

    name: str | None = Field(default=None, max_length=255)
    interval_unit: str | None = Field(default=None, max_length=8)
    interval_count: int | None = Field(default=None, ge=1)
    next_due_date: date | None = None
    lead_days: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None


class MaintenanceComplete(BaseModel):
    """Body for POST /maintenance-schedules/{id}/complete.

    Parameters
    ----------
    completed_on:
        The completion date.  Defaults to today when omitted.  Back-datable:
        providing a past date advances ``next_due_date`` from that date.
    note:
        An optional completion annotation.  **Not persisted in M7** — there
        is no per-completion history table (M7 §13 deferred).  The field is
        accepted in the schema for parity with future history-table work;
        the service intentionally ignores it and documents this choice.
        When the completion history table ships, the service can start
        persisting it without a schema change.
    """

    completed_on: date | None = None
    note: str | None = Field(default=None, max_length=1000)
