"""User-administration endpoints (M6 Step 2).

All endpoints require a valid session.  Listing users is open to any
authenticated user (VIEW); mutations (get-one, patch, delete) require
``MANAGE_USERS`` (admin only).

Routes (all under the api_prefix, e.g. /api):
    GET    /users           List all users incl. inactive (any authed user).
    GET    /users/{id}      Get one user (MANAGE_USERS / admin).
    PATCH  /users/{id}      Change role and/or active status (MANAGE_USERS).
    DELETE /users/{id}      Delete a user (MANAGE_USERS).

Error contract:
    401  No/invalid session.
    403  Insufficient role (auth.forbidden).
    404  User not found (user.not_found).
    409  Last-admin guard triggered (user.last_admin).
    422  Invalid role string.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_manage_users
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.user_admin import UserAdminUpdate, UserSummary
from app.services.audit import AuditService
from app.services.user_admin import UserAdminService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/users", tags=["users"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> UserAdminService:
    """Dependency: build and return a UserAdminService."""
    return UserAdminService(db)


@router.get("", response_model=list[UserSummary])
def list_users(
    _current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[UserAdminService, Depends(_get_service)],
) -> list[UserSummary]:
    """Return a list of all users (any authenticated user).

    Returns id, email, role, and is_active for each user.  Used by the
    admin user-management page and as the data source for responsible-party
    pickers (M6 §4.1 / §4.9).
    """
    users = service.list_users()
    return [UserSummary.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    _admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[UserAdminService, Depends(_get_service)],
) -> UserResponse:
    """Return the full representation of a user (MANAGE_USERS only).

    404 ``user.not_found`` when no user with that id exists.
    """
    user = service.get_user(user_id)
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    body: UserAdminUpdate,
    request: Request,
    admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[UserAdminService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> UserResponse:
    """Change a user's role and/or active status (MANAGE_USERS only).

    PATCH semantics: only fields present in the request body are applied;
    omitted fields are untouched.

    Emits up to two audit events on success (one per changed field):
    - ``user.role_changed``    — params ``{"old_role":…, "new_role":…}``.
    - ``user.deactivated``     — when ``is_active`` toggled False.
    - ``user.reactivated``     — when ``is_active`` toggled True.

    Error codes:
    - 404 ``user.not_found``    — no user with that id.
    - 409 ``user.last_admin``   — operation would orphan the household.
    - 422 ``validation.invalid_input`` — unknown role string.
    """
    # Capture the old state before the update so we can compute what changed.
    target = service.get_user(user_id)
    old_role = target.role
    old_is_active = target.is_active

    user = service.update_user(
        user_id,
        role=body.role,
        is_active=body.is_active,
        fields_set=body.model_fields_set,
    )

    # Emit audit rows for each field that actually changed.
    audit = AuditService(db)
    ip = request.client.host if request.client else None
    if user.role != old_role:
        audit.record(
            "user.role_changed",
            actor_user_id=admin.id,
            actor_email=admin.email,
            target_type="user",
            target_id=user.id,
            params={"old_role": old_role, "new_role": user.role},
            ip_address=ip,
        )
    if user.is_active != old_is_active:
        event = "user.reactivated" if user.is_active else "user.deactivated"
        audit.record(
            event,
            actor_user_id=admin.id,
            actor_email=admin.email,
            target_type="user",
            target_id=user.id,
            ip_address=ip,
        )

    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[UserAdminService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Delete a user (MANAGE_USERS only).

    Emits ``user.deleted`` on success.  The target user's email is captured
    BEFORE deletion as a snapshot (the row is gone after the flush).

    Error codes:
    - 404 ``user.not_found``  — no user with that id.
    - 409 ``user.last_admin`` — cannot delete the last active admin.
    """
    # Capture identity before deletion so the audit row has the target email.
    target = service.get_user(user_id)
    target_id_snapshot = target.id
    target_email_snapshot = target.email

    service.delete_user(user_id)

    ip = request.client.host if request.client else None
    # When the admin deletes their own account actor_user_id must be NULL:
    # the actor row has just been deleted, so inserting a FK reference to it
    # would raise FOREIGN KEY constraint failed.  actor_email is still captured
    # as a snapshot so the row remains auditable.
    actor_id = None if target_id_snapshot == admin.id else admin.id
    AuditService(db).record(
        "user.deleted",
        actor_user_id=actor_id,
        actor_email=admin.email,
        target_type="user",
        target_id=target_id_snapshot,
        params={"email": target_email_snapshot},
        ip_address=ip,
    )
