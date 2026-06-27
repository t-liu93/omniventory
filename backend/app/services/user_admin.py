"""UserAdminService — user-administration business logic (M6 Step 2).

Provides
--------
``list_users()``                Return all users (including inactive).
``get_user(user_id)``          Fetch one user by id; raises 404 if missing.
``update_user(...)``           PATCH role/is_active with the last-admin guard.
``delete_user(user_id)``       Delete a user with the last-admin guard.

Last-admin guard (§4.1 / §5 — the easy-to-get-wrong logic)
-----------------------------------------------------------
The household must **never** lose its last active admin.  An operation that
would reduce ``count_active_admins()`` from 1 to 0 is blocked with
``AppError(ErrorCode.USER_LAST_ADMIN, status_code=409)``.

Concretely, a user currently counts as an **active admin** when
``user.role == "admin" and user.is_active is True``.  The guard fires when:

- The user currently counts as an active admin, AND
- After the operation they would no longer count (role demoted, deactivated,
  or deleted), AND
- They are the *last* active admin (``count_active_admins() == 1``).

The guard is evaluated on the **resulting** state for PATCH (combined
role+is_active changes are considered together) and before the delete.
Pure Python logic — no DB-side CHECK constraint (roadmap §2.11).

All DB access is delegated to ``UserRepository``; no raw queries here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.repositories.user import UserRepository


class UserAdminService:
    """Business-logic facade for admin user-management operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = UserRepository(db)

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def list_users(self) -> list[User]:
        """Return all users including inactive, ordered by id."""
        return self._repo.list_all()

    def get_user(self, user_id: int) -> User:
        """Return a user by primary key.

        Raises
        ------
        AppError(USER_NOT_FOUND, 404)
            When no user with *user_id* exists.
        """
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise AppError(ErrorCode.USER_NOT_FOUND, status_code=404)
        return user

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def update_user(
        self,
        user_id: int,
        *,
        role: str | None,
        is_active: bool | None,
        fields_set: set[str],
    ) -> User:
        """Apply a PATCH update to the user's role and/or active status.

        Only fields present in *fields_set* (and with non-None values) are
        applied; the rest are untouched.

        Parameters
        ----------
        user_id:
            PK of the target user.
        role:
            New role string (must already be validated by the Pydantic schema).
            Ignored when ``"role" not in fields_set`` or ``role is None``.
        is_active:
            New active flag.  Ignored when ``"is_active" not in fields_set``
            or ``is_active is None``.
        fields_set:
            The set of field names explicitly present in the request payload
            (from ``UserAdminUpdate.model_fields_set``).

        Raises
        ------
        AppError(USER_NOT_FOUND, 404)
            When no user with *user_id* exists.
        AppError(USER_LAST_ADMIN, 409)
            When the operation would remove the last active admin.
        """
        user = self.get_user(user_id)

        # Compute the resulting role and is_active *after* this PATCH.
        # Only consider a field "changing" when it is in fields_set AND non-None.
        effective_role = role if ("role" in fields_set and role is not None) else user.role
        effective_is_active = (
            is_active if ("is_active" in fields_set and is_active is not None) else user.is_active
        )

        # Last-admin guard: would this change reduce active-admin count to 0?
        self._enforce_last_admin_guard(user, effective_role, effective_is_active)

        # Apply the changes.
        if "role" in fields_set and role is not None:
            self._repo.set_role(user, role)
        if "is_active" in fields_set and is_active is not None:
            self._repo.set_active(user, is_active)

        return user

    def delete_user(self, user_id: int) -> None:
        """Delete a user by primary key.

        Raises
        ------
        AppError(USER_NOT_FOUND, 404)
            When no user with *user_id* exists.
        AppError(USER_LAST_ADMIN, 409)
            When the user is the last active admin.
        """
        user = self.get_user(user_id)
        # After delete the user would no longer count as active admin at all.
        self._enforce_last_admin_guard(user, resulting_role=None, resulting_is_active=False)
        self._repo.delete(user)

    # ---------------------------------------------------------------------- #
    # Internal                                                                 #
    # ---------------------------------------------------------------------- #

    def _enforce_last_admin_guard(
        self,
        user: User,
        resulting_role: str | None,
        resulting_is_active: bool,
    ) -> None:
        """Raise USER_LAST_ADMIN/409 if the operation would orphan the household.

        A user currently counts as an active admin when:
            ``user.role == "admin" and user.is_active is True``

        After the operation they would count only when:
            ``resulting_role == "admin" and resulting_is_active is True``

        If they currently count but would no longer, and they are the only
        active admin (``count_active_admins() == 1``), the operation is blocked.
        """
        current_is_active_admin = user.role == "admin" and user.is_active
        resulting_is_active_admin = resulting_role == "admin" and resulting_is_active

        if (
            current_is_active_admin
            and not resulting_is_active_admin
            and self._repo.count_active_admins() == 1
        ):
            raise AppError(ErrorCode.USER_LAST_ADMIN, status_code=409)
