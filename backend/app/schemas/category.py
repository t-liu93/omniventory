"""Pydantic request/response schemas for Category endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CategoryCreate(BaseModel):
    """Body for POST /categories."""

    name: str
    description: str | None = None
    parent_id: int | None = None


class CategoryUpdate(BaseModel):
    """Body for PATCH /categories/{id} — all fields optional."""

    name: str | None = None
    description: str | None = None
    parent_id: int | None = None


class CategoryResponse(BaseModel):
    """Public representation of a Category (flat, no children)."""

    id: int
    name: str
    description: str | None
    parent_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CategoryTreeNode(BaseModel):
    """Recursive tree node for GET /categories/tree."""

    id: int
    name: str
    description: str | None
    parent_id: int | None
    created_at: datetime
    children: list[CategoryTreeNode] = []

    model_config = {"from_attributes": True}
