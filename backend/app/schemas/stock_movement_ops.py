"""Pydantic request schemas for stock movement operation endpoints (M2 Step 4).

Each schema corresponds to one intention-revealing operation endpoint (M2 §4.6):

- ``IntakeRequest``   POST /instances/{id}/intake
- ``DiscardRequest``  POST /instances/{id}/discard
- ``AdjustRequest``   POST /instances/{id}/adjust
- ``MoveRequest``     POST /instances/{id}/move
- ``ConsumeRequest``  POST /definitions/{id}/consume
- ``ReverseRequest``  POST /movements/{id}/reverse

Design notes (M2 §4.8):
- Quantities are ``Decimal`` (never float) per roadmap §2.9.
- ``occurred_at`` is optional on all operation bodies; omitting it uses the
  server-side default (current time).
- ``note`` is optional on all operations (a plain string; rich
  attachments/tags are M5).
- These are thin wire DTOs; all business logic lives in ``StockMovementService``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class IntakeRequest(BaseModel):
    """Body for POST /instances/{id}/intake.

    Adds ``quantity`` units to the lot's ledger.
    """

    quantity: Decimal
    occurred_at: datetime | None = None
    note: str | None = None


class DiscardRequest(BaseModel):
    """Body for POST /instances/{id}/discard.

    Removes ``quantity`` units from the lot's ledger (write-off / disposal).
    """

    quantity: Decimal
    occurred_at: datetime | None = None
    note: str | None = None


class AdjustRequest(BaseModel):
    """Body for POST /instances/{id}/adjust.

    Sets the lot's quantity to the given absolute ``quantity`` (stock-take).
    The signed delta is computed by the service: ``delta = quantity − current``.
    """

    quantity: Decimal  # The absolute counted value.
    occurred_at: datetime | None = None
    note: str | None = None


class MoveRequest(BaseModel):
    """Body for POST /instances/{id}/move.

    Moves the whole lot to ``to_location_id``.  Records a ``move`` movement
    with ``delta = 0`` and updates the lot's ``location_id``.
    """

    to_location_id: int
    occurred_at: datetime | None = None
    note: str | None = None


class ConsumeRequest(BaseModel):
    """Body for POST /definitions/{id}/consume.

    Consumes ``quantity`` units from the definition's lots in FIFO order
    (oldest received_at first).
    """

    quantity: Decimal
    occurred_at: datetime | None = None
    note: str | None = None


class ReverseRequest(BaseModel):
    """Body for POST /movements/{id}/reverse.

    Appends a compensating ``correction`` movement that undoes the target.
    ``note`` is optional; no other field is needed (the delta is derived
    from the target movement).
    """

    note: str | None = None
