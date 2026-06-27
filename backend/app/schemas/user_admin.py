"""Pydantic schemas for user-administration endpoints (M6 Step 2).

Schemas defined here
--------------------
``UserSummary``
    Lightweight read-only view of a user (id, email, role, is_active).
    Returned by ``GET /users`` and used as the source for responsible-party
    pickers.  Full user data (timestamps, prefs) is available via
    ``UserResponse`` on ``GET /users/{id}``.

``UserAdminUpdate``
    PATCH body for ``PATCH /users/{id}``.  Both ``role`` and ``is_active``
    are optional; omitting a field is a true no-op (PATCH semantics via
    ``model_fields_set``).  ``role`` is validated against ``VALID_ROLES``
    when present and non-null.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.auth.permissions import VALID_ROLES


class UserSummary(BaseModel):
    """Lightweight user representation for list views and pickers.

    Contains only the fields needed to display a user in a table row or
    a picker dropdown.  Full details (timestamps, preferences) are on
    ``UserResponse`` (``GET /users/{id}``).
    """

    id: int
    email: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserAdminUpdate(BaseModel):
    """PATCH body for ``PATCH /users/{id}``.

    PATCH semantics (null-vs-omitted)
    ----------------------------------
    All fields default to ``None``.  The route inspects ``model_fields_set``
    to distinguish "field was omitted" from "field was explicitly set".
    - **Omitted** field → no-op (existing value unchanged).
    - **Non-null value** → validated + applied.
    - **Null value** → treated as omitted (no semantic meaning for either
      field; neither ``role=null`` nor ``is_active=null`` makes sense as a
      target state and are therefore silently ignored).

    Role validation
    ---------------
    When ``role`` is present and non-null it must be one of the three fixed
    role strings (``admin`` / ``member`` / ``viewer``); an unknown value is
    rejected with 422 ``validation.invalid_input``.
    """

    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str | None) -> str | None:
        """Reject unknown role strings; allow None (means 'omitted / no-op')."""
        if v is not None and v not in VALID_ROLES:
            valid = ", ".join(sorted(VALID_ROLES))
            raise ValueError(f"role must be one of: {valid}")
        return v
