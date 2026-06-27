"""Invitation, password-reset, and change-password endpoints (M6 Step 3).

This module registers:

Invitations (under ``/invitations``)
-------------------------------------
POST   /invitations              Create an invite (MANAGE_USERS).
GET    /invitations              List pending invites (MANAGE_USERS).
DELETE /invitations/{id}         Revoke an invite (MANAGE_USERS).
GET    /invitations/accept       Validate token; return {email, role} (public).
POST   /invitations/accept       Accept invite; create user (public).

Password reset (under ``/users`` + ``/password-reset``)
---------------------------------------------------------
POST   /users/{id}/reset-password   Issue a reset link (MANAGE_USERS).
GET    /password-reset/accept       Validate token; return masked email (public).
POST   /password-reset/accept       Accept reset; set new password (public).

The change-password endpoint (``POST /auth/change-password``) is registered
separately in ``app/api/routes/auth.py`` (alongside the other auth routes).

All non-public endpoints require a valid session.  Public endpoints (accept
paths) work with no session cookie.

Error contract
--------------
400  ``auth.invalid_token``        Token invalid/expired/consumed.
401  No/invalid session (non-public routes).
403  ``auth.forbidden``            Insufficient role.
404  ``invitation.not_found``      Revoke on missing invite.
     ``user.not_found``            Reset on missing user.
409  ``user.email_exists``         Invite for an already-registered email.
422  Validation error.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_manage_users
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import MessageResponse, UserResponse
from app.schemas.invitation import (
    InvitationAccept,
    InvitationCreate,
    InvitationPublic,
    InvitationResponse,
    PasswordResetAccept,
    PasswordResetIssueResponse,
    PasswordResetPublic,
    PendingInvitationResponse,
)
from app.services.invitation import InvitationService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(tags=["invitations"], responses=_ERROR_RESPONSES)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_service(db: Session = Depends(get_db)) -> InvitationService:
    return InvitationService(db)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post("/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_invitation(
    body: InvitationCreate,
    request: Request,
    admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[InvitationService, Depends(_get_service)],
) -> InvitationResponse:
    """Create an invitation for *email* with *role* (MANAGE_USERS only).

    Returns the one-time accept URL (the admin copies this and sends it to the
    invitee, or it is emailed automatically when SMTP is configured).

    The ``accept_url`` is derived from the incoming request's base URL —
    zero-config for the single-container deployment.

    Error codes:
    - 409 ``user.email_exists`` — *email* is already a registered user.
    - 422 ``validation.invalid_input`` — unknown role string.
    """
    token, raw_token, emailed = service.create_invite(
        body.email,
        body.role,
        created_by=admin.id,
        request=request,
    )

    # Build the accept_url from the raw_token; app_origin is the same
    # base URL the service used when sending the email.
    app_origin = str(request.base_url).rstrip("/")
    accept_url = f"{app_origin}/invite/accept?token={raw_token}"

    return InvitationResponse(
        id=token.id,
        email=token.email,  # type: ignore[arg-type]
        role=token.role,  # type: ignore[arg-type]
        expires_at=token.expires_at,
        accept_url=accept_url,
        emailed=emailed,
    )


@router.get("/invitations", response_model=list[PendingInvitationResponse])
def list_invitations(
    _admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[InvitationService, Depends(_get_service)],
) -> list[PendingInvitationResponse]:
    """List all currently pending invitations (MANAGE_USERS only)."""
    tokens = service.list_pending()
    return [PendingInvitationResponse.model_validate(t) for t in tokens]


@router.delete("/invitations/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    invite_id: int,
    _admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[InvitationService, Depends(_get_service)],
) -> None:
    """Revoke a pending invitation by id (MANAGE_USERS only).

    Error codes:
    - 404 ``invitation.not_found`` — no invite with that id.
    """
    service.revoke(invite_id)


@router.get("/invitations/accept", response_model=InvitationPublic)
def get_invitation_accept(
    token: str,
    service: Annotated[InvitationService, Depends(_get_service)],
) -> InvitationPublic:
    """Validate an invite token and return the invitee's email and role (public).

    Called by the frontend to pre-fill the set-password form before the user
    clicks "Create account".  No session required.

    Error codes:
    - 400 ``auth.invalid_token`` — token invalid, expired, or already consumed.
    """
    user_token = service.validate_invite(token)
    return InvitationPublic(
        email=user_token.email,  # type: ignore[arg-type]
        role=user_token.role,  # type: ignore[arg-type]
    )


@router.post(
    "/invitations/accept", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def post_invitation_accept(
    body: InvitationAccept,
    service: Annotated[InvitationService, Depends(_get_service)],
) -> UserResponse:
    """Accept an invite and create the new user account (public, no auth).

    Does NOT auto-login — the frontend redirects to the login page after success.

    Error codes:
    - 400 ``auth.invalid_token`` — token invalid, expired, consumed, or email
      race (email was registered between invite creation and accept).
    """
    user = service.accept_invite(body.token, body.password)
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/reset-password",
    response_model=PasswordResetIssueResponse,
)
def issue_password_reset(
    user_id: int,
    request: Request,
    admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[InvitationService, Depends(_get_service)],
) -> PasswordResetIssueResponse:
    """Issue a password-reset link for *user_id* (MANAGE_USERS only).

    Returns the one-time reset URL and whether it was emailed.

    Error codes:
    - 404 ``user.not_found`` — no user with that id.
    """
    reset_url, emailed = service.issue_reset(
        user_id,
        created_by=admin.id,
        request=request,
    )
    return PasswordResetIssueResponse(reset_url=reset_url, emailed=emailed)


@router.get("/password-reset/accept", response_model=PasswordResetPublic)
def get_password_reset_accept(
    token: str,
    service: Annotated[InvitationService, Depends(_get_service)],
) -> PasswordResetPublic:
    """Validate a password-reset token and return a masked email (public).

    Called by the frontend to confirm which account the reset is for before
    showing the set-new-password form.  No session required.

    Error codes:
    - 400 ``auth.invalid_token`` — token invalid, expired, or consumed.
    """
    email_masked = service.get_reset_email_masked(token)
    return PasswordResetPublic(email_masked=email_masked)


@router.post("/password-reset/accept", response_model=MessageResponse)
def post_password_reset_accept(
    body: PasswordResetAccept,
    service: Annotated[InvitationService, Depends(_get_service)],
) -> MessageResponse:
    """Accept a password-reset token and set the new password (public, no auth).

    On success: the user's password is updated; all their existing sessions
    are revoked (they must re-authenticate with the new password).

    Error codes:
    - 400 ``auth.invalid_token`` — token invalid, expired, consumed, or user missing.
    """
    service.accept_reset(body.token, body.password)
    return MessageResponse(message="Password reset successfully.")
