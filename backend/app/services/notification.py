"""NotificationService — in-app inbox reads and writes (M4 §4.1 / §9 Step 6).

This service encapsulates the inbox-API business logic:
- list notifications for the current user (with optional unread filter + limit)
- return the unread count
- mark one notification as read (raises 404 when missing or not owned by caller)
- mark all notifications as read
- dismiss one notification (soft-dismiss; raises 404 when missing or not
  owned by caller)
- dismiss all notifications (soft-dismiss)

All DB access is delegated to ``NotificationRepository``; no raw queries here.
``params`` deserialization (JSON text → dict) lives here so routes and schemas
receive clean Python dicts, not raw strings.

Soft-dismiss note: ``dismiss`` / ``dismiss_all`` only stamp ``dismissed_at``;
they never touch the dedup or low-stock episode state (see
``NotificationRepository`` module docstring for the full invariant).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.notification import Notification
from app.repositories.notification import NotificationRepository

logger = logging.getLogger(__name__)


def _deserialize_params(notification: Notification) -> dict[str, Any] | None:
    """Return the notification's params as a Python dict, or None.

    The ``params`` column stores a JSON-encoded text blob.  This helper
    parses it so that routes/schemas receive a plain dict.  If the stored
    value is NULL or an empty string, ``None`` is returned.  If the JSON
    is malformed (should not happen in practice — the engine always writes
    valid JSON), a warning is logged and ``None`` is returned defensively.
    """
    raw = notification.params
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning(
            "Notification id=%s has invalid params JSON — returning None.",
            notification.id,
        )
        return None
    if isinstance(parsed, dict):
        result: dict[str, Any] = parsed
        return result
    # Unlikely: the engine only writes dict params.
    logger.warning(
        "Notification id=%s params is not a dict (type=%s) — returning None.",
        notification.id,
        type(parsed).__name__,
    )
    return None


class NotificationService:
    """Business logic for the in-app notification inbox.

    Instantiate once per request with the current DB session.
    """

    def __init__(self, db: Session) -> None:
        self._repo = NotificationRepository(db)
        self._db = db

    def list_for_user(
        self,
        user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[tuple[Notification, dict[str, Any] | None]]:
        """Return notifications for a user, newest-first.

        Returns a list of ``(Notification, parsed_params)`` tuples so the
        caller (route) can build ``NotificationResponse`` objects with the
        deserialized ``params`` dict without an extra parsing step.

        Parameters
        ----------
        user_id:
            Only notifications belonging to this user are returned.
        unread_only:
            When ``True``, only unread rows (``read_at IS NULL``) are included.
        limit:
            Maximum number of rows.  The route layer validates 1 ≤ limit ≤ 200.
        """
        notifications = self._repo.list_for_user(
            user_id,
            unread_only=unread_only,
            limit=limit,
        )
        return [(n, _deserialize_params(n)) for n in notifications]

    def unread_count(self, user_id: int) -> int:
        """Return the number of unread notifications for the given user."""
        return self._repo.unread_count(user_id)

    def mark_read(
        self,
        user_id: int,
        notification_id: int,
    ) -> tuple[Notification, dict[str, Any] | None]:
        """Mark a single notification as read and return it with parsed params.

        Raises
        ------
        AppError(NOTIFICATION_NOT_FOUND, 404)
            When the notification does not exist or belongs to a different user.
        """
        notification = self._repo.mark_read(user_id, notification_id)
        if notification is None:
            raise AppError(
                ErrorCode.NOTIFICATION_NOT_FOUND,
                status_code=404,
                params={"id": notification_id},
            )
        self._db.flush()
        return notification, _deserialize_params(notification)

    def mark_all_read(self, user_id: int) -> int:
        """Mark all unread notifications for a user as read.

        Returns the number of rows actually updated.
        """
        count = self._repo.mark_all_read(user_id)
        self._db.flush()
        return count

    def dismiss(
        self,
        user_id: int,
        notification_id: int,
    ) -> tuple[Notification, dict[str, Any] | None]:
        """Soft-dismiss a single notification and return it with parsed params.

        Raises
        ------
        AppError(NOTIFICATION_NOT_FOUND, 404)
            When the notification does not exist or belongs to a different user.
        """
        notification = self._repo.dismiss(user_id, notification_id)
        if notification is None:
            raise AppError(
                ErrorCode.NOTIFICATION_NOT_FOUND,
                status_code=404,
                params={"id": notification_id},
            )
        self._db.flush()
        return notification, _deserialize_params(notification)

    def dismiss_all(self, user_id: int) -> int:
        """Soft-dismiss all currently-visible notifications for a user.

        Returns the number of rows actually updated.
        """
        count = self._repo.dismiss_all(user_id)
        self._db.flush()
        return count
