"""Shopping-list endpoints (M7 Steps 1 + 2).

All endpoints require a valid session.  Reads require VIEW (any authenticated
user); mutations require EDIT (member or admin).

Routes (all under the api_prefix, e.g. /api):
    GET    /shopping-list?include_purchased=  List items (open first; VIEW).
    POST   /shopping-list                     Add a manual item (EDIT).
    POST   /shopping-list/clear-purchased     Delete all checked items (EDIT).
    POST   /shopping-list/refresh             Force auto-reconcile; return list (EDIT).
    PATCH  /shopping-list/{id}                Edit qty/name/note (EDIT).
    POST   /shopping-list/{id}/check          Mark purchased (EDIT).
    POST   /shopping-list/{id}/uncheck        Revert purchased (EDIT).
    DELETE /shopping-list/{id}                Remove an item (EDIT).

Step 1 scope: CRUD + check/uncheck (no intake — that is Step 3).
Step 2 scope: POST /shopping-list/refresh + auto-reconcile wired in.

Route ordering note: ``/clear-purchased`` and ``/refresh`` are registered
BEFORE ``/{item_id}`` to avoid FastAPI trying (and failing) to convert the
literal strings to integers for the path parameter.

Error contract:
    401  No/invalid session.
    403  Insufficient role (auth.forbidden).
    404  Item definition not found (item_definition.not_found) on POST.
    404  Shopping list item not found (shopping_list.not_found) on PATCH/DELETE/check/uncheck.
    422  Cross-field guard: neither definition_id nor name provided (validation.invalid_input).
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
from app.schemas.shopping_list import (
    ClearPurchasedResponse,
    ShoppingListItemCreate,
    ShoppingListItemResponse,
    ShoppingListItemUpdate,
)
from app.services.shopping_list import ShoppingListService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/shopping-list", tags=["shopping-list"], responses=_ERROR_RESPONSES)


def _get_service(db: Annotated[Session, Depends(get_db)]) -> ShoppingListService:
    """Dependency: build and return a ShoppingListService."""
    return ShoppingListService(db)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ShoppingListItemResponse])
def list_shopping_list(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_view)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    include_purchased: Annotated[
        bool,
        Query(description="Include checked/purchased items (default: false = open only)."),
    ] = False,
) -> list[ShoppingListItemResponse]:
    """Return the shopping list (open items first; optionally include purchased).

    Name and unit are resolved live: definition-linked rows show the
    definition's current name/unit; free-text rows show the row's own values.
    """
    items = service.list_items(include_purchased=include_purchased)
    return [ShoppingListItemResponse.from_item(item) for item in items]


# ---------------------------------------------------------------------------
# Create (add manual item)
# ---------------------------------------------------------------------------


@router.post("", response_model=ShoppingListItemResponse, status_code=status.HTTP_201_CREATED)
def add_shopping_list_item(
    body: ShoppingListItemCreate,
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ShoppingListItemResponse:
    """Add a manual shopping-list item.

    At least one of ``definition_id`` / ``name`` must be provided.
    Returns 404 if the referenced definition does not exist.
    Returns 422 if neither definition_id nor name is provided.
    """
    user_id = ctx.user.id if ctx.user is not None else None
    item = service.add_manual(
        definition_id=body.definition_id,
        name=body.name,
        desired_quantity=body.desired_quantity,
        unit=body.unit,
        note=body.note,
        created_by=user_id,
    )
    db.commit()
    db.refresh(item)
    # Re-fetch with definition joinedloaded for live name/unit resolution.
    from app.repositories.shopping_list import ShoppingListRepository

    fresh = ShoppingListRepository(db).get(item.id)
    assert fresh is not None
    return ShoppingListItemResponse.from_item(fresh)


# ---------------------------------------------------------------------------
# Edit (PATCH)
# Note: /shopping-list/clear-purchased must be registered BEFORE
# /shopping-list/{id} to avoid "clear-purchased" being captured as an integer id.
# ---------------------------------------------------------------------------


@router.post("/clear-purchased", response_model=ClearPurchasedResponse)
def clear_purchased(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ClearPurchasedResponse:
    """Delete all purchased (checked-off) items.

    Returns the count of deleted rows.
    """
    count = service.clear_purchased()
    db.commit()
    return ClearPurchasedResponse(deleted_count=count)


# ---------------------------------------------------------------------------
# Refresh (Step 2) — must be registered BEFORE /{item_id} routes
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=list[ShoppingListItemResponse])
def refresh_shopping_list(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ShoppingListItemResponse]:
    """Force auto-reconcile and return the current (open) shopping list.

    Immediately runs ``reconcile_auto_items()`` so that newly-low definitions
    appear as auto rows and recovered definitions' open auto rows are pruned,
    without waiting for the daily scan.  This is the in-UI way to demo
    auto-population.

    Returns only open (unchecked) items after the reconcile.
    """
    service.reconcile_auto_items()
    db.commit()
    items = service.list_items(include_purchased=False)
    return [ShoppingListItemResponse.from_item(item) for item in items]


@router.patch("/{item_id}", response_model=ShoppingListItemResponse)
def edit_shopping_list_item(
    item_id: int,
    body: ShoppingListItemUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ShoppingListItemResponse:
    """Edit an existing shopping-list item (desired_quantity / name / note).

    Only fields present in the request body are applied (PATCH semantics).
    Returns 404 if the item does not exist.
    """
    item = service.edit(item_id, body)
    db.commit()
    db.refresh(item)
    from app.repositories.shopping_list import ShoppingListRepository

    fresh = ShoppingListRepository(db).get(item.id)
    assert fresh is not None
    return ShoppingListItemResponse.from_item(fresh)


# ---------------------------------------------------------------------------
# Check / uncheck
# ---------------------------------------------------------------------------


@router.post("/{item_id}/check", response_model=ShoppingListItemResponse)
def check_off_item(
    item_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ShoppingListItemResponse:
    """Mark a shopping-list item as purchased (check-off).

    Step 1: stamps ``purchased_at = now`` only (no intake body or stock creation).
    Check-off with intake is added in Step 3.
    Returns 404 if the item does not exist.
    """
    item = service.check_off(item_id)
    db.commit()
    db.refresh(item)
    from app.repositories.shopping_list import ShoppingListRepository

    fresh = ShoppingListRepository(db).get(item.id)
    assert fresh is not None
    return ShoppingListItemResponse.from_item(fresh)


@router.post("/{item_id}/uncheck", response_model=ShoppingListItemResponse)
def uncheck_item(
    item_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ShoppingListItemResponse:
    """Revert a shopping-list item to the open/unchecked state.

    Clears ``purchased_at``.  Does not reverse any intake that occurred at
    check-off time (that is a separate stock action).
    Returns 404 if the item does not exist.
    """
    item = service.uncheck(item_id)
    db.commit()
    db.refresh(item)
    from app.repositories.shopping_list import ShoppingListRepository

    fresh = ShoppingListRepository(db).get(item.id)
    assert fresh is not None
    return ShoppingListItemResponse.from_item(fresh)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_shopping_list_item(
    item_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_edit)],
    service: Annotated[ShoppingListService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Remove (hard-delete) a shopping-list item.

    Returns 404 if the item does not exist.
    """
    service.remove(item_id)
    db.commit()
