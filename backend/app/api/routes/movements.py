"""Stock movement reversal endpoint.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    POST /movements/{id}/reverse    Append a compensating reversal entry.

Error contract:
    404  Movement not found.
    409  Cannot-reverse-reversal; already reversed; reversal would go negative.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.stock_instance import InstanceResponse
from app.schemas.stock_movement_ops import ReverseRequest
from app.services.stock_movement import StockMovementService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/movements", tags=["movements"], responses=_ERROR_RESPONSES)


def _get_movement_service(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    db: Session = Depends(get_db),
) -> StockMovementService:
    """Dependency: build and return a StockMovementService."""
    return StockMovementService(db, ctx)


@router.post("/{movement_id}/reverse", response_model=InstanceResponse)
def reverse(
    movement_id: int,
    body: ReverseRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    movement_svc: Annotated[StockMovementService, Depends(_get_movement_service)],
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Append a compensating correction movement to undo the specified movement.

    The original movement is NOT mutated — the ledger is append-only.
    A ``correction`` entry with ``delta = −original.delta`` is appended and
    the lot's quantity is recomputed.

    Returns 404 if the movement does not exist.
    Returns 409 if the movement is itself a reversal, has already been reversed,
    or the reversal would drive the lot's quantity below 0.
    """
    inst = movement_svc.reverse(movement_id, note=body.note)
    db.commit()
    db.refresh(inst)
    return InstanceResponse.model_validate(inst)
