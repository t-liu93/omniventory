"""Pydantic request/response schemas for StockInstance endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.

Key notes:
- ``quantity`` and ``purchase_price`` are ``Decimal`` (never float) per
  roadmap §2.9.
- ``serial ⇒ quantity = 1`` is enforced in the service layer (422) and at
  the DB level (CHECK constraint); these schemas accept any value and leave
  enforcement to the service.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class InstanceCreate(BaseModel):
    """Body for POST /instances."""

    definition_id: int
    location_id: int | None = None
    quantity: Decimal | None = None  # service defaults to Decimal("1") when omitted
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    warranty_expires: date | None = None
    warranty_details: str | None = None
    purchase_price: Decimal | None = None
    purchase_date: date | None = None
    purchase_source: str | None = None


class InstanceUpdate(BaseModel):
    """Body for PATCH /instances/{id} — all fields optional."""

    location_id: int | None = None
    quantity: Decimal | None = None
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    warranty_expires: date | None = None
    warranty_details: str | None = None
    purchase_price: Decimal | None = None
    purchase_date: date | None = None
    purchase_source: str | None = None


class InstanceResponse(BaseModel):
    """Public representation of a StockInstance."""

    id: int
    definition_id: int
    location_id: int | None
    quantity: Decimal
    serial: str | None
    model_number: str | None
    manufacturer: str | None
    warranty_expires: date | None
    warranty_details: str | None
    purchase_price: Decimal | None
    purchase_date: date | None
    purchase_source: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
