"""Stock instance CRUD and movement operation endpoints.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    GET    /instances               Flat list, optionally filtered.
    POST   /instances               Create a stock instance.
    GET    /instances/{id}          Get a single instance.
    PATCH  /instances/{id}          Partial update.
    DELETE /instances/{id}          Delete.

    GET    /instances/{id}/movements   Ledger history (newest-first).
    POST   /instances/{id}/intake      Add stock.
    POST   /instances/{id}/discard     Write off stock.
    POST   /instances/{id}/adjust      Stock-take to an absolute value.
    POST   /instances/{id}/move        Relocate the whole lot.

Query params for GET /instances:
    q              Case-insensitive substring match on serial / model_number /
                   manufacturer.
    definition_id  Filter to instances of this definition.
    location_id    Filter to instances at this location.

Error contract:
    404  Instance not found / definition not found / location not found.
    409  Conflict (e.g. serial uniqueness violation from DB; mode conflict).
    422  serial provided but quantity != 1; validation errors; insufficient stock.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.stock_instance import InstanceCreate, InstanceResponse, InstanceUpdate
from app.schemas.stock_movement import MovementResponse
from app.schemas.stock_movement_ops import AdjustRequest, DiscardRequest, IntakeRequest, MoveRequest
from app.services.stock_instance import StockInstanceService
from app.services.stock_movement import StockMovementService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/instances", tags=["instances"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> StockInstanceService:
    """Dependency: build and return a StockInstanceService."""
    return StockInstanceService(db)


def _get_movement_service(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    db: Session = Depends(get_db),
) -> StockMovementService:
    """Dependency: build and return a StockMovementService."""
    return StockMovementService(db, ctx)


@router.get("", response_model=list[InstanceResponse])
def list_instances(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[StockInstanceService, Depends(_get_service)],
    q: Annotated[
        str | None,
        Query(
            description="Case-insensitive substring match on serial, model_number, or manufacturer."
        ),
    ] = None,
    definition_id: Annotated[
        int | None,
        Query(description="Filter by definition_id."),
    ] = None,
    location_id: Annotated[
        int | None,
        Query(description="Filter by location_id."),
    ] = None,
) -> list[InstanceResponse]:
    """Return a flat list of stock instances, optionally filtered."""
    instances = service.list_all(q=q, definition_id=definition_id, location_id=location_id)
    return [InstanceResponse.model_validate(i) for i in instances]


@router.post("", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
def create_instance(
    body: InstanceCreate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[StockInstanceService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Create a new stock instance.

    Returns 422 if a serial is provided with quantity != 1.
    Returns 404 if definition_id or location_id does not exist.
    """
    inst = service.create(body)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)


@router.get("/{instance_id}", response_model=InstanceResponse)
def get_instance(
    instance_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[StockInstanceService, Depends(_get_service)],
) -> InstanceResponse:
    """Return a single stock instance by id."""
    inst = service.get(instance_id)
    return InstanceResponse.model_validate(inst)


@router.patch("/{instance_id}", response_model=InstanceResponse)
def update_instance(
    instance_id: int,
    body: InstanceUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[StockInstanceService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Partially update a stock instance.

    Returns 422 if the update would result in a serial with quantity != 1.
    """
    inst = service.update(instance_id, body)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(
    instance_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[StockInstanceService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Delete a stock instance."""
    service.delete(instance_id)
    db.commit()


# --------------------------------------------------------------------------- #
# Ledger history                                                               #
# --------------------------------------------------------------------------- #


@router.get("/{instance_id}/movements", response_model=list[MovementResponse])
def list_movements(
    instance_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    instance_svc: Annotated[StockInstanceService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
) -> list[MovementResponse]:
    """Return the ledger history for a stock instance (newest-first).

    Returns 404 if the instance does not exist.
    """
    # Verify the instance exists.
    instance_svc.get(instance_id)
    movements = movement_svc.list_movements_for_instance(instance_id)
    return [MovementResponse.model_validate(m) for m in movements]


# --------------------------------------------------------------------------- #
# Ledger operations                                                            #
# --------------------------------------------------------------------------- #


@router.post("/{instance_id}/intake", response_model=InstanceResponse)
def intake(
    instance_id: int,
    body: IntakeRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    instance_svc: Annotated[StockInstanceService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Add stock to a lot.

    Returns 404 if the instance does not exist.
    Returns 409 if the definition is not in 'exact' mode.
    Returns 422 if quantity <= 0, or the operation would violate the
    serial⇒qty=1 constraint.
    """
    inst = instance_svc.get(instance_id)
    movement_svc.intake(inst, body.quantity, occurred_at=body.occurred_at, note=body.note)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)


@router.post("/{instance_id}/discard", response_model=InstanceResponse)
def discard(
    instance_id: int,
    body: DiscardRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    instance_svc: Annotated[StockInstanceService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Write off stock from a lot.

    Returns 404 if the instance does not exist.
    Returns 409 if the definition is not in 'exact' mode.
    Returns 422 if quantity <= 0, or the operation would drive the lot below 0.
    """
    inst = instance_svc.get(instance_id)
    movement_svc.discard(inst, body.quantity, occurred_at=body.occurred_at, note=body.note)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)


@router.post("/{instance_id}/adjust", response_model=InstanceResponse)
def adjust(
    instance_id: int,
    body: AdjustRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    instance_svc: Annotated[StockInstanceService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Adjust a lot's quantity to an absolute counted value (stock-take).

    Returns 404 if the instance does not exist.
    Returns 409 if the definition is not in 'exact' mode.
    Returns 422 if quantity < 0.
    """
    inst = instance_svc.get(instance_id)
    movement_svc.adjust(inst, body.quantity, occurred_at=body.occurred_at, note=body.note)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)


@router.post("/{instance_id}/move", response_model=InstanceResponse)
def move(
    instance_id: int,
    body: MoveRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    instance_svc: Annotated[StockInstanceService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Relocate a lot to a new location (whole-lot move).

    Records a move movement with delta = 0; updates location_id.

    Returns 404 if the instance or to_location_id does not exist.
    Returns 409 if the definition is not in 'exact' mode.
    """
    inst = instance_svc.get(instance_id)
    movement_svc.move(inst, body.to_location_id, occurred_at=body.occurred_at, note=body.note)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)
