"""Attachment upload/list/update/delete endpoints (M5 Step 1).

All endpoints require a valid session.

Routes (all under the api_prefix, e.g. /api):
    POST   /attachments                          Upload a file and create a reference.
    GET    /attachments?model_type=&model_id=    List an owner's attachments.
    PATCH  /attachments/{id}                     Update title / sort_order.
    DELETE /attachments/{id}                     Remove a reference (ref-count cleanup).

Error contract:
    401  No/invalid session.
    404  Attachment not found / owner not found.
    413  File too large (attachment.file_too_large).
    415  Unsupported content type (attachment.unsupported_type).
    422  Bad model_type (validation.invalid_input).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.attachment import AttachmentResponse, AttachmentUpdate
from app.services.attachment import AttachmentService, unlink_post_commit

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/attachments", tags=["attachments"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> AttachmentService:
    """Dependency: build and return an AttachmentService with the configured media dir."""
    settings = get_settings()
    media_dir = Path(settings.data_dir) / "media"
    return AttachmentService(db, media_dir=media_dir)


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
def upload_attachment(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[AttachmentService, Depends(_get_service)],
    db: Session = Depends(get_db),
    model_type: str = Form(
        ..., description="Owner type: item_definition / stock_instance / location"
    ),
    model_id: int = Form(..., description="Owner PK."),
    file: UploadFile = File(..., description="File to upload."),
    title: str | None = Form(default=None, description="Optional caption."),
) -> AttachmentResponse:
    """Upload a file and attach it to an owner entity.

    Returns 422 if model_type is invalid, 404 if the owner is missing,
    413 if the file exceeds the size limit, 415 if the type is unsupported.
    """
    user_id = ctx.user.id if ctx.user is not None else None
    att = service.upload(
        model_type,
        model_id,
        file,
        title=title,
        uploaded_by=user_id,
    )
    db.commit()
    db.refresh(att)
    # Eager-load media_file for response serialization.
    _ = att.media_file
    return AttachmentResponse.from_orm_with_url(att)


@router.get("", response_model=list[AttachmentResponse])
def list_attachments(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[AttachmentService, Depends(_get_service)],
    model_type: Annotated[str, Query(description="Owner type.")],
    model_id: Annotated[int, Query(description="Owner PK.")],
) -> list[AttachmentResponse]:
    """List all attachments for a given owner (model_type + model_id)."""
    atts = service.list_for(model_type, model_id)
    return [AttachmentResponse.from_orm_with_url(a) for a in atts]


@router.patch("/{attachment_id}", response_model=AttachmentResponse)
def update_attachment(
    attachment_id: int,
    body: AttachmentUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[AttachmentService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> AttachmentResponse:
    """Update the title and/or sort_order of an attachment."""
    set_title = "title" in body.model_fields_set
    att = service.update(
        attachment_id,
        title=body.title,
        set_title=set_title,
        sort_order=body.sort_order,
    )
    db.commit()
    db.refresh(att)
    _ = att.media_file
    return AttachmentResponse.from_orm_with_url(att)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[AttachmentService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Remove an attachment reference.

    If this was the last reference to the underlying media file, the
    media_files row and the physical file are also deleted (best-effort).
    Returns 404 if the attachment does not exist.
    """
    paths = service.delete(attachment_id)
    db.commit()
    unlink_post_commit(paths)
