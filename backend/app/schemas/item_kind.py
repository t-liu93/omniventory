"""Pydantic response schema for ItemKind endpoints.

``item_kinds`` is read-only in M1 — no Create or Update schemas.
The ``KindResponse`` is used by ``GET /kinds``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class KindResponse(BaseModel):
    """Public representation of an ItemKind."""

    id: int
    code: str
    name: str
    is_system: bool
    created_at: datetime

    model_config = {"from_attributes": True}
