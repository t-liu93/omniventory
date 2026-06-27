"""Pydantic schemas for invitation, password-reset, and change-password endpoints (M6 Step 3).

Schemas defined here
--------------------
``InvitationCreate``
    Request body for ``POST /invitations``.

``InvitationResponse``
    Response for ``POST /invitations``.  Includes the one-time accept URL and
    whether the email was sent (admin is trusted to see the raw link).

``PendingInvitationResponse``
    One item in the ``GET /invitations`` list.  Does NOT include ``accept_url``
    (the link was already given to the admin at creation time).

``InvitationPublic``
    Response for ``GET /invitations/accept?token=`` (public endpoint, no auth).
    Returns only ``email`` and ``role`` to let the frontend pre-fill the form.

``InvitationAccept``
    Request body for ``POST /invitations/accept`` (public endpoint).

``PasswordResetIssueResponse``
    Response for ``POST /users/{id}/reset-password``.

``PasswordResetPublic``
    Response for ``GET /password-reset/accept?token=`` — returns a masked email.

``PasswordResetAccept``
    Request body for ``POST /password-reset/accept`` (public endpoint).

``PasswordChange``
    Request body for ``POST /auth/change-password`` (any authed user).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Shared validator
# ---------------------------------------------------------------------------


def _non_empty_str(v: str) -> str:
    """Reject empty or whitespace-only strings."""
    if not v or not v.strip():
        raise ValueError("Field must not be empty.")
    return v


# ---------------------------------------------------------------------------
# Invitation schemas
# ---------------------------------------------------------------------------


class InvitationCreate(BaseModel):
    """Request body for ``POST /invitations``."""

    email: str
    role: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _non_empty_str(v)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        return _non_empty_str(v)


class InvitationResponse(BaseModel):
    """Response body for ``POST /invitations``.

    Includes the ``accept_url`` (the one-time link) because the admin is
    trusted and must be able to copy/paste the link for the invitee.
    ``emailed`` indicates whether the link was also sent by email.
    """

    id: int
    email: str
    role: str
    expires_at: datetime
    accept_url: str
    emailed: bool

    model_config = {"from_attributes": True}


class PendingInvitationResponse(BaseModel):
    """One row in the ``GET /invitations`` list of pending invites.

    Does not include ``accept_url`` — the link was surfaced at creation time;
    the admin can re-issue if needed (which replaces the prior link).
    """

    id: int
    email: str
    role: str
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class InvitationPublic(BaseModel):
    """Response for ``GET /invitations/accept?token=`` (public, no auth).

    Lets the frontend pre-fill the form (which email is being invited, and
    what role they'll get) before the user sets their password.
    """

    email: str
    role: str


class InvitationAccept(BaseModel):
    """Request body for ``POST /invitations/accept`` (public, no auth)."""

    token: str
    password: str

    @field_validator("token", "password")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        return _non_empty_str(v)


# ---------------------------------------------------------------------------
# Password reset schemas
# ---------------------------------------------------------------------------


class PasswordResetIssueResponse(BaseModel):
    """Response for ``POST /users/{id}/reset-password``."""

    reset_url: str
    emailed: bool


class PasswordResetPublic(BaseModel):
    """Response for ``GET /password-reset/accept?token=`` (public, no auth).

    Returns a masked email so the user can confirm which account the reset
    link is for, without exposing the full address to an anonymous caller.
    """

    email_masked: str


class PasswordResetAccept(BaseModel):
    """Request body for ``POST /password-reset/accept`` (public, no auth)."""

    token: str
    password: str

    @field_validator("token", "password")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        return _non_empty_str(v)


# ---------------------------------------------------------------------------
# Self change-password schema
# ---------------------------------------------------------------------------


class PasswordChange(BaseModel):
    """Request body for ``POST /auth/change-password`` (any authed user).

    ``current_password`` is verified against the stored hash.
    ``new_password`` replaces it on success.

    On mismatch → 400 ``auth.password_incorrect``.
    On success, the user's other sessions are revoked (the current session
    remains active).
    """

    current_password: str
    new_password: str

    @field_validator("current_password", "new_password")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        return _non_empty_str(v)


# Note: MessageResponse (generic ``{message}`` body) is defined in
# ``app.schemas.auth`` and is reused by invitation/reset routes.
