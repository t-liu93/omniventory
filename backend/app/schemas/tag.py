"""Pydantic request/response schemas for Tag endpoints (M5 Step 2).

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TagResponse(BaseModel):
    """Public representation of a Tag."""

    id: int
    name: str
    color: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    """Body for POST /tags."""

    name: str = Field(..., min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=32)


class TagUpdate(BaseModel):
    """Body for PATCH /tags/{id} — all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=32)


class TagLinkResponse(BaseModel):
    """Public representation of a TagLink (a tag attached to an owner).

    ``tag`` embeds the full tag details for convenience (avoids a second
    round-trip on the client side).
    """

    id: int
    tag_id: int
    model_type: str
    model_id: int
    created_at: datetime
    tag: TagResponse

    model_config = {"from_attributes": True}


class TagSetRequest(BaseModel):
    """Body for PUT /tags/links — replace an owner's whole tag set."""

    model_type: str = Field(..., min_length=1)
    model_id: int
    tag_ids: list[int] = Field(default_factory=list)
