"""Repository for the Notification table (M4 §4.1 / §9 Step 3 + Step 4).

All DB access to the ``notifications`` table goes through this class.  Route
handlers and services must not issue raw queries against ``notifications``; they
call ``NotificationRepository`` methods.

Public methods
--------------
``create_if_absent(...)``
    Idempotent insert by ``(user_id, dedup_key)``.  Returns the
    ``(Notification, created: bool)`` tuple — ``created=True`` when a new row
    was inserted, ``created=False`` when a matching row already existed.

    Implementation strategy: SELECT first, INSERT inside a SAVEPOINT on miss.
    Using a SAVEPOINT (nested transaction via ``Session.begin_nested()``) means
    a unique-constraint violation only rolls back the savepoint, not the outer
    transaction.  This is critical for the event-hook path (Step 4) where the
    notification INSERT shares a transaction with a stock movement: a plain
    ``session.rollback()`` would destroy the movement data too (F2 fix).

``open_low_stock_opener(user_id, definition_id) -> Notification | None``
    Return the open opener (offset_days=0, resolved_at NULL) for a (user, def)
    pair, or None if no episode is currently open.

``open_low_stock_openers(user_id) -> list[Notification]``
    Return all open low-stock openers for a user (for the "close recovered
    episodes" step of the scan).

``mark_resolved(opener) -> None``
    Stamp ``resolved_at`` on the opener and all its open sibling repeat rows
    (same user_id + subject_id + episode_started_on, resolved_at NULL).

``list_for_user(user_id, unread_only=False, limit=50) -> list[Notification]``
    Newest-first inbox listing.  Always excludes soft-dismissed rows
    (``dismissed_at IS NOT NULL``); ``unread_only`` additionally excludes read
    rows.

``unread_count(user_id) -> int``
    Badge count.  Excludes soft-dismissed rows in addition to read rows.

``mark_read(user_id, notification_id) -> Notification | None``
``mark_all_read(user_id) -> int``
    In-app read state.

``dismiss(user_id, notification_id) -> Notification | None``
    Soft-dismiss a single notification owned by ``user_id``.  Hides the row
    from ``list_for_user`` / ``unread_count`` only; does not touch the dedup
    or low-stock episode lookups (see the "Notification hygiene" note below).

``dismiss_all(user_id) -> int``
    Soft-dismiss every currently-visible notification owned by ``user_id``.

Notification hygiene (soft-dismiss) — the non-negotiable invariant
--------------------------------------------------------------------
Soft-dismiss (``dismissed_at``) hides a row from the inbox ONLY.  It must
never be consulted by ``_get_by_dedup`` / ``create_if_absent`` (a dismissed
row must still anchor its dedup key, so a still-active source does not
re-spam the user) or by the low-stock episode helpers
(``open_low_stock_opener``, ``open_low_stock_openers``,
``count_low_stock_openers_on``, ``mark_resolved`` — a dismissed opener must
still hold live episode state, so a rescan does not open a duplicate
episode).  Only ``list_for_user`` and ``unread_count`` filter on
``dismissed_at``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)


class NotificationRepository:
    """Data-access object for the notifications table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create_if_absent(
        self,
        *,
        user_id: int,
        source: str,
        subject_type: str,
        subject_id: int,
        dedup_key: str,
        message_code: str,
        params: dict[str, Any] | None = None,
        episode_started_on: date | None = None,
        offset_days: int | None = None,
    ) -> tuple[Notification, bool]:
        """Insert a notification row only when the dedup key is absent for this user.

        Returns
        -------
        (notification, created)
            ``created=True``  -> a new row was inserted and flushed.
            ``created=False`` -> an existing row with this dedup key was returned
                               unchanged (the scan is idempotent).

        Implementation
        --------------
        SELECT -> miss -> INSERT inside a SAVEPOINT + flush.

        The SAVEPOINT (``Session.begin_nested()``) isolates the INSERT so that
        when the unique index ``uq_notifications_user_dedup`` fires an
        IntegrityError (rare race condition), only the savepoint rolls back --
        the outer transaction (which may hold a stock movement) is unaffected.
        A plain ``session.rollback()`` would roll back the entire transaction
        and destroy any co-committed movement data (M4 Step 4 F2 fix).
        """
        # SELECT first -- the common case after the first scan is a hit (fast path).
        existing = self._get_by_dedup(user_id, dedup_key)
        if existing is not None:
            return existing, False

        # INSERT inside a SAVEPOINT so a unique-constraint race only rolls back
        # the savepoint, not the enclosing transaction.
        params_text: str | None = json.dumps(params) if params is not None else None
        notification = Notification(
            user_id=user_id,
            source=source,
            subject_type=subject_type,
            subject_id=subject_id,
            dedup_key=dedup_key,
            message_code=message_code,
            params=params_text,
            episode_started_on=episode_started_on,
            offset_days=offset_days,
        )
        try:
            with self._db.begin_nested():
                self._db.add(notification)
                # flush() inside the nested block materialises the INSERT so
                # the unique-constraint check happens now (within the savepoint).
                self._db.flush()
            return notification, True
        except IntegrityError:
            # Unique constraint hit: another concurrent call inserted the same
            # dedup key between our SELECT and INSERT.  The savepoint has been
            # rolled back automatically by the context manager; the outer
            # transaction remains intact.  Re-fetch and return the winning row.
            existing = self._get_by_dedup(user_id, dedup_key)
            if existing is not None:
                return existing, False
            raise  # Unexpected integrity error -- re-raise.

    def delete_for_subject(self, subject_type: str, subject_id: int) -> int:
        """Bulk-delete all notification rows for a given (subject_type, subject_id).

        Intended for cleanup when the subject itself is deleted (e.g. a
        maintenance schedule is hard-deleted).  Without this call, orphaned
        notification rows with a stale dedup key persist in the bell and the
        dedup logic silently suppresses new notifications for any new row that
        reuses the deleted subject's PK and the same ``next_due_date``.

        Deliberately **not** user-scoped: one subject (e.g. a maintenance
        schedule routed by responsible party) can fire notifications for
        multiple users, and deleting the subject must clean up *all* of them.
        Do not add a ``user_id`` filter here — doing so would re-orphan other
        users' rows.

        Parameters
        ----------
        subject_type:
            The value to match against ``notifications.subject_type``
            (e.g. ``"maintenance_schedule"``).
        subject_id:
            The value to match against ``notifications.subject_id`` (the
            deleted row's PK).

        Returns
        -------
        int
            Number of rows deleted (0 when nothing matched).

        Implementation follows the ``ShoppingListRepository.clear_purchased``
        idiom: SELECT-count first (reliable cross-dialect rowcount), then a
        bulk DELETE with ``synchronize_session="fetch"``, then flush.
        """
        count_stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.subject_type == subject_type,
                Notification.subject_id == subject_id,
            )
        )
        count: int = self._db.execute(count_stmt).scalar_one()
        if count == 0:
            return 0

        bulk_stmt = (
            delete(Notification)
            .where(
                Notification.subject_type == subject_type,
                Notification.subject_id == subject_id,
            )
            .execution_options(synchronize_session="fetch")
        )
        self._db.execute(bulk_stmt)
        self._db.flush()
        return count

    def mark_resolved(self, opener: Notification) -> None:
        """Close a low-stock episode by stamping resolved_at on the opener and its open repeats.

        Sets ``resolved_at`` on:
        - The opener row itself.
        - All sibling repeat rows sharing the same (user_id, subject_id,
          episode_started_on) that are still open (resolved_at IS NULL).

        Uses UTC now as the resolved_at timestamp.  The value is internal
        episode bookkeeping; the frontend formats displayed timestamps in the
        user's locale, so UTC is appropriate here.

        Flushes but does not commit (the caller's transaction boundary controls
        the commit).
        """
        now_utc = datetime.now(tz=UTC)

        # Fetch the opener and all open sibling repeats in one query.
        stmt = select(Notification).where(
            Notification.user_id == opener.user_id,
            Notification.subject_id == opener.subject_id,
            Notification.episode_started_on == opener.episode_started_on,
            Notification.source == "low_stock",
            Notification.resolved_at.is_(None),
        )
        rows = self._db.execute(stmt).scalars().all()
        for row in rows:
            row.resolved_at = now_utc
        self._db.flush()

    # ---------------------------------------------------------------------- #
    # Read: low-stock episode helpers                                          #
    # ---------------------------------------------------------------------- #

    def count_low_stock_openers_on(self, user_id: int, subject_id: int, anchor_date: date) -> int:
        """Return the count of low-stock opener rows for (user, definition, date).

        Counts ALL opener rows (offset_days=0) for the given (user_id, subject_id,
        episode_started_on == anchor_date) regardless of ``resolved_at``.  Used by
        the engine to assign a sequential suffix to same-day re-opened episodes so
        that the new opener gets a non-colliding dedup key.

        Parameters
        ----------
        user_id:
            The recipient user.
        subject_id:
            The definition ID (subject_type is implicitly 'definition' for
            low-stock episodes).
        anchor_date:
            The calendar date to count openers for (matches ``episode_started_on``).
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.source == "low_stock",
                Notification.subject_type == "definition",
                Notification.subject_id == subject_id,
                Notification.offset_days == 0,
                Notification.episode_started_on == anchor_date,
            )
        )
        return self._db.execute(stmt).scalar_one()

    def open_low_stock_opener(self, user_id: int, definition_id: int) -> Notification | None:
        """Return the open low-stock opener for (user, definition), or None.

        An "open opener" is a row with:
        - source = 'low_stock'
        - offset_days = 0  (distinguishes the opener from repeat rows)
        - subject_type = 'definition'
        - subject_id = definition_id
        - resolved_at IS NULL  (episode is still active)
        """
        stmt = select(Notification).where(
            Notification.user_id == user_id,
            Notification.source == "low_stock",
            Notification.subject_type == "definition",
            Notification.subject_id == definition_id,
            Notification.offset_days == 0,
            Notification.resolved_at.is_(None),
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def open_low_stock_openers(self, user_id: int) -> list[Notification]:
        """Return all open low-stock openers for a user.

        Used by the scan to find definitions whose episodes should be closed
        because they are no longer low.  Returns only opener rows (offset_days=0)
        that are still open (resolved_at IS NULL).
        """
        stmt = select(Notification).where(
            Notification.user_id == user_id,
            Notification.source == "low_stock",
            Notification.offset_days == 0,
            Notification.resolved_at.is_(None),
        )
        return list(self._db.execute(stmt).scalars().all())

    # ---------------------------------------------------------------------- #
    # Read: inbox API (Step 6)                                                #
    # ---------------------------------------------------------------------- #

    def list_for_user(
        self,
        user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:
        """Return notifications for a user, newest-first.

        Parameters
        ----------
        user_id:
            Only rows belonging to this user are returned.
        unread_only:
            When ``True``, filter to rows where ``read_at IS NULL``.
        limit:
            Maximum number of rows to return.  Callers should validate
            the value before calling (route layer enforces 1 ≤ limit ≤ 200).

        Always excludes soft-dismissed rows (``dismissed_at IS NOT NULL``) --
        dismiss is meant to empty the inbox view, not just mark rows read.

        Ordering is ``created_at DESC, id DESC`` — the secondary ``id DESC``
        break ties deterministically when multiple rows land in the same
        second (e.g. a batch scan).
        """
        stmt = (
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.dismissed_at.is_(None),
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        return list(self._db.execute(stmt).scalars().all())

    def unread_count(self, user_id: int) -> int:
        """Return the number of unread notifications for a user.

        Counts rows where ``read_at IS NULL`` for the given user, excluding
        soft-dismissed rows (a dismissed row never counts toward the badge,
        read or not).
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        return self._db.execute(stmt).scalar_one()

    def mark_read(self, user_id: int, notification_id: int) -> Notification | None:
        """Stamp ``read_at`` on a notification owned by the given user.

        Only marks the row if it belongs to ``user_id``; returns ``None`` when
        the row does not exist or belongs to a different user (the route layer
        raises 404 in that case).

        Idempotency: if the row is already read, the existing ``read_at`` is
        preserved (not refreshed).  This avoids spurious updates and keeps
        the timestamp semantically accurate ("when was it first read").

        Flushes but does not commit.
        """
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        notification = self._db.execute(stmt).scalar_one_or_none()
        if notification is None:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.now(tz=UTC)
            self._db.flush()
        return notification

    def mark_all_read(self, user_id: int) -> int:
        """Mark all unread notifications for a user as read.

        Returns the number of rows affected (rows that were actually unread
        and had ``read_at`` stamped on them).

        Strategy: count unread rows first, then bulk-UPDATE.  The pre-count is
        a cheap index-only scan (``ix_notifications_user_read_at``); returning
        the count lets the caller report "N marked" without an extra query after
        the UPDATE.  The two operations are within the same transaction, so the
        count cannot race with an external writer inside a single-user deployment.

        Flushes but does not commit.
        """
        count_stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        affected: int = self._db.execute(count_stmt).scalar_one()

        if affected == 0:
            return 0

        now_utc = datetime.now(tz=UTC)
        update_stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now_utc)
            .execution_options(synchronize_session="fetch")
        )
        self._db.execute(update_stmt)
        self._db.flush()
        return affected

    def dismiss(self, user_id: int, notification_id: int) -> Notification | None:
        """Stamp ``dismissed_at`` on a notification owned by the given user.

        Only dismisses the row if it belongs to ``user_id``; returns ``None``
        when the row does not exist or belongs to a different user (the route
        layer raises 404 in that case).

        Idempotency: if the row is already dismissed, the existing
        ``dismissed_at`` is preserved (not refreshed) and the row is returned
        unchanged.

        IMPORTANT: this is a soft-dismiss.  It hides the row from
        ``list_for_user`` / ``unread_count`` only.  It does NOT delete the
        row, and it must NEVER be mirrored onto ``_get_by_dedup`` /
        ``create_if_absent`` or the low-stock episode helpers -- a dismissed
        row still anchors its dedup key and still holds live episode state.

        Flushes but does not commit.
        """
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        notification = self._db.execute(stmt).scalar_one_or_none()
        if notification is None:
            return None
        if notification.dismissed_at is None:
            notification.dismissed_at = datetime.now(tz=UTC)
            self._db.flush()
        return notification

    def dismiss_all(self, user_id: int) -> int:
        """Soft-dismiss all currently-visible notifications for a user.

        Returns the number of rows affected (rows that were not already
        dismissed and had ``dismissed_at`` stamped on them).

        Strategy mirrors ``mark_all_read``: count first, then bulk-UPDATE.
        The two operations are within the same transaction.

        IMPORTANT: soft-dismiss only.  Does not delete rows; does not touch
        the dedup or low-stock episode state (see ``dismiss`` docstring).

        Flushes but does not commit.
        """
        count_stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.dismissed_at.is_(None),
            )
        )
        affected: int = self._db.execute(count_stmt).scalar_one()

        if affected == 0:
            return 0

        now_utc = datetime.now(tz=UTC)
        update_stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.dismissed_at.is_(None),
            )
            .values(dismissed_at=now_utc)
            .execution_options(synchronize_session="fetch")
        )
        self._db.execute(update_stmt)
        self._db.flush()
        return affected

    # ---------------------------------------------------------------------- #
    # Read (internal helpers)                                                  #
    # ---------------------------------------------------------------------- #

    def _get_by_dedup(self, user_id: int, dedup_key: str) -> Notification | None:
        """Return an existing notification by ``(user_id, dedup_key)``, or None."""
        stmt = select(Notification).where(
            Notification.user_id == user_id,
            Notification.dedup_key == dedup_key,
        )
        return self._db.execute(stmt).scalar_one_or_none()
