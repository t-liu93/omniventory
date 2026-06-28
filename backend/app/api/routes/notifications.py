"""In-app notification inbox endpoints (M4 §4.10 / §9 Step 6).

Routes (all under the api_prefix, e.g. /api; all session-authenticated):
    GET  /notifications                      Current user's inbox (newest-first).
    GET  /notifications/unread-count         Badge count (unread rows).
    POST /notifications/{id}/read            Mark one notification read.
    POST /notifications/read-all             Mark all current-user notifications read.

Design notes
------------
- All four endpoints are scoped to the **current user** (``ctx.user``); a user
  can only list, count, or mark their own notifications.
- ``GET /notifications/unread-count`` is declared **before** the ``/{id}/read``
  route so FastAPI does not attempt to parse the literal "unread-count" as an
  integer path parameter.
- ``NotificationService`` is the single entry point; no raw queries in handlers.
- ``params`` is surfaced as a parsed dict (not the raw JSON string) — the service
  layer handles deserialization.
- The ``POST /notifications/{id}/read`` returns the updated ``NotificationResponse``
  (rather than 204) so the client can update the local row immediately without a
  second round-trip.

Error contract:
    401  No/invalid session.
    404  ``notification.not_found`` — id missing or owned by another user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.notification import Notification
from app.schemas.notification import (
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)
from app.services.notification import NotificationService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}

router = APIRouter(tags=["notifications"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> NotificationService:
    """Dependency: build and return a NotificationService."""
    return NotificationService(db)


def _build_response(
    notification: Notification,
    params: dict[str, object] | None,
) -> NotificationResponse:
    """Construct a NotificationResponse from a model instance and parsed params."""
    return NotificationResponse(
        id=notification.id,
        source=notification.source,
        subject_type=notification.subject_type,
        subject_id=notification.subject_id,
        message_code=notification.message_code,
        params=params,
        offset_days=notification.offset_days,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


@router.get("/notifications", response_model=list[NotificationResponse])
def list_notifications(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[NotificationService, Depends(_get_service)],
    unread_only: bool = Query(
        default=False, description="When true, return only unread notifications."
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of notifications to return (1–200, default 50).",
    ),
) -> list[NotificationResponse]:
    """Return the current user's notification inbox, newest-first.

    ``unread_only=true`` restricts the result to rows where ``read_at IS NULL``.
    ``limit`` caps the number of rows (default 50, max 200).

    M6 Step 5 — in-app inbox gating: when the user has opted out of the in-app
    inbox (``notify_in_app=False``), this endpoint returns an empty list.
    Notification rows may still exist in the DB to feed the email digest — a
    deliberate simplification documented in M6 §12.  The bell/badge UI should
    hide itself when the user's ``notify_in_app`` pref is off.
    """
    assert ctx.user is not None  # get_authenticated_context guarantees this
    # M6 §4.5 inbox gate: return [] when user opted out of the in-app inbox.
    if not ctx.user.notify_in_app:
        return []
    pairs = service.list_for_user(ctx.user.id, unread_only=unread_only, limit=limit)
    return [_build_response(n, p) for n, p in pairs]


# NOTE: this route must be declared BEFORE /{id}/read so that FastAPI does not
# attempt to coerce the literal string "unread-count" into an integer.
@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
def get_unread_count(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[NotificationService, Depends(_get_service)],
) -> UnreadCountResponse:
    """Return the number of unread notifications for the current user.

    Used to drive the bell badge in the frontend.  Returns ``{ count: 0 }``
    when there are no unread notifications.

    M6 Step 5 — in-app inbox gating: returns ``{ count: 0 }`` when the user
    has opted out of the in-app inbox (``notify_in_app=False``).
    """
    assert ctx.user is not None
    # M6 §4.5 inbox gate: return 0 when user opted out of the in-app inbox.
    if not ctx.user.notify_in_app:
        return UnreadCountResponse(count=0)
    return UnreadCountResponse(count=service.unread_count(ctx.user.id))


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: int,
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[NotificationService, Depends(_get_service)],
) -> NotificationResponse:
    """Mark a single notification as read and return the updated row.

    The notification must belong to the current user; if the id is missing or
    owned by another user the endpoint returns 404 ``notification.not_found``.

    Idempotent: calling this endpoint on an already-read notification is a
    no-op that returns the row unchanged (the original ``read_at`` is preserved).
    """
    assert ctx.user is not None
    notification, params = service.mark_read(ctx.user.id, notification_id)
    return _build_response(notification, params)


@router.post("/notifications/read-all", response_model=ReadAllResponse)
def mark_all_notifications_read(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[NotificationService, Depends(_get_service)],
) -> ReadAllResponse:
    """Mark all unread notifications for the current user as read.

    Returns ``{ marked: N }`` where ``N`` is the number of rows that were
    actually updated.  Zero means there were no unread notifications.
    """
    assert ctx.user is not None
    count = service.mark_all_read(ctx.user.id)
    return ReadAllResponse(marked=count)
