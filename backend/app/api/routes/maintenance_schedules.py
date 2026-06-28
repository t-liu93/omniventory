"""Maintenance schedule endpoints (M7 Step 4).

All endpoints require a valid session.  Reads require VIEW (any authenticated
user); mutations require EDIT (member or admin).

Routes (all under the api_prefix, e.g. /api):
    GET    /maintenance-schedules?instance_id=&active=
                                              List schedules (VIEW).
    POST   /maintenance-schedules             Create a schedule (EDIT).
    GET    /maintenance-schedules/{id}        One schedule (VIEW).
    PATCH  /maintenance-schedules/{id}        Edit (EDIT).
    DELETE /maintenance-schedules/{id}        Delete (EDIT).
    POST   /maintenance-schedules/{id}/complete
                                              Record completion; advance next_due (EDIT).

Instance-scoped convenience route (registered separately in instances router):
    GET    /instances/{id}/maintenance-schedules  Schedules for one instance (VIEW).

Error contract:
    401  No/invalid session.
    403  Insufficient role (auth.forbidden) — viewer on mutations.
    404  Schedule not found (maintenance.not_found) on GET/PATCH/DELETE/complete.
    404  Stock instance not found (stock_instance.not_found) on POST with bad instance_id.
    422  Invalid interval_unit (validation.unsupported_interval_unit).
    422  Invalid interval_count or lead_days (Pydantic → validation.invalid_input).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_edit, require_view
from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.schemas.maintenance_schedule import (
    MaintenanceComplete,
    MaintenanceScheduleCreate,
    MaintenanceScheduleResponse,
    MaintenanceScheduleUpdate,
)
from app.services.maintenance_schedule import MaintenanceScheduleService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(
    prefix="/maintenance-schedules",
    tags=["maintenance-schedules"],
    responses=_ERROR_RESPONSES,
)


def _get_service(db: Annotated[Session, Depends(get_db)]) -> MaintenanceScheduleService:
    """Dependency: build and return a MaintenanceScheduleService."""
    return MaintenanceScheduleService(db)


def _to_response(
    service: MaintenanceScheduleService,
    schedule: object,
) -> MaintenanceScheduleResponse:
    """Build a response from a schedule ORM object using the current global lead."""
    # Type: trust the service layer (schedule is always a MaintenanceSchedule).
    # Avoid isinstance() so module-reload in tests doesn't break class identity.
    return MaintenanceScheduleResponse.from_schedule(
        schedule,  # type: ignore[arg-type]
        service.global_lead_days(),
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=list[MaintenanceScheduleResponse])
def list_maintenance_schedules(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_view)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
    instance_id: Annotated[
        int | None,
        Query(description="Filter to schedules for this stock instance."),
    ] = None,
    active: Annotated[
        bool | None,
        Query(description="When true, return only active schedules; false = only paused."),
    ] = None,
) -> list[MaintenanceScheduleResponse]:
    """Return maintenance schedules, optionally filtered by instance and/or active state.

    - Omit ``instance_id`` to list across all instances.
    - ``active=true`` returns only is_active=True rows; ``active=false`` only
      is_active=False rows; omitting returns all.
    """
    active_only = bool(active) if active is not None else False
    schedules = service.list_all(instance_id=instance_id, active_only=active_only)
    return [_to_response(service, s) for s in schedules]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", response_model=MaintenanceScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_maintenance_schedule(
    body: MaintenanceScheduleCreate,
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> MaintenanceScheduleResponse:
    """Create a new maintenance schedule on a stock instance.

    Returns 404 if the referenced stock instance does not exist.
    Returns 422 if ``interval_unit`` is not one of ``day``/``week``/``month``/``year``.
    Returns 422 if ``interval_count < 1`` or ``lead_days < 0``.
    """
    user_id = ctx.user.id if ctx.user is not None else None
    schedule = service.create(
        instance_id=body.instance_id,
        name=body.name,
        interval_unit=body.interval_unit,
        interval_count=body.interval_count,
        next_due_date=body.next_due_date,
        lead_days=body.lead_days,
        notes=body.notes,
        created_by=user_id,
    )
    db.commit()
    db.refresh(schedule)
    # Re-fetch with relationships joinedloaded for name/status resolution.
    fresh = service.get(schedule.id)
    return _to_response(service, fresh)


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


@router.get("/{schedule_id}", response_model=MaintenanceScheduleResponse)
def get_maintenance_schedule(
    schedule_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_view)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
) -> MaintenanceScheduleResponse:
    """Return one maintenance schedule by id.

    Returns 404 (maintenance.not_found) when the id does not exist.
    """
    schedule = service.get(schedule_id)
    return _to_response(service, schedule)


# ---------------------------------------------------------------------------
# Edit (PATCH)
# ---------------------------------------------------------------------------


@router.patch("/{schedule_id}", response_model=MaintenanceScheduleResponse)
def edit_maintenance_schedule(
    schedule_id: int,
    body: MaintenanceScheduleUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> MaintenanceScheduleResponse:
    """Edit an existing maintenance schedule (PATCH — only supplied fields updated).

    Returns 404 when the schedule does not exist.
    Returns 422 when an invalid ``interval_unit`` is supplied.
    """
    schedule = service.edit(schedule_id, body)
    db.commit()
    db.refresh(schedule)
    fresh = service.get(schedule.id)
    return _to_response(service, fresh)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_maintenance_schedule(
    schedule_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete a maintenance schedule.

    Returns 404 (maintenance.not_found) when the id does not exist.
    """
    service.delete(schedule_id)
    db.commit()


# ---------------------------------------------------------------------------
# Complete (mark done → advance next_due_date)
# ---------------------------------------------------------------------------


@router.post("/{schedule_id}/complete", response_model=MaintenanceScheduleResponse)
def complete_maintenance_schedule(
    schedule_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[MaintenanceScheduleService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
    body: MaintenanceComplete | None = None,
) -> MaintenanceScheduleResponse:
    """Record a maintenance completion and advance next_due_date.

    Sets ``last_completed_date = completed_on`` (today if omitted) and
    advances ``next_due_date`` by the schedule's interval (calendar-correct).
    Back-dated completions: supply ``completed_on`` to advance from a past date.

    The optional ``note`` is accepted for parity with future completion-history
    work but is **not persisted in M7** (no history table — M7 §13 deferred).

    Returns 404 (maintenance.not_found) when the id does not exist.
    """
    completed_on = body.completed_on if body is not None else None
    note = body.note if body is not None else None
    schedule = service.complete(schedule_id, completed_on=completed_on, note=note)
    db.commit()
    db.refresh(schedule)
    fresh = service.get(schedule.id)
    return _to_response(service, fresh)
