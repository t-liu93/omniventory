"""Pydantic request/response schemas for ItemDefinition endpoints.

Schemas are thin wire DTOs; business logic lives in the service layer.
All response schemas use ``from_attributes = True`` so they can be constructed
directly from SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.custom_fields import (
    CustomFieldsDict,
    CustomFieldsMap,
    deserialize_custom_fields,
)
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
    default_best_before_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Default shelf life in days (M3). ``NULL`` = no default. "
            "Must be ≥ 0 (0 = same-day expiry). "
            "Pydantic ge=0 is the sole validation; no DB CHECK constraint."
        ),
    )
    reminder_lead_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-item reminder lead-time override in days (M4). ``NULL`` = inherit "
            "(engine falls through to the per-user then global default — §4.3). "
            "Must be ≥ 0 when provided (0 = fire on the target date itself). "
            "Applies to whichever date source this definition's lots carry "
            "(best_before for perishables, warranty for durables). "
            "Pydantic ge=0 is the sole validation; no DB CHECK constraint."
        ),
    )
    custom_fields: CustomFieldsMap | None = Field(
        default=None,
        description=(
            "Optional flat key/value map for user-defined attributes (M5 Step 4). "
            "Keys: non-empty string ≤ 64 chars. "
            "Values: str (≤ 1024 chars), int, float, bool, or null — no nesting. "
            "Maximum 50 fields. NULL = no custom fields."
        ),
    )
    responsible_user_id: int | None = Field(
        default=None,
        description=(
            "Optional FK → users.id. The default responsible party for all lots of "
            "this definition (M6 Step 4). NULL = unassigned; the reminder engine "
            "falls back to all active users (M4 parity). "
            "Must reference an existing user when provided."
        ),
    )


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
    default_best_before_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Default shelf life in days (M3). ``NULL`` = remove the default. "
            "Must be ≥ 0 when provided."
        ),
    )
    reminder_lead_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-item reminder lead-time override in days (M4). ``NULL`` = remove "
            "the override (inherit from per-user or global). "
            "Must be ≥ 0 when provided."
        ),
    )
    custom_fields: CustomFieldsMap | None = Field(
        default=None,
        description=(
            "Optional flat key/value map (M5 Step 4). "
            "When explicitly set to null in the PATCH body, custom fields are cleared. "
            "When omitted from the PATCH body, existing custom fields are unchanged. "
            "Use model_fields_set to distinguish 'omitted' from 'explicitly null'."
        ),
    )
    responsible_user_id: int | None = Field(
        default=None,
        description=(
            "Optional FK → users.id (M6 Step 4). "
            "When explicitly provided (even as null), updates the responsible-party "
            "assignment: non-null sets the responsible user (validated to exist); "
            "null clears the assignment. "
            "When omitted from the PATCH body, the existing assignment is unchanged. "
            "Use model_fields_set to distinguish 'omitted' from 'explicitly null'."
        ),
    )


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
    default_best_before_days: int | None  # M3: shelf-life default in days; NULL = no default
    reminder_lead_days: int | None  # M4: per-item lead override; NULL = inherit (§4.3)
    custom_fields: CustomFieldsDict | None  # M5: parsed dict (or None)
    responsible_user_id: int | None  # M6: responsible-party FK (or None = unassigned)
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
