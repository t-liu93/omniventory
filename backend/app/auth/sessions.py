"""Server-side session management.

Sessions are stored in the ``sessions`` table.  The cookie carries only
the opaque session id — no user data.

Public API
----------
``create(db, user_id)``                          Create a new session row; return its id.
``verify(db, session_id)``                       Look up and validate; return the Session or None.
``revoke(db, session_id)``                       Delete the session row (logout / revocation).
``revoke_all_for_user(db, user_id, *, ...)``     Bulk-delete all sessions for a user (M6 Step 3).
``purge_expired(db)``                            Delete all rows whose ``expires_at`` has passed.

Session lifetime
----------------
M0 uses a fixed expiry (``SESSION_TTL_HOURS`` = 24 h).  Sliding expiry and
"remember me" are deferred to M6.

Expired-session cleanup
-----------------------
``verify`` is a **pure read**: it rejects expired sessions (returns ``None``)
but does NOT delete the row.  Expired rows are cleaned up by ``purge_expired``,
which is called on application startup (lifespan hook in ``app/main.py``).
This avoids the rollback trap where a 401 response from a route handler would
undo a flush-but-not-committed DELETE inside ``verify``.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session as DBSession

from app.models.session import Session

# Fixed session lifetime for M0.  Sliding window and "remember me" → M6.
SESSION_TTL_HOURS: int = 24


def _now_utc() -> datetime:
    """Return the current time in UTC (timezone-aware)."""
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """Ensure ``dt`` is timezone-aware (UTC).

    SQLite stores datetimes without timezone info, so values read back from
    the DB are offset-naive.  This function attaches UTC if the value has no
    tzinfo, making comparisons with offset-aware datetimes safe.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _new_session_id() -> str:
    """Generate a cryptographically random, URL-safe session token."""
    # token_urlsafe(32) gives 256 bits of entropy; more than sufficient.
    return secrets.token_urlsafe(32)


def create(db: DBSession, user_id: int) -> Session:
    """Create a new server-side session for ``user_id``.

    Returns the newly-created ``Session`` ORM object.  The session ``id`` is
    the value to store in the cookie.

    The caller must commit (or yield from ``get_db``) to persist the row.
    """
    now = _now_utc()
    session = Session(
        id=_new_session_id(),
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
        last_seen_at=now,
    )
    db.add(session)
    db.flush()  # Assign id and raise IntegrityError now (before commit).
    return session


def verify(db: DBSession, session_id: str) -> Session | None:
    """Look up and validate a session by its id.

    Returns the ``Session`` ORM object if the session exists and has not
    expired; returns ``None`` if the session is missing or expired.

    This function is a **pure read** — it never mutates the DB.  In
    particular it does NOT delete expired rows; that is handled by
    ``purge_expired`` (called on app startup).  Keeping ``verify`` free of
    writes avoids the rollback trap: when a caller raises ``HTTPException``
    after this function returns ``None``, the ``get_db`` error handler would
    roll back any pending flush, silently undoing a DELETE.

    Note on ``last_seen_at``
    ------------------------
    The model carries a ``last_seen_at`` column as a pre-wired hook for M6's
    sliding-window expiry.  M0 uses fixed expiry, so updating it on every
    authenticated request would be pure write-amplification with no benefit.
    The column is left untouched here; M6 will enable the update when sliding
    expiry is actually implemented.
    """
    session = db.get(Session, session_id)
    if session is None:
        return None

    if _now_utc() >= _as_utc(session.expires_at):
        # Expired — reject without deleting.  purge_expired handles cleanup.
        return None

    return session


def revoke(db: DBSession, session_id: str) -> None:
    """Delete the session row, effectively logging the user out.

    No-op if the session does not exist (idempotent revocation).
    The caller must commit to persist the deletion.
    """
    session = db.get(Session, session_id)
    if session is not None:
        db.delete(session)
        db.flush()


def revoke_all_for_user(
    db: DBSession,
    user_id: int,
    *,
    except_session_id: str | None = None,
) -> int:
    """Bulk-delete all sessions for ``user_id``, optionally keeping one.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    user_id:
        The user whose sessions are revoked.
    except_session_id:
        When provided, this session is kept alive (used by change-password to
        keep the current session while revoking all other sessions).

    Returns the number of rows deleted.

    Implementation notes
    --------------------
    Uses ``synchronize_session=False`` + ``expire_all()`` for the same reason
    as ``purge_expired`` — avoids the tz-naive comparison trap and keeps the
    identity map consistent after a bulk DELETE (M6 §4.3).

    Called by:
    - ``InvitationService.accept_reset`` — revoke ALL sessions for the user.
    - ``InvitationService.change_password`` — revoke OTHER sessions, keep the
      current one (pass ``except_session_id``).
    """
    from sqlalchemy import CursorResult, delete

    stmt = delete(Session).where(Session.user_id == user_id)
    if except_session_id is not None:
        stmt = stmt.where(Session.id != except_session_id)

    raw = db.execute(stmt, execution_options={"synchronize_session": False})
    db.expire_all()
    db.flush()
    cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
    count: int = cursor.rowcount if cursor.rowcount is not None else 0
    return count


def purge_expired(db: DBSession) -> int:
    """Delete all expired session rows.

    Returns the number of rows deleted.  Called on application startup
    (lifespan hook in ``app/main.py``) to keep the sessions table tidy.

    Implementation note
    -------------------
    We use ``synchronize_session=False`` on the bulk DELETE to skip
    SQLAlchemy's in-memory WHERE-clause evaluation.  That evaluation would
    fail with ``TypeError`` when the DB returns offset-naive datetimes
    (SQLite strips tzinfo on round-trip) and we compare them to the
    offset-aware ``now``.  With ``synchronize_session=False`` SQLAlchemy
    issues the SQL DELETE directly; we then call ``db.expire_all()`` so any
    subsequently-accessed objects are refreshed from the DB rather than stale
    identity-map state.
    """
    from sqlalchemy import CursorResult, delete

    now = _now_utc()
    raw = db.execute(
        delete(Session).where(Session.expires_at < now),
        execution_options={"synchronize_session": False},
    )
    db.expire_all()  # Evict stale identity-map entries after the bulk DELETE.
    db.flush()
    # CursorResult.rowcount is the number of affected rows.
    cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
    count: int = cursor.rowcount if cursor.rowcount is not None else 0
    return count
