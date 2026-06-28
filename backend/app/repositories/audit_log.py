"""Repository for AuditLog (append-only event store) — M6 Step 6.

Provides two operations only:
  ``append(**fields)``   Insert a row and flush; NO update/delete methods.
  ``list(filters...)``   Return a page of rows (newest-first) + total count.

The append-only constraint is enforced structurally: this class exposes no
update or delete methods.  A blind reviewer can confirm append-only semantics
by inspecting that only ``append`` and ``list`` are present.

All DB access to the ``audit_log`` table must go through this class; route
handlers and services must not issue raw queries against ``audit_log``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditLogRepository:
    """Data-access object for the append-only audit_log table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Write (append only)                                                      #
    # ---------------------------------------------------------------------- #

    def append(
        self,
        *,
        event_type: str,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        params: str | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Insert a new audit-log row and flush (caller commits or not).

        The caller is responsible for committing the transaction.  For the
        failed-login case the caller MUST commit before raising the 401 so the
        row survives the ``get_db`` rollback triggered by the exception.

        Parameters
        ----------
        event_type:
            Stable event identifier (e.g. ``auth.login_failed``).
        actor_user_id:
            PK of the acting user.  NULL for anonymous events (failed logins,
            public token accepts).
        actor_email:
            Denormalized email snapshot.  May be the attempted email for failed
            logins (before we know if the user exists).
        target_type:
            Polymorphic target kind: ``"user"`` / ``"invitation"`` /
            ``"setting"`` / None.
        target_id:
            PK of the affected entity (no FK — polymorphic).
        params:
            JSON-encoded string with structured event detail.
        ip_address:
            Client IP, derived from ``request.client.host`` at call time.

        Returns
        -------
        AuditLog
            The newly-inserted (flushed) ORM object.
        """
        row = AuditLog(
            event_type=event_type,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type=target_type,
            target_id=target_id,
            params=params,
            ip_address=ip_address,
        )
        self._db.add(row)
        self._db.flush()
        return row

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def list(
        self,
        *,
        event_type: str | None = None,
        actor_user_id: int | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        """Return a page of audit rows (newest-first) and the total count.

        Filters are combined with AND; omitted filters are no-ops.

        Parameters
        ----------
        event_type:
            Exact match on ``AuditLog.event_type``.
        actor_user_id:
            Exact match on ``AuditLog.actor_user_id``.
        created_from:
            Lower bound on ``AuditLog.created_at`` (inclusive).
        created_to:
            Upper bound on ``AuditLog.created_at`` (inclusive).
        limit:
            Maximum rows in the page (caller validates max).
        offset:
            Zero-based row offset for pagination.

        Returns
        -------
        (rows, total)
            ``rows``   — the current page, ordered newest-first.
            ``total``  — total matching rows (across all pages).
        """
        # Build base filter statement shared between count + page queries.
        base = select(AuditLog)
        if event_type is not None:
            base = base.where(AuditLog.event_type == event_type)
        if actor_user_id is not None:
            base = base.where(AuditLog.actor_user_id == actor_user_id)
        if created_from is not None:
            base = base.where(AuditLog.created_at >= created_from)
        if created_to is not None:
            base = base.where(AuditLog.created_at <= created_to)

        # Count all matching rows.
        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = int(self._db.execute(count_stmt).scalar_one())

        # Fetch the requested page, newest-first.
        page_stmt = base.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        rows = list(self._db.scalars(page_stmt).all())

        return rows, total
