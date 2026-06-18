"""Pydantic request/response schemas for ItemDefinition endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.item_kind import KindResponse


class DefinitionCreate(BaseModel):
    """Body for POST /definitions."""

    name: str
    description: str | None = None
    category_id: int | None = None
    kind_id: int | None = None  # optional; service defaults to 'durable' when omitted
    unit: str = "pcs"
    default_location_id: int | None = None
    stock_tracking_mode: str = "exact"  # validated app-layer against STOCK_TRACKING_MODES (M2)
    min_stock: Decimal | None = None  # low-stock threshold; meaningful for 'exact' mode only


class DefinitionUpdate(BaseModel):
    """Body for PATCH /definitions/{id} — all fields optional."""

    name: str | None = None
    description: str | None = None
    category_id: int | None = None
    kind_id: int | None = None
    unit: str | None = None
    default_location_id: int | None = None
    stock_tracking_mode: str | None = None  # validated app-layer against STOCK_TRACKING_MODES (M2)
    min_stock: Decimal | None = None  # low-stock threshold; meaningful for 'exact' mode only


class DefinitionResponse(BaseModel):
    """Public representation of an ItemDefinition."""

    id: int
    name: str
    description: str | None
    category_id: int | None
    kind_id: int
    kind: KindResponse
    unit: str
    default_location_id: int | None
    stock_tracking_mode: str
    min_stock: Decimal | None
    created_at: datetime

    model_config = {"from_attributes": True}
