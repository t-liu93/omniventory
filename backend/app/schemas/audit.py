"""Pydantic schemas for the audit-log API (M6 Step 6).

``AuditLogResponse``
    Wire representation of a single audit-log row.  ``params`` is surfaced as a
    parsed ``dict`` (the DB stores a compact JSON string — the validator
    deserialises it transparently).

``AuditLogListResponse``
    Paginated envelope returned by ``GET /audit``.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, field_validator


class AuditLogResponse(BaseModel):
    """Wire representation of one audit-log row.

    ``params`` is stored as a compact JSON string in the DB; the
    ``_parse_params`` validator deserialises it to a dict (or ``None``) before
    the value is serialised back to JSON by the response encoder.  This mirrors
    how ``notifications.params`` is handled in the M4 notification schemas.
    """

    id: int
    event_type: str
    actor_email: str | None = None
    target_type: str | None = None
    target_id: int | None = None
    params: dict[str, object] | None = None
    ip_address: str | None = None
    created_at: datetime

    @field_validator("params", mode="before")
    @classmethod
    def _parse_params(cls, v: object) -> dict[str, object] | None:
        """Deserialise a JSON string to a dict (or pass through an existing dict)."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
                return None
            except (json.JSONDecodeError, ValueError):
                return None
        if isinstance(v, dict):
            return v
        return None

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    """Paginated envelope for ``GET /audit``."""

    items: list[AuditLogResponse]
    total: int
    limit: int
    offset: int
