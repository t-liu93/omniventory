"""Stock instance CRUD endpoints.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    GET    /instances               Flat list, optionally filtered.
    POST   /instances               Create a stock instance.
    GET    /instances/{id}          Get a single instance.
    PATCH  /instances/{id}          Partial update.
    DELETE /instances/{id}          Delete.

Query params for GET /instances:
    q              Case-insensitive substring match on serial / model_number /
                   manufacturer.
    definition_id  Filter to instances of this definition.
    location_id    Filter to instances at this location.

Error contract:
    404  Instance not found / definition not found / location not found.
    409  Conflict (e.g. serial uniqueness violation from DB).
    422  serial provided but quantity != 1; validation errors.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.db.session import get_db
from app.schemas.stock_instance import InstanceCreate, InstanceResponse, InstanceUpdate
from app.services.stock_instance import StockInstanceService

router = APIRouter(prefix="/instances", tags=["instances"])


def _get_service(db: Session = Depends(get_db)) -> StockInstanceService:
    """Dependency: build and return a StockInstanceService."""
    return StockInstanceService(db)


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
