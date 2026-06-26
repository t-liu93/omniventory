"""Pydantic request/response schemas for StockInstance endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.

Key notes (M2 Step 3):
- ``quantity`` and ``purchase_price`` are ``Decimal`` (never float) per
  roadmap §2.9.
- ``quantity`` is now nullable in both ``InstanceCreate`` and ``InstanceResponse``
  to support all three tracking modes (M2 §3.2 / §3.4):
    - ``exact`` — Decimal provided on create (initial intake); nullable in
      response (ledger-derived cache).
    - ``level`` — must be NULL; ``stock_level`` is set instead.
    - ``none`` — both NULL.
- ``InstanceUpdate`` **does not include quantity** (M2 §2 "Create vs. movement"):
  once created, an ``exact`` lot's quantity changes only through movement
  endpoints.  ``stock_level`` is included for ``level``-mode updates.
- ``serial ⇒ quantity = 1`` is enforced in the service layer (422) and at the
  DB level (CHECK constraint); these schemas accept any value and leave
  enforcement to the service.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.custom_fields import (
    CustomFieldsDict,
    CustomFieldsMap,
    deserialize_custom_fields,
)


class InstanceCreate(BaseModel):
    """Body for POST /instances.

    ``quantity`` — optional initial intake for ``exact``-mode lots (service
    defaults to Decimal("1") when omitted for exact mode).  Must be NULL / not
    provided for ``level`` and ``none`` modes.

    ``stock_level`` — required for ``level``-mode lots; must be one of
    STOCK_LEVELS (validated by the service, not here).  Must not be provided
    for ``exact`` and ``none`` modes.

    ``best_before_date`` — optional per-lot best-before date (M3 Step 2).
    When omitted, the service auto-computes it from the definition's
    ``default_best_before_days`` (``today + N``) if set, otherwise leaves it
    NULL.  An explicit date (including a past date) always wins.  An explicit
    ``None`` stays NULL even when a default exists.
    Mode-independent (valid for ``exact``/``level``/``none`` lots alike).

    ``custom_fields`` — optional flat key/value map (M5 Step 4).  NULL = none.
    """

    definition_id: int
    location_id: int | None = None
    quantity: Decimal | None = None
    stock_level: str | None = None
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    warranty_expires: date | None = None
    warranty_details: str | None = None
    best_before_date: date | None = None
    purchase_price: Decimal | None = None
    purchase_date: date | None = None
    purchase_source: str | None = None
    custom_fields: CustomFieldsMap | None = Field(
        default=None,
        description=(
            "Optional flat key/value map for user-defined attributes (M5 Step 4). "
            "Keys: non-empty string ≤ 64 chars. "
            "Values: str (≤ 1024 chars), int, float, bool, or null — no nesting. "
            "Maximum 50 fields. NULL = no custom fields."
        ),
    )


class InstanceUpdate(BaseModel):
    """Body for PATCH /instances/{id} — all fields optional.

    ``quantity`` is intentionally absent (M2 §2 "Create vs. movement"):
    once an ``exact`` lot is created its quantity changes only through
    movement endpoints (intake / discard / adjust / consume / reverse).

    ``stock_level`` may be updated for ``level``-mode lots.

    ``best_before_date`` — optional (M3 Step 2).  Uses the model_fields_set
    convention: omitting the field leaves the stored date unchanged; supplying
    ``null`` explicitly clears the date to NULL.  No auto-compute on update —
    update is an explicit correction only.

    ``custom_fields`` — optional (M5 Step 4).  Uses the model_fields_set
    convention: omitting leaves stored custom fields unchanged; supplying
    ``null`` explicitly clears them.
    """

    location_id: int | None = None
    stock_level: str | None = None
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    warranty_expires: date | None = None
    warranty_details: str | None = None
    best_before_date: date | None = None
    purchase_price: Decimal | None = None
    purchase_date: date | None = None
    purchase_source: str | None = None
    custom_fields: CustomFieldsMap | None = Field(
        default=None,
        description=(
            "Optional flat key/value map (M5 Step 4). "
            "Omitting leaves existing custom fields unchanged. "
            "Explicitly supplying null clears custom fields."
        ),
    )


class InstanceResponse(BaseModel):
    """Public representation of a StockInstance."""

    id: int
    definition_id: int
    location_id: int | None
    quantity: Decimal | None  # nullable: NULL for level/none lots
    stock_level: str | None
    received_at: datetime | None
    serial: str | None
    model_number: str | None
    manufacturer: str | None
    warranty_expires: date | None
    warranty_details: str | None
    best_before_date: date | None  # nullable: NULL if no expiry tracked (M3)
    purchase_price: Decimal | None
    purchase_date: date | None
    purchase_source: str | None
    custom_fields: CustomFieldsDict | None  # M5: parsed dict (or None)
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("custom_fields", mode="before")
    @classmethod
    def _parse_custom_fields(cls, v: Any) -> CustomFieldsDict | None:
        """Parse a raw JSON string from the ORM column into a Python dict.

        When ``model_validate(orm_obj)`` is called, Pydantic reads the
        ``custom_fields`` attribute from the ORM object (a ``str | None``).
        This validator converts that raw string to the expected dict shape.
        A plain dict (e.g. from direct schema construction) is passed through.
        """
        if v is None or isinstance(v, dict):
            return v
        if isinstance(v, str):
            return deserialize_custom_fields(v)
        return None
