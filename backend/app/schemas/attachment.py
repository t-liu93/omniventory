"""Pydantic request/response schemas for Attachment endpoints (M5 Step 1).

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MediaSummary(BaseModel):
    """Embedded media metadata within an AttachmentResponse.

    ``url`` is the capability URL for the media file: ``/media/<sha256[:2]>/<sha256>``.
    The path matches the StaticFiles mount at ``/media``.
    """

    sha256: str
    content_type: str
    byte_size: int
    width: int | None
    height: int | None
    url: str

    model_config = {"from_attributes": True}


class AttachmentResponse(BaseModel):
    """Public representation of an Attachment (with embedded media metadata)."""

    id: int
    model_type: str
    model_id: int
    original_filename: str | None
    title: str | None
    sort_order: int
    uploaded_by: int | None
    created_at: datetime
    media: MediaSummary

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_url(cls, att: Any) -> AttachmentResponse:
        """Build an AttachmentResponse from an Attachment ORM object.

        Derives the ``media.url`` from the sha256 hash (not stored in DB).
        Uses duck-typing rather than isinstance to survive module reloads in tests.
        """
        mf = att.media_file
        sha = mf.sha256
        url = f"/media/{sha[:2]}/{sha}"
        media = MediaSummary(
            sha256=sha,
            content_type=mf.content_type,
            byte_size=mf.byte_size,
            width=mf.width,
            height=mf.height,
            url=url,
        )
        return cls(
            id=att.id,
            model_type=att.model_type,
            model_id=att.model_id,
            original_filename=att.original_filename,
            title=att.title,
            sort_order=att.sort_order,
            uploaded_by=att.uploaded_by,
            created_at=att.created_at,
            media=media,
        )


class AttachmentUpdate(BaseModel):
    """Body for PATCH /attachments/{id} — all fields optional."""

    title: str | None = None
    sort_order: int | None = None
