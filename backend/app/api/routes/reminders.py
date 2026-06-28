"""Reminders API endpoint (M4 §4.7 / §4.10 / §9 Steps 3 + 7).

Routes (all under the api_prefix, e.g. /api; session-authenticated):
    POST  /reminders/run   Trigger ``ReminderEngine.run_scan()`` on demand and
                           return per-source created-notification counts.

This endpoint is the primary way to demo the engine without waiting for the
daily APScheduler job (Step 5).  It is safe to call multiple times — the engine
is idempotent (a second call in the same day creates no new notifications for
the same lots).

Dispatch order (F1 fix — M4 §2: "Network I/O happens after commit"):
1. ``ReminderEngine.run_scan()`` creates notification rows (not yet committed).
2. ``get_db`` dependency commits on handler success — rows are now durable.
3. ``build_dispatcher(db).dispatch(...)`` runs external channels (email, etc.)
   AFTER commit.  Channel errors are best-effort and never crash this handler.
4. ``get_db`` commits again on return — ``notification_deliveries`` rows are
   persisted.

Error contract:
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_manage_settings
from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.notifications.dispatcher import build_dispatcher, publish_mqtt_state
from app.schemas.reminders import ReminderRunSummary
from app.services.reminder_engine import ReminderEngine

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
}

router = APIRouter(tags=["reminders"], responses=_ERROR_RESPONSES)


@router.post("/reminders/run", response_model=ReminderRunSummary)
def run_reminders(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    _: Annotated[User, Depends(require_manage_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> ReminderRunSummary:
    """Trigger the reminder engine scan on demand.

    Evaluates all sources (best_before, warranty, low_stock) across all active
    users and creates idempotent in-app notification rows.  Returns the count
    of *newly created* rows per source; zero means "nothing new this scan"
    (either no lots qualify or they were already notified).

    Re-running is safe: the engine uses a unique ``(user_id, dedup_key)`` to
    prevent duplicate notifications.

    After the handler returns, ``get_db`` auto-commits so notification rows are
    durable.  External channel dispatch (email digest etc.) runs next via
    ``build_dispatcher``; delivery rows are committed by the subsequent
    ``get_db`` commit.

    Note: dispatch happens AFTER ``get_db`` commits (F1 fix).  To achieve this
    within a single ``get_db`` dependency scope the handler explicitly commits,
    dispatches, then returns — ``get_db`` will commit again on exit (idempotent
    for an empty transaction).
    """
    summary = ReminderEngine(db).run_scan()

    # Auto-reconcile shopping list rows right after the scan (best-effort +
    # savepoint-isolated so a reconcile failure never discards the scan's
    # notification rows).  The engine stays decoupled: callers invoke both;
    # the engine never imports ShoppingListService (M7 §2 locked decisions).
    try:
        import logging as _logging

        from app.services.shopping_list import ShoppingListService

        with db.begin_nested():
            ShoppingListService(db).reconcile_auto_items()
    except Exception:
        _logging.getLogger(__name__).warning(
            "reconcile_auto_items after /reminders/run failed (best-effort).",
            exc_info=True,
        )

    # Commit notification rows so they are durable before any network I/O.
    db.commit()

    # Dispatch external channels post-commit (F1: network I/O after commit).
    build_dispatcher(db).dispatch(summary.new_notifications, include_email_digest=True)
    # delivery rows will be committed by get_db on handler return.

    # Publish MQTT state counts after scan (best-effort, post-commit).
    publish_mqtt_state(db)

    return ReminderRunSummary(
        best_before=summary.best_before,
        warranty=summary.warranty,
        low_stock=summary.low_stock,
    )
