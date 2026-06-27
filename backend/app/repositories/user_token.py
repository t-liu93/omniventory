"""Repository for UserToken (one-time invite / password-reset tokens) — M6 Step 3.

All DB access to the ``user_tokens`` table goes through this class.  Route
handlers and services must not issue raw queries against ``user_tokens``; they
call ``UserTokenRepository`` methods.

Design
------
- "Pending" = ``consumed_at IS NULL AND expires_at > now``.
- SELECT predicates that compare ``expires_at`` to now work correctly at the
  SQL level because SQLAlchemy strips the timezone when binding the parameter
  for SQLite (which stores datetimes as naive UTC strings).  The comparison is
  therefore a fair string/datetime comparison.
- Bulk DELETE uses ``synchronize_session=False`` + ``expire_all()`` to avoid
  the tz-naive identity-map evaluation trap (same pattern as
  ``sessions.purge_expired``).

Public methods
--------------
``create(...)``                           Insert a new token row.
``get_by_token_hash(token_hash)``         Fetch by hash; returns ``UserToken | None``.
``get_pending_invite_by_email(email)``    Pending invite for email; ``None`` if absent.
``list_pending_invites()``                All current pending invites.
``list_pending_resets_for_user(user_id)`` All pending resets for a user.
``get_by_id(id)``                         Fetch by PK; returns ``UserToken | None``.
``mark_consumed(token, when)``            Set ``consumed_at`` and flush.
``delete(token)``                         Delete the row and flush.
``purge_expired(now)``                    Bulk-delete expired rows; returns count.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.user_token import UserToken


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo if the datetime is offset-naive (SQLite round-trip)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class UserTokenRepository:
    """Data-access object for one-time user tokens."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        purpose: str,
        token_hash: str,
        expires_at: datetime,
        email: str | None = None,
        role: str | None = None,
        user_id: int | None = None,
        created_by: int | None = None,
    ) -> UserToken:
        """Insert a new token row and flush.

        The caller must commit (or rely on ``get_db``'s auto-commit).

        Parameters
        ----------
        purpose:
            ``"invite"`` or ``"password_reset"`` (app-validated by callers).
        token_hash:
            sha256 hex of the raw token (from ``app.auth.tokens.mint_token``).
        expires_at:
            Hard expiry timestamp (UTC-aware).
        email:
            Invitee email (invites only).
        role:
            Invited role (invites only).
        user_id:
            Target user id (password_reset only).
        created_by:
            Admin user id who issued the token.
        """
        token = UserToken(
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
            email=email,
            role=role,
            user_id=user_id,
            created_by=created_by,
        )
        self._db.add(token)
        self._db.flush()
        return token

    def mark_consumed(self, token: UserToken, when: datetime) -> None:
        """Set ``token.consumed_at`` to ``when`` and flush.

        Once consumed a token is permanently spent — ``validate_*`` methods
        reject any row with a non-NULL ``consumed_at``.
        """
        token.consumed_at = when
        self._db.flush()

    def delete(self, token: UserToken) -> None:
        """Delete the token row and flush (used by revoke / replace-prior-invite)."""
        self._db.delete(token)
        self._db.flush()

    def purge_expired(self, now: datetime) -> int:
        """Delete all token rows whose ``expires_at`` has passed.

        Returns the number of rows deleted.  Called on application startup
        alongside ``sessions.purge_expired`` to keep the table tidy.

        Uses ``synchronize_session=False`` to skip SQLAlchemy's in-memory
        WHERE evaluation — avoids the tz-naive comparison trap (see module
        docstring).  ``expire_all()`` evicts stale identity-map entries.
        """
        from sqlalchemy import CursorResult

        raw = self._db.execute(
            delete(UserToken).where(UserToken.expires_at < now),
            execution_options={"synchronize_session": False},
        )
        self._db.expire_all()
        self._db.flush()
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        count: int = cursor.rowcount if cursor.rowcount is not None else 0
        return count

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_id(self, token_id: int) -> UserToken | None:
        """Return a token by primary key, or None if not found."""
        return self._db.get(UserToken, token_id)

    def get_by_token_hash(self, token_hash: str) -> UserToken | None:
        """Return a token by its hash, or None if not found."""
        stmt = select(UserToken).where(UserToken.token_hash == token_hash)
        return self._db.execute(stmt).scalar_one_or_none()

    def get_pending_invite_by_email(self, email: str) -> UserToken | None:
        """Return the single pending invite for *email*, or None.

        "Pending" = ``purpose="invite"``, ``consumed_at IS NULL``, and
        ``expires_at`` is still in the future.  The comparison on
        ``expires_at`` is done at the SQL level (safe for SQLite, see module
        docstring).

        The lower-case comparison via ``func.lower`` is a belt-and-suspenders
        guard; the service already lower-cases the email before storing it.
        """
        now = datetime.now(UTC)
        stmt = select(UserToken).where(
            UserToken.purpose == "invite",
            func.lower(UserToken.email) == email.lower(),
            UserToken.consumed_at.is_(None),
            UserToken.expires_at > now,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_pending_invites(self) -> list[UserToken]:
        """Return all currently pending invites (not consumed, not expired)."""
        now = datetime.now(UTC)
        stmt = (
            select(UserToken)
            .where(
                UserToken.purpose == "invite",
                UserToken.consumed_at.is_(None),
                UserToken.expires_at > now,
            )
            .order_by(UserToken.created_at)
        )
        return list(self._db.scalars(stmt).all())

    def list_pending_resets_for_user(self, user_id: int) -> list[UserToken]:
        """Return all pending password-reset tokens for *user_id*.

        Used by ``InvitationService.issue_reset`` to revoke any prior pending
        reset before minting a new one (prevents a user from having multiple
        live reset links at once).
        """
        now = datetime.now(UTC)
        stmt = select(UserToken).where(
            UserToken.purpose == "password_reset",
            UserToken.user_id == user_id,
            UserToken.consumed_at.is_(None),
            UserToken.expires_at > now,
        )
        return list(self._db.scalars(stmt).all())
