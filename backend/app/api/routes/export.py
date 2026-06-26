"""Export endpoint (M5 Step 7).

``GET /export/{entity}?format=csv|json``
    Session-authenticated.  Streams item_definitions, stock_instances, or
    locations as a downloadable file in CSV or JSON format.

    Path parameter
    --------------
    entity (str)
        One of ``item_definitions``, ``stock_instances``, ``locations``.

    Query parameter
    ---------------
    format (str, default "csv")
        ``csv`` or ``json``.

    Response
    --------
    200  ``StreamingResponse`` with the appropriate media type and a
         ``Content-Disposition: attachment`` header.

Error contract
--------------
    401  No/invalid session.
    422  Unknown entity or unknown format (``validation.invalid_input``).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.services.export import ExportService

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(tags=["export"], responses=_ERROR_RESPONSES)


@router.get(
    "/export/{entity}",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Streamed file download (CSV or JSON).",
            "content": {
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "application/json": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def export_entity(
    entity: str,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    db: Annotated[Session, Depends(get_db)],
    fmt: Annotated[
        str,
        Query(
            alias="format",
            description=("Output format: ``csv`` or ``json``. Defaults to ``csv`` when omitted."),
        ),
    ] = "csv",
) -> StreamingResponse:
    """Stream item_definitions, stock_instances, or locations as a file download.

    Foreign keys are exported as *id + resolved name* pairs (e.g.
    ``category_id``, ``category_name``).  A NULL FK yields an empty name in
    CSV and ``null`` in JSON.  ``custom_fields`` is a JSON string column.
    Tags are comma-joined into a single ``tags`` column.

    Bad *entity* or *format* → 422 ``validation.invalid_input``.
    """
    service = ExportService(db)
    # Validation (and AppError on bad inputs) happens synchronously here,
    # before the StreamingResponse is created — so the error handler catches it.
    data_iter = service.export(entity, fmt)

    today = date.today().isoformat()  # "YYYY-MM-DD" — stable, locale-independent
    filename = f"{entity}-{today}.{fmt}"

    media_type = "application/json" if fmt == "json" else "text/csv"

    return StreamingResponse(
        content=data_iter,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
