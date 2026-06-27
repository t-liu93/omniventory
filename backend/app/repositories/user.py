"""Repository for User accounts.

All DB access to the ``users`` table goes through this class.  Route handlers
and services must not issue raw queries against ``users``; they call
``UserRepository`` methods.

Public methods
--------------
``get_by_id(id)``                                       Fetch by PK; returns ``User | None``.
``get_by_email(email)``                                 Fetch by (lowercased) email; returns ``User | None``.
``create(email, hash, role, is_active)``                Insert a new user row.
``count()``                                             Return total user count (used by bootstrap guard).
``list_active()``                                       Return all active users (recipients for M4 §4.2).
``list_all()``                                          Return ALL users incl. inactive, ordered by id (M6 §4.1).
``set_role(user, role)``                                Set user.role and flush (M6 §4.1).
``set_active(user, is_active)``                         Set user.is_active and flush (M6 §4.1).
``delete(user)``                                        Delete the user row and flush (M6 §4.1).
``count_active_admins()``                               Count users with role='admin' AND is_active=True (M6 §4.1).
``set_preferred_language(user, lang)``                  Update the user's preferred_language and flush.
``set_reminder_best_before_lead_days(user, days)``      Update the per-user best-before lead override and flush.
``set_reminder_warranty_lead_days(user, days)``         Update the per-user warranty lead override and flush.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Data-access object for User accounts."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, user_id: int) -> User | None:
        """Return a User by primary key, or None if not found."""
        return self._db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Return a User by email (case-insensitive match), or None."""
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return self._db.execute(stmt).scalar_one_or_none()

    def create(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "admin",
        is_active: bool = True,
    ) -> User:
        """Insert and return a new User row.

        ``email`` is stored lower-cased for consistent case-insensitive lookup.
        ``password_hash`` must already be hashed via ``app.auth.passwords``.
        The caller must commit (or flush within a ``get_db`` transaction).
        """
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            role=role,
            is_active=is_active,
        )
        self._db.add(user)
        self._db.flush()
        return user

    def count(self) -> int:
        """Return the total number of user rows."""
        result = self._db.execute(select(func.count()).select_from(User))
        value = result.scalar()
        return int(value) if value is not None else 0

    def list_active(self) -> list[User]:
        """Return all users with ``is_active=True``, ordered by id.

        Used by the reminder engine (M4 §4.2) to determine the recipient set
        for each scan.  In M4 this is typically a single admin; the structure
        scales and M6 can narrow the set further.
        """
        stmt = select(User).where(User.is_active.is_(True)).order_by(User.id)
        return list(self._db.scalars(stmt).all())

    def list_all(self) -> list[User]:
        """Return ALL users including inactive, ordered by id.

        Used by the admin user-management page (M6 §4.1) and as the source
        for responsible-party pickers (all users, not just active ones).
        """
        stmt = select(User).order_by(User.id)
        return list(self._db.scalars(stmt).all())

    def set_role(self, user: User, role: str) -> User:
        """Set ``user.role`` to *role* and flush.

        DB access only — the caller is responsible for validating *role*
        against ``VALID_ROLES`` (M6 §2.11 / §4.1).
        The caller must commit (or rely on ``get_db``'s auto-commit).
        """
        user.role = role
        self._db.flush()
        return user

    def set_active(self, user: User, is_active: bool) -> User:
        """Set ``user.is_active`` to *is_active* and flush.

        DB access only — no guard logic here; last-admin enforcement lives
        in ``UserAdminService`` (M6 §4.1).
        The caller must commit (or rely on ``get_db``'s auto-commit).
        """
        user.is_active = is_active
        self._db.flush()
        return user

    def delete(self, user: User) -> None:
        """Delete the user row and flush.

        DB access only — last-admin guard must be enforced by the caller
        before calling this method.
        The caller must commit (or rely on ``get_db``'s auto-commit).
        """
        self._db.delete(user)
        self._db.flush()

    def count_active_admins(self) -> int:
        """Return the count of users with ``role='admin'`` AND ``is_active=True``.

        Used by ``UserAdminService`` to enforce the last-admin guard (M6 §4.1
        / §5): the household must always retain at least one active admin.
        """
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        value = self._db.execute(stmt).scalar()
        return int(value) if value is not None else 0

    def set_preferred_language(self, user: User, language: str | None) -> User:
        """Update the user's preferred_language and flush.

        Pass ``None`` to explicitly unset the preference (→ NULL in DB),
        which re-enables the client-side resolution chain.
        The caller must commit (or rely on ``get_db``'s auto-commit on
        response).
        """
        user.preferred_language = language
        self._db.flush()
        return user

    def set_reminder_best_before_lead_days(self, user: User, days: int | None) -> User:
        """Update the user's per-user best-before lead-time override and flush.

        Pass ``None`` to explicitly clear the override (→ NULL in DB), which
        causes the engine to fall through to the global default (§4.3 resolution
        chain: per-item > per-user > global).  Pass an integer ``≥ 0`` to set
        the override (Pydantic ``ge=0`` is the sole guard; no DB CHECK constraint).
        The caller must commit (or rely on ``get_db``'s auto-commit on response).
        """
        user.reminder_best_before_lead_days = days
        self._db.flush()
        return user

    def set_reminder_warranty_lead_days(self, user: User, days: int | None) -> User:
        """Update the user's per-user warranty-expiry lead-time override and flush.

        Pass ``None`` to explicitly clear the override (→ NULL in DB), which
        causes the engine to fall through to the global default (§4.3 resolution
        chain: per-item > per-user > global).  Pass an integer ``≥ 0`` to set
        the override (Pydantic ``ge=0`` is the sole guard; no DB CHECK constraint).
        The caller must commit (or rely on ``get_db``'s auto-commit on response).
        """
        user.reminder_warranty_lead_days = days
        self._db.flush()
        return user

    def set_password_hash(self, user: User, password_hash: str) -> User:
        """Set the user's password_hash and flush.

        Called by the invitation/password service (M6 Step 3) after validating
        a one-time reset token or after verifying the current password for a
        self change-password.  The caller must commit (or rely on ``get_db``).

        DB access only — the caller is responsible for all business-logic
        validation (token validity, current-password check, etc.).
        """
        user.password_hash = password_hash
        self._db.flush()
        return user
