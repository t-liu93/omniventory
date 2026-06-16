"""Pydantic request/response schemas for Location endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.

Step 4 additions:
- ``LocationUpdate`` now accepts ``item_instance_id`` (the container-as-item
  bridge); ``LocationService`` validates and applies it.
- ``LocationResponse`` and ``LocationTreeNode`` expose ``item_instance_id``
  so the frontend can display and navigate the bridge.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LocationCreate(BaseModel):
    """Body for POST /locations."""

    name: str
    description: str | None = None
    parent_id: int | None = None


class LocationUpdate(BaseModel):
    """Body for PATCH /locations/{id} — all fields optional."""

    name: str | None = None
    description: str | None = None
    parent_id: int | None = None
    item_instance_id: int | None = None


class LocationResponse(BaseModel):
    """Public representation of a Location (flat, no children)."""

    id: int
    name: str
    description: str | None
    parent_id: int | None
    item_instance_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LocationTreeNode(BaseModel):
    """Recursive tree node for GET /locations/tree."""

    id: int
    name: str
    description: str | None
    parent_id: int | None
    item_instance_id: int | None
    created_at: datetime
    children: list[LocationTreeNode] = []

    model_config = {"from_attributes": True}
