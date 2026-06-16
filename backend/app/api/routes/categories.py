"""Category tree CRUD endpoints.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    GET    /categories               Flat list, optionally filtered by q / parent_id.
    GET    /categories/tree          Full nested tree (recursive CategoryTreeNode).
    POST   /categories               Create a category.
    GET    /categories/{id}          Get a single category.
    PATCH  /categories/{id}          Partial update (reparent cycle-checked).
    DELETE /categories/{id}          Delete (guarded — 409 if non-empty).

Error contract:
    404  Category not found / parent not found.
    409  Cycle detected on reparent / delete of non-empty node.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.db.session import get_db
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryTreeNode,
    CategoryUpdate,
)
from app.services.category import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"])


def _get_service(db: Session = Depends(get_db)) -> CategoryService:
    """Dependency: build and return a CategoryService."""
    return CategoryService(db)


@router.get("/tree", response_model=list[CategoryTreeNode])
def get_tree(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
) -> list[CategoryTreeNode]:
    """Return the full category tree as a nested structure."""
    return service.get_tree()


@router.get("", response_model=list[CategoryResponse])
def list_categories(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
    q: Annotated[str | None, Query(description="Case-insensitive name substring filter.")] = None,
    parent_id: Annotated[
        int | None,
        Query(description="Filter by parent_id (omit to get all categories)."),
    ] = None,
) -> list[CategoryResponse]:
    """Return a flat list of categories, optionally filtered.

    - ``q``: case-insensitive substring match on the name.
    - ``parent_id``: when provided, return only categories with that parent.
    """
    parent_id_filter = parent_id is not None
    cats = service.list_all(q=q, parent_id=parent_id, parent_id_filter=parent_id_filter)
    return [CategoryResponse.model_validate(cat) for cat in cats]


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryCreate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> CategoryResponse:
    """Create a new category."""
    cat = service.create(body)
    db.commit()
    db.refresh(cat)
    return CategoryResponse.model_validate(cat)


@router.get("/{category_id}", response_model=CategoryResponse)
def get_category(
    category_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
) -> CategoryResponse:
    """Return a single category by id."""
    cat = service.get(category_id)
    return CategoryResponse.model_validate(cat)


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    body: CategoryUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> CategoryResponse:
    """Partially update a category.

    Reparenting (changing ``parent_id``) is cycle-checked in the service layer.
    """
    cat = service.update(category_id, body)
    db.commit()
    db.refresh(cat)
    return CategoryResponse.model_validate(cat)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[CategoryService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Delete a category.

    Returns 409 if the category still has child categories.
    """
    service.delete(category_id)
    db.commit()
