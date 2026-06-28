"""Audit-log endpoint (M6 Step 6).

GET /audit
    Paginated list of security/admin events.  Requires ``VIEW_AUDIT``
    permission (admin only).

Query parameters
----------------
event_type  str | None      Exact match on event_type.
actor_id    int | None      Exact match on actor_user_id.
from        datetime | None Lower bound on created_at (inclusive).  Uses the
                            ``from`` alias because ``from`` is a Python keyword;
                            Query(..., alias="from") maps the wire name.
to          datetime | None Upper bound on created_at (inclusive).
limit       int             Page size (default 50, max 200).
offset      int             Zero-based row offset (default 0).

Response
--------
Returns ``AuditLogListResponse`` (items, total, limit, offset), ordered
newest-first.  ``params`` is surfaced as a parsed dict (not a raw string).

Auth: ``VIEW_AUDIT`` (admin) — 403 ``auth.forbidden`` for member/viewer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_view_audit
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.models.user import User
from app.schemas.audit import AuditLogListResponse, AuditLogResponse
from app.services.audit import AuditService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}

router = APIRouter(tags=["audit"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> AuditService:
    """Dependency: build and return an AuditService."""
    return AuditService(db)


@router.get("/audit", response_model=AuditLogListResponse)
def list_audit(
    _admin: Annotated[User, Depends(require_view_audit)],
    service: Annotated[AuditService, Depends(_get_service)],
    event_type: str | None = None,
    actor_id: int | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditLogListResponse:
    """Return a paginated list of security/admin audit events (VIEW_AUDIT only).

    Filters are ANDed; omitted filters are no-ops.  Results are ordered
    newest-first.

    Error codes:
    - 403 ``auth.forbidden`` — caller lacks ``VIEW_AUDIT`` (not admin).
    """
    rows, total = service.list(
        event_type=event_type,
        actor_user_id=actor_id,
        created_from=from_,
        created_to=to,
        limit=limit,
        offset=offset,
    )
    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
