"""Notification schemas for the in-app inbox API (M4 §4.11 / §9 Step 6).

``NotificationResponse``
    Returned by the inbox list and mark-read endpoints.  Carries ``message_code``
    + ``params`` (parsed dict) so the frontend can localise without server text.
    Deliberately omits any rendered human-readable message — in-app notifications
    stay code+params per the M1.5 wire/display split.

``UnreadCountResponse``
    Returned by ``GET /notifications/unread-count``.  Feeds the bell badge.

``ReadAllResponse``
    Returned by ``POST /notifications/read-all``.  Confirms how many rows
    were actually marked read (useful for debugging; ignored by the bell).

``DismissAllResponse``
    Returned by ``POST /notifications/dismiss-all``.  Confirms how many rows
    were actually soft-dismissed.  Mirrors ``ReadAllResponse``.

Note: ``NotificationResponse`` deliberately does NOT expose ``dismissed_at``.
Dismissed rows are never returned by the inbox (``list_for_user`` excludes
them), and the single-dismiss endpoint's caller only needs to drop the row
locally -- keeping the response shape unchanged avoids needless contract
churn.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    """Public shape of a single in-app notification row.

    Fields
    ------
    id              Surrogate PK.
    source          ``best_before`` / ``warranty`` / ``low_stock``.
    subject_type    ``instance`` / ``definition``.
    subject_id      PK of the referenced lot or definition.
    message_code    i18n code — frontend localises with ``t(message_code, params)``.
    params          Deserialized render params (dict) or ``None``.
                    The backend stores params as a JSON text blob; this schema
                    surfaces the parsed dict so the frontend does not need to
                    JSON-parse again.
    offset_days     Low-stock only: which repeat offset this row represents.
                    ``None`` for date sources.
    created_at      Row-creation timestamp (UTC).
    read_at         When the notification was first marked read; ``None`` if
                    still unread.
    """

    model_config = {"from_attributes": True}

    id: int
    source: str
    subject_type: str
    subject_id: int
    message_code: str
    params: dict[str, object] | None
    offset_days: int | None
    created_at: datetime
    read_at: datetime | None


class UnreadCountResponse(BaseModel):
    """Badge count for the notification bell.

    ``count`` is the number of notifications where ``read_at IS NULL`` for the
    current user.
    """

    count: int


class ReadAllResponse(BaseModel):
    """Result of ``POST /notifications/read-all``.

    ``marked`` is the number of rows that were actually updated (rows that were
    unread before the call).  Zero means there was nothing to mark.
    """

    marked: int


class DismissAllResponse(BaseModel):
    """Result of ``POST /notifications/dismiss-all``.

    ``dismissed`` is the number of rows that were actually soft-dismissed
    (rows that were still visible before the call).  Zero means there was
    nothing to dismiss.  Mirrors ``ReadAllResponse``.
    """

    dismissed: int
