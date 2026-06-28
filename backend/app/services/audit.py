"""AuditService — security/admin audit log (M6 Step 6).

Business-logic facade for writing and querying audit rows.

``AuditService`` is thin: it serialises ``params`` dicts to JSON and delegates
all DB access to ``AuditLogRepository``.  Services and routes import
``AuditService`` and call ``record(...)`` directly.

Commit semantics
----------------
``record`` adds + flushes BUT does **not** commit.  For the vast majority of
events this is correct — the normal request lifecycle commits automatically via
``get_db``.

**Exception — failed logins (auth.login_failed)**: the login route raises a
401 ``AppError`` after recording the failed attempt; ``get_db`` rolls back on
any exception, which would discard the flushed-but-uncommitted audit row.  The
login route MUST call ``db.commit()`` immediately after ``record(...)`` and
BEFORE raising the 401.  The subsequent rollback by ``get_db`` is then a
harmless no-op (nothing pending).  See ``app/api/routes/auth.py`` for the
implementation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.repositories.audit_log import AuditLogRepository

logger = logging.getLogger(__name__)


class AuditService:
    """Facade for writing and querying the append-only audit_log table."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = AuditLogRepository(db)

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def record(
        self,
        event_type: str,
        *,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        params: dict[str, object] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Write one audit-log row and flush (caller commits).

        Serialises *params* to a compact JSON string (mirroring how
        ``Notification.params`` is stored).  ``None`` params → NULL in the DB.

        The caller is responsible for committing the transaction.  For
        failed-login events the caller MUST commit BEFORE raising the 401 (see
        module docstring).

        Parameters
        ----------
        event_type:
            Stable event identifier (e.g. ``"auth.login_failed"``).
        actor_user_id:
            PK of the acting user; NULL for anonymous events.
        actor_email:
            Denormalized email snapshot (may differ from ``users.email`` for
            failed-login attempts where no user row exists).
        target_type:
            Polymorphic target kind: ``"user"`` / ``"invitation"`` / etc.
        target_id:
            PK of the affected entity (no hard FK).
        params:
            Structured event detail dict; serialised to JSON string internally.
        ip_address:
            Client IP at the time of the event.

        Returns
        -------
        AuditLog
            The newly-inserted (flushed) ORM object.
        """
        params_str = json.dumps(params, separators=(",", ":")) if params is not None else None
        return self._repo.append(
            event_type=event_type,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type=target_type,
            target_id=target_id,
            params=params_str,
            ip_address=ip_address,
        )

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

        Delegates to ``AuditLogRepository.list``; see that method's docstring
        for full parameter documentation.
        """
        return self._repo.list(
            event_type=event_type,
            actor_user_id=actor_user_id,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )
