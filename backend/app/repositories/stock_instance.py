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
from sqlalchemy.orm import Session, joinedload

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
        """Return lots for a definition with quantity > 0, ordered by the FEFO key.

        This is the FEFO ordering key used by consume_fifo (M3 §4.3):
        nearest best_before_date first (dated lots before NULL/never-expiring
        lots), then oldest received_at, then stable id as the tie-breaker.

        NULLS-LAST is expressed portably via a leading ``best_before_date IS
        NULL`` boolean (0 = dated, 1 = NULL) rather than a dialect-specific
        ``NULLS LAST`` clause — this works correctly on both SQLite (which
        sorts NULLs first under plain ASC) and Postgres (roadmap §2.11).

        Only lots with quantity > 0 are returned — zero-quantity lots are
        retained in the DB (M2 §2 "empty lots are kept") but skipped by FEFO.

        The WHERE clause is unchanged from M2; only the ORDER BY changes.

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance)
            .where(
                StockInstance.definition_id == definition_id,
                StockInstance.quantity > 0,
            )
            .order_by(
                StockInstance.best_before_date.is_(
                    None
                ),  # 0=dated first, 1=NULL last (portable NULLS LAST)
                StockInstance.best_before_date,  # nearest expiry first
                StockInstance.received_at,  # then oldest received (M2 tie-break)
                StockInstance.id,  # then stable id
            )
        )
        return list(self._db.scalars(stmt).all())

    def sum_quantity_for_definition(self, definition_id: int) -> Decimal:
        """Return the SUM of quantity across all lots for a definition.

        Uses COALESCE so that a definition with no lots (or all-NULL quantities)
        returns Decimal("0") rather than None.

        Only ``exact``-mode lots carry a numeric quantity; ``level``/``none``
        lots have NULL which the SUM naturally skips.

        Pure data access — no business rules here.
        """
        stmt = select(func.coalesce(func.sum(StockInstance.quantity), 0)).where(
            StockInstance.definition_id == definition_id
        )
        result = self._db.execute(stmt).scalar_one()
        return Decimal(str(result))

    def definition_has_low_level_lot(self, definition_id: int) -> bool:
        """Return True if any lot for the definition has stock_level == 'low'.

        Used by the low-stock scan for ``level``-mode definitions (M2 §4.5).

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance.id)
            .where(
                StockInstance.definition_id == definition_id,
                StockInstance.stock_level == "low",
            )
            .limit(1)
        )
        return self._db.scalars(stmt).first() is not None

    def list_live_with_best_before(self) -> list[StockInstance]:
        """Return all live lots that have a ``best_before_date`` set.

        Live lot criterion (M4 §4.4): ``quantity IS NULL OR quantity > 0``.
        NULL quantity ⇒ level/none-mode lot (present but unquantified) — counted
        as live.  Zero quantity ⇒ depleted exact-mode lot — excluded.

        The lot's definition is eager-loaded via joinedload to avoid N+1 queries
        (the engine reads ``lot.definition.name`` and
        ``lot.definition.reminder_lead_days`` for every lot).

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance)
            .where(
                StockInstance.best_before_date.is_not(None),
                (StockInstance.quantity.is_(None)) | (StockInstance.quantity > 0),
            )
            .options(joinedload(StockInstance.definition))
            .order_by(StockInstance.best_before_date, StockInstance.id)
        )
        return list(self._db.scalars(stmt).unique().all())

    def list_live_with_warranty(self) -> list[StockInstance]:
        """Return all live lots that have a ``warranty_expires`` date set.

        Live lot criterion (M4 §4.4): ``quantity IS NULL OR quantity > 0``.
        NULL quantity ⇒ level/none-mode lot — counted as live.
        Zero quantity ⇒ depleted exact-mode lot — excluded.

        The lot's definition is eager-loaded via joinedload to avoid N+1 queries
        (the engine reads ``lot.definition.name`` and
        ``lot.definition.reminder_lead_days`` for every lot).

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance)
            .where(
                StockInstance.warranty_expires.is_not(None),
                (StockInstance.quantity.is_(None)) | (StockInstance.quantity > 0),
            )
            .options(joinedload(StockInstance.definition))
            .order_by(StockInstance.warranty_expires, StockInstance.id)
        )
        return list(self._db.scalars(stmt).unique().all())

    def list_expiring(self, cutoff_date: date) -> list[StockInstance]:
        """Return lots whose best_before_date is not NULL and <= cutoff_date.

        Filter (M3 §4.4 / §2 "live stock"):
            best_before_date IS NOT NULL
            AND best_before_date <= cutoff_date
            AND (quantity IS NULL OR quantity > 0)

        The ``quantity IS NULL`` arm keeps level/none-mode lots (present-but-
        unquantified); the ``> 0`` arm drops fully-consumed exact-mode lots
        so a depleted batch does not haunt the list.

        Ordered soonest/most-overdue first (``ORDER BY best_before_date, id``)
        so expired lots naturally lead (their date is earliest).

        The lot's definition is eager-loaded via joinedload so the service can
        read ``definition.name`` without an N+1 (M3 §4.4 / §12 note).

        Pure data access — no business rules here.
        """
        stmt = (
            select(StockInstance)
            .where(
                StockInstance.best_before_date.is_not(None),
                StockInstance.best_before_date <= cutoff_date,
                (StockInstance.quantity.is_(None)) | (StockInstance.quantity > 0),
            )
            .options(joinedload(StockInstance.definition))
            .order_by(
                StockInstance.best_before_date,
                StockInstance.id,
            )
        )
        return list(self._db.scalars(stmt).unique().all())

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
        best_before_date: date | None = None,
        purchase_price: Decimal | None = None,
        purchase_date: date | None = None,
        purchase_source: str | None = None,
        custom_fields: str | None = None,
        responsible_user_id: int | None = None,
    ) -> StockInstance:
        """Insert a new StockInstance and flush to get its PK.

        ``quantity`` is nullable: pass ``None`` for level/none-mode lots or when
        the service will set it via ledger recompute (exact-mode).
        ``stock_level`` is nullable: pass the validated level string for
        level-mode lots; None otherwise.
        ``best_before_date`` is nullable: pass the resolved date (explicit or
        auto-computed from the definition's default_best_before_days) or None.
        ``custom_fields`` is nullable: pass the JSON-serialized string or None.
        ``responsible_user_id`` is nullable: pass a validated user PK for
        per-lot responsible-party override (M6 Step 4), or None to inherit.
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
            best_before_date=best_before_date,
            purchase_price=purchase_price,
            purchase_date=purchase_date,
            purchase_source=purchase_source,
            custom_fields=custom_fields,
            responsible_user_id=responsible_user_id,
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
        set_best_before_date: bool = False,
        best_before_date: date | None = None,
        set_purchase_price: bool = False,
        purchase_price: Decimal | None = None,
        set_purchase_date: bool = False,
        purchase_date: date | None = None,
        set_purchase_source: bool = False,
        purchase_source: str | None = None,
        set_custom_fields: bool = False,
        custom_fields: str | None = None,
        set_responsible_user_id: bool = False,
        responsible_user_id: int | None = None,
    ) -> StockInstance:
        """Apply partial field updates to a StockInstance.

        Nullable fields use an explicit ``set_*`` flag to distinguish
        "don't change" from "explicitly set to NULL".

        Note: ``quantity`` is intentionally absent from this method (M2 §2).
        An ``exact`` lot's quantity is changed only through the movement
        ledger (``StockMovementService``, Step 4).
        ``best_before_date`` uses the ``set_best_before_date`` flag so that
        omitting the field on PATCH preserves the existing date while an
        explicit ``None`` in the payload clears it to NULL (M3 Step 2).
        ``custom_fields`` uses the ``set_custom_fields`` flag for the same
        reason (M5 Step 4): omit = unchanged; explicit None = clear.
        ``responsible_user_id`` uses the ``set_responsible_user_id`` flag for
        the same reason (M6 Step 4): omit = unchanged; explicit None = clear.
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
        if set_best_before_date:
            instance.best_before_date = best_before_date
        if set_purchase_price:
            instance.purchase_price = purchase_price
        if set_purchase_date:
            instance.purchase_date = purchase_date
        if set_purchase_source:
            instance.purchase_source = purchase_source
        if set_custom_fields:
            instance.custom_fields = custom_fields
        if set_responsible_user_id:
            instance.responsible_user_id = responsible_user_id
        self._db.flush()
        return instance

    def delete(self, instance: StockInstance) -> None:
        """Delete a StockInstance row (caller must ensure it is safe to delete)."""
        self._db.delete(instance)
        self._db.flush()
