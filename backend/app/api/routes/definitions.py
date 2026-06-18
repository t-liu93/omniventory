"""Item definition CRUD and consume endpoints.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    GET    /definitions                      Flat list, optionally filtered by q / category_id.
    POST   /definitions                      Create a definition.
    GET    /definitions/{id}                 Get a single definition.
    PATCH  /definitions/{id}                 Partial update.
    DELETE /definitions/{id}                 Delete.
    POST   /definitions/{id}/consume         FIFO consume across definition's lots.

Error contract:
    404  Definition not found / category not found / location not found.
    409  Mode conflict (tracking_mode_change_conflict).
    422  Invalid kind_id; insufficient stock; negative quantity.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.item_definition import DefinitionCreate, DefinitionResponse, DefinitionUpdate
from app.schemas.stock_instance import InstanceResponse
from app.schemas.stock_movement_ops import ConsumeRequest
from app.services.item_definition import ItemDefinitionService
from app.services.stock_movement import StockMovementService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/definitions", tags=["definitions"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> ItemDefinitionService:
    """Dependency: build and return an ItemDefinitionService."""
    return ItemDefinitionService(db)


def _get_movement_service(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    db: Session = Depends(get_db),
) -> StockMovementService:
    """Dependency: build and return a StockMovementService."""
    return StockMovementService(db, ctx)


@router.get("", response_model=list[DefinitionResponse])
def list_definitions(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[ItemDefinitionService, Depends(_get_service)],
    q: Annotated[str | None, Query(description="Case-insensitive name substring filter.")] = None,
    category_id: Annotated[
        int | None,
        Query(description="Filter by category_id."),
    ] = None,
) -> list[DefinitionResponse]:
    """Return a flat list of item definitions, optionally filtered.

    - ``q``: case-insensitive substring match on the name.
    - ``category_id``: when provided, return only definitions with that category.
    """
    defns = service.list_all(q=q, category_id=category_id)
    return [DefinitionResponse.model_validate(d) for d in defns]


@router.post("", response_model=DefinitionResponse, status_code=status.HTTP_201_CREATED)
def create_definition(
    body: DefinitionCreate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[ItemDefinitionService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> DefinitionResponse:
    """Create a new item definition.

    If ``kind_id`` is omitted, the ``durable`` kind is used automatically.
    Returns 422 if ``kind_id`` is provided but does not exist.
    """
    defn = service.create(body)
    db.commit()
    db.refresh(defn)
    return DefinitionResponse.model_validate(defn)


@router.get("/{definition_id}", response_model=DefinitionResponse)
def get_definition(
    definition_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[ItemDefinitionService, Depends(_get_service)],
) -> DefinitionResponse:
    """Return a single item definition by id."""
    defn = service.get(definition_id)
    return DefinitionResponse.model_validate(defn)


@router.patch("/{definition_id}", response_model=DefinitionResponse)
def update_definition(
    definition_id: int,
    body: DefinitionUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[ItemDefinitionService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> DefinitionResponse:
    """Partially update an item definition.

    Returns 422 if ``kind_id`` is provided but does not exist.
    """
    defn = service.update(definition_id, body)
    db.commit()
    db.refresh(defn)
    return DefinitionResponse.model_validate(defn)


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_definition(
    definition_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[ItemDefinitionService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Delete an item definition."""
    service.delete(definition_id)
    db.commit()


# --------------------------------------------------------------------------- #
# FIFO consume                                                                 #
# --------------------------------------------------------------------------- #


@router.post("/{definition_id}/consume", response_model=list[InstanceResponse])
def consume(
    definition_id: int,
    body: ConsumeRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    def_service: Annotated[ItemDefinitionService, Depends(_get_service)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> list[InstanceResponse]:
    """Consume stock from a definition's lots in FIFO order (oldest first).

    Returns the list of lots that were touched (with updated quantities).

    Returns 404 if the definition does not exist.
    Returns 409 if the definition is not in 'exact' mode.
    Returns 422 if quantity <= 0, or total available stock is insufficient.
    """
    defn = def_service.get(definition_id)
    touched = movement_svc.consume_fifo(
        defn, body.quantity, occurred_at=body.occurred_at, note=body.note
    )
    db.commit()
    for inst in touched:
        db.refresh(inst)
    return [InstanceResponse.model_validate(inst) for inst in touched]
