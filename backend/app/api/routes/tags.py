"""Tag CRUD and tag-link endpoints (M5 Step 2).

All endpoints require a valid session.

Routes (all under the api_prefix, e.g. /api):
    GET    /tags                             List all tags (optional ?q= substring filter).
    POST   /tags                             Create a new tag.
    PATCH  /tags/{id}                        Update name / color.
    DELETE /tags/{id}                        Delete a tag (and cascade its links via FK).
    GET    /tags/links?model_type=&model_id= List tags attached to an owner.
    PUT    /tags/links                       Replace an owner's tag set (TagSetRequest body).

Error contract:
    401  No/invalid session.
    404  Tag not found / owner not found.
    409  tag.duplicate_name.
    422  Bad model_type (validation.invalid_input).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.tag import TagLink
from app.schemas.tag import TagCreate, TagLinkResponse, TagResponse, TagSetRequest, TagUpdate
from app.services.tag import TagService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/tags", tags=["tags"], responses=_ERROR_RESPONSES)


def _get_service(db: Annotated[Session, Depends(get_db)]) -> TagService:
    """Dependency: build and return a TagService."""
    return TagService(db)


def _tag_link_response(link: TagLink, tag: object) -> TagLinkResponse:
    """Build a TagLinkResponse from a TagLink ORM object + its Tag."""
    from app.schemas.tag import TagResponse as TR

    tag_resp = TR.model_validate(tag)
    return TagLinkResponse(
        id=link.id,
        tag_id=link.tag_id,
        model_type=link.model_type,
        model_id=link.model_id,
        created_at=link.created_at,
        tag=tag_resp,
    )


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TagResponse])
def list_tags(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    q: Annotated[str | None, Query(description="Case-insensitive name substring filter.")] = None,
) -> list[TagResponse]:
    """List all tags, optionally filtered by name substring."""
    tags = service.list_tags(q=q)
    return [TagResponse.model_validate(t) for t in tags]


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(
    body: TagCreate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> TagResponse:
    """Create a new tag.

    Returns 409 if a tag with the same name already exists (case-insensitive).
    """
    tag = service.create(name=body.name, color=body.color)
    db.commit()
    db.refresh(tag)
    return TagResponse.model_validate(tag)


@router.patch("/{tag_id}", response_model=TagResponse)
def update_tag(
    tag_id: int,
    body: TagUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> TagResponse:
    """Update a tag's name and/or color.

    Returns 404 if the tag does not exist.
    Returns 409 if renaming to a name already used by another tag (case-insensitive).
    """
    set_color = "color" in body.model_fields_set
    tag = service.update(
        tag_id,
        name=body.name,
        color=body.color,
        set_color=set_color,
    )
    db.commit()
    db.refresh(tag)
    return TagResponse.model_validate(tag)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tag_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete a tag.

    Deleting a tag also drops all its tag_links via the FK ondelete=CASCADE.
    Returns 404 if the tag does not exist.
    """
    service.delete(tag_id)
    db.commit()


# ---------------------------------------------------------------------------
# Tag-link operations
# ---------------------------------------------------------------------------


@router.get("/links", response_model=list[TagLinkResponse])
def list_tag_links(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    model_type: Annotated[str, Query(description="Owner type.")],
    model_id: Annotated[int, Query(description="Owner PK.")],
    db: Annotated[Session, Depends(get_db)],
) -> list[TagLinkResponse]:
    """List all tags attached to a given owner (model_type + model_id)."""
    from app.repositories.tag import TagLinkRepository, TagRepository

    link_repo = TagLinkRepository(db)
    tag_repo = TagRepository(db)
    links = link_repo.list_for_owner(model_type, model_id)
    result = []
    for link in links:
        tag = tag_repo.get(link.tag_id)
        if tag is not None:
            result.append(_tag_link_response(link, tag))
    return result


@router.put("/links", response_model=list[TagResponse])
def set_tag_links(
    body: TagSetRequest,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[TagService, Depends(_get_service)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TagResponse]:
    """Replace an owner's tag set.

    Adds links for tag_ids not yet attached; removes links no longer in the
    list.  Returns the owner's new tag list.

    Returns 422 if model_type is invalid, 404 if the owner or any tag is missing.
    """
    tags = service.set_tags_for_owner(body.model_type, body.model_id, body.tag_ids)
    db.commit()
    return [TagResponse.model_validate(t) for t in tags]
