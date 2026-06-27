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

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_manage_users
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.user_admin import UserAdminUpdate, UserSummary
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
    _admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[UserAdminService, Depends(_get_service)],
) -> UserResponse:
    """Change a user's role and/or active status (MANAGE_USERS only).

    PATCH semantics: only fields present in the request body are applied;
    omitted fields are untouched.

    Error codes:
    - 404 ``user.not_found``    — no user with that id.
    - 409 ``user.last_admin``   — operation would orphan the household.
    - 422 ``validation.invalid_input`` — unknown role string.
    """
    user = service.update_user(
        user_id,
        role=body.role,
        is_active=body.is_active,
        fields_set=body.model_fields_set,
    )
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    _admin: Annotated[User, Depends(require_manage_users)],
    service: Annotated[UserAdminService, Depends(_get_service)],
) -> None:
    """Delete a user (MANAGE_USERS only).

    Error codes:
    - 404 ``user.not_found``  — no user with that id.
    - 409 ``user.last_admin`` — cannot delete the last active admin.
    """
    service.delete_user(user_id)
