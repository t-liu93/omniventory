"""Repository for the StockInstance table.

Pure data access — no business rules here.  Business logic (serial⇒qty=1
enforcement, mode validation, default-location resolution) lives in
``app.services.stock_instance``.

Public methods
--------------
get(id)                              Return a StockInstance by PK, or None.
list_all(q, definition_id, location_id)
                                     Filtered flat list.
create(definition_id, ...)           Insert and flush a new StockInstance.
update(instance, ...)                Apply partial field updates.
delete(instance)                     Delete a StockInstance row.
has_instances_at_location(location_id)
                                     True if any instance is assigned to that location.
has_instances_for_definition(definition_id)
                                     True if any instance references that definition.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.stock_instance import StockInstance


class StockInstanceRepository:
    """Data-access object for the stock_instances table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, instance_id: int) -> StockInstance | None:
        """Return a StockInstance by PK, or None if not found."""
        return self._db.get(StockInstance, instance_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        definition_id: int | None = None,
        location_id: int | None = None,
    ) -> list[StockInstance]:
        """Return a filtered flat list of stock instances.

        Parameters
        ----------
        q
            Case-insensitive substring match against ``serial``, ``model_number``,
            or ``manufacturer``.
        definition_id
            When provided, filter to only instances of this definition.
        location_id
            When provided, filter to only instances at this location.
        """
        stmt = select(StockInstance)

        if q is not None:
            pattern = func.lower(q)
            stmt = stmt.where(
                or_(
                    func.lower(StockInstance.serial).contains(pattern),
                    func.lower(StockInstance.model_number).contains(pattern),
                    func.lower(StockInstance.manufacturer).contains(pattern),
                )
            )

        if definition_id is not None:
            stmt = stmt.where(StockInstance.definition_id == definition_id)

        if location_id is not None:
            stmt = stmt.where(StockInstance.location_id == location_id)

        stmt = stmt.order_by(StockInstance.id)
        return list(self._db.scalars(stmt).all())

    def list_active_lots_for_definition(self, definition_id: int) -> list[StockInstance]:
        """Return lots for a definition with quantity > 0, ordered by (received_at, id).

        This is the FIFO ordering key used by consume_fifo (M2 §4.3):
        oldest received_at first, with id as the tie-breaker.

        Only lots with quantity > 0 are returned — zero-quantity lots are
        retained in the DB (M2 §2 "empty lots are kept") but skipped by FIFO.

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance)
            .where(
                StockInstance.definition_id == definition_id,
                StockInstance.quantity > 0,
            )
            .order_by(StockInstance.received_at, StockInstance.id)
        )
        return list(self._db.scalars(stmt).all())

    def has_instances_at_location(self, location_id: int) -> bool:
        """Return True if any stock instance is assigned to the given location."""
        stmt = select(StockInstance.id).where(StockInstance.location_id == location_id).limit(1)
        return self._db.scalars(stmt).first() is not None

    def has_instances_for_definition(self, definition_id: int) -> bool:
        """Return True if any stock instance references the given definition."""
        stmt = select(StockInstance.id).where(StockInstance.definition_id == definition_id).limit(1)
        return self._db.scalars(stmt).first() is not None

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        definition_id: int,
        location_id: int | None = None,
        quantity: Decimal | None = None,
        stock_level: str | None = None,
        serial: str | None = None,
        model_number: str | None = None,
        manufacturer: str | None = None,
        warranty_expires: date | None = None,
        warranty_details: str | None = None,
        purchase_price: Decimal | None = None,
        purchase_date: date | None = None,
        purchase_source: str | None = None,
    ) -> StockInstance:
        """Insert a new StockInstance and flush to get its PK.

        ``quantity`` is nullable: pass ``None`` for level/none-mode lots or when
        the service will set it via ledger recompute (exact-mode).
        ``stock_level`` is nullable: pass the validated level string for
        level-mode lots; None otherwise.
        """
        instance = StockInstance(
            definition_id=definition_id,
            location_id=location_id,
            quantity=quantity,
            stock_level=stock_level,
            serial=serial,
            model_number=model_number,
            manufacturer=manufacturer,
            warranty_expires=warranty_expires,
            warranty_details=warranty_details,
            purchase_price=purchase_price,
            purchase_date=purchase_date,
            purchase_source=purchase_source,
        )
        self._db.add(instance)
        self._db.flush()
        return instance

    def update(
        self,
        instance: StockInstance,
        *,
        set_location_id: bool = False,
        location_id: int | None = None,
        set_stock_level: bool = False,
        stock_level: str | None = None,
        set_serial: bool = False,
        serial: str | None = None,
        set_model_number: bool = False,
        model_number: str | None = None,
        set_manufacturer: bool = False,
        manufacturer: str | None = None,
        set_warranty_expires: bool = False,
        warranty_expires: date | None = None,
        set_warranty_details: bool = False,
        warranty_details: str | None = None,
        set_purchase_price: bool = False,
        purchase_price: Decimal | None = None,
        set_purchase_date: bool = False,
        purchase_date: date | None = None,
        set_purchase_source: bool = False,
        purchase_source: str | None = None,
    ) -> StockInstance:
        """Apply partial field updates to a StockInstance.

        Nullable fields use an explicit ``set_*`` flag to distinguish
        "don't change" from "explicitly set to NULL".

        Note: ``quantity`` is intentionally absent from this method (M2 §2).
        An ``exact`` lot's quantity is changed only through the movement
        ledger (``StockMovementService``, Step 4).
        """
        if set_location_id:
            instance.location_id = location_id
        if set_stock_level:
            instance.stock_level = stock_level
        if set_serial:
            instance.serial = serial
        if set_model_number:
            instance.model_number = model_number
        if set_manufacturer:
            instance.manufacturer = manufacturer
        if set_warranty_expires:
            instance.warranty_expires = warranty_expires
        if set_warranty_details:
            instance.warranty_details = warranty_details
        if set_purchase_price:
            instance.purchase_price = purchase_price
        if set_purchase_date:
            instance.purchase_date = purchase_date
        if set_purchase_source:
            instance.purchase_source = purchase_source
        self._db.flush()
        return instance

    def delete(self, instance: StockInstance) -> None:
        """Delete a StockInstance row (caller must ensure it is safe to delete)."""
        self._db.delete(instance)
        self._db.flush()
