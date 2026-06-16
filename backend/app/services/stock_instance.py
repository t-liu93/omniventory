"""Service layer for StockInstance CRUD.

Business rules handled here (M1.md §4.1 / §9 Step 4):

- **serial ⇒ quantity = 1**: if a serial is provided (on create or update),
  the quantity must be exactly 1.  Rejected with HTTP 422 before the DB is
  touched (the DB CHECK is a second safety net, not the first line of defence).

- **Default quantity = 1**: when quantity is omitted on create, it defaults
  to Decimal("1").

- **Default-location resolution**: when a new instance omits ``location_id``,
  the service looks up the definition's ``default_location_id`` and uses it.
  If the definition also has no default location, ``location_id`` stays NULL.

- **FK existence checks**: ``definition_id`` and (when provided) ``location_id``
  are validated to exist before any write.

All DB access goes through ``StockInstanceRepository``; no raw queries in
this layer.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stock_instance import StockInstance
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.location import LocationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.schemas.stock_instance import InstanceCreate, InstanceUpdate

_ONE = Decimal("1")


class StockInstanceService:
    """Business-logic facade for StockInstance operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = StockInstanceRepository(db)
        self._def_repo = ItemDefinitionRepository(db)
        self._loc_repo = LocationRepository(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, instance_id: int) -> StockInstance:
        """Return a StockInstance or raise HTTP 404."""
        inst = self._repo.get(instance_id)
        if inst is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stock instance {instance_id} not found.",
            )
        return inst

    def _assert_serial_qty_1(self, serial: str | None, quantity: Decimal) -> None:
        """Raise HTTP 422 if serial is set but quantity != 1.

        Service-layer enforcement of the serial⇒qty=1 constraint (M1.md §2 /
        §3.5).  The DB CHECK is a second safety net for direct writes that
        bypass the service layer.
        """
        if serial is not None and quantity != _ONE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "When a serial number is provided, quantity must be exactly 1 "
                    f"(serial={serial!r}, quantity={quantity})."
                ),
            )

    def _assert_definition_exists(self, definition_id: int) -> None:
        """Raise HTTP 404 if the definition does not exist."""
        if self._def_repo.get(definition_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item definition {definition_id} not found.",
            )

    def _assert_location_exists(self, location_id: int) -> None:
        """Raise HTTP 404 if the location does not exist."""
        if self._loc_repo.get(location_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location {location_id} not found.",
            )

    def _assert_serial_unique(
        self,
        definition_id: int,
        serial: str,
        *,
        exclude_instance_id: int | None = None,
    ) -> None:
        """Raise HTTP 409 if another instance already holds this serial for the definition.

        Service-layer enforcement of the partial-unique index
        ``(definition_id, serial) WHERE serial IS NOT NULL`` (M1.md §3.5 / §4.2).
        A pre-check here returns a meaningful 409 to the client instead of
        letting a DB IntegrityError bubble up as 500.

        On update, ``exclude_instance_id`` is the instance being updated so
        that a no-op serial update (same serial, same instance) does not
        incorrectly conflict with itself.
        """
        stmt = select(StockInstance).where(
            StockInstance.definition_id == definition_id,
            StockInstance.serial == serial,
        )
        if exclude_instance_id is not None:
            stmt = stmt.where(StockInstance.id != exclude_instance_id)
        conflict = self._db.scalars(stmt).first()
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Serial number {serial!r} is already registered for definition "
                    f"{definition_id} (instance id={conflict.id}). "
                    "Serial numbers must be unique per item definition."
                ),
            )

    def _resolve_location_id(
        self,
        definition_id: int,
        location_id: int | None,
        location_id_provided: bool,
    ) -> int | None:
        """Resolve the effective location_id for a new instance.

        If the caller explicitly provided a location_id, validate and use it.
        If omitted, fall back to the definition's default_location_id.
        Stays NULL when neither the caller nor the definition specifies one.
        """
        if location_id_provided:
            if location_id is not None:
                self._assert_location_exists(location_id)
            return location_id

        # Not provided — try definition's default.
        defn = self._def_repo.get(definition_id)
        if defn is None:
            # Already validated above; this branch is unreachable in practice.
            return None
        return defn.default_location_id

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: InstanceCreate) -> StockInstance:
        """Create a new stock instance.

        - Validates ``definition_id``.
        - Enforces ``serial ⇒ quantity = 1`` (422).
        - Resolves ``location_id`` from the definition's ``default_location_id``
          when omitted.
        """
        self._assert_definition_exists(data.definition_id)

        qty = data.quantity if data.quantity is not None else _ONE
        self._assert_serial_qty_1(data.serial, qty)

        # Uniqueness check for (definition_id, serial) — must give 409, not 500.
        if data.serial is not None:
            self._assert_serial_unique(data.definition_id, data.serial)

        location_id_provided = "location_id" in data.model_fields_set
        resolved_location_id = self._resolve_location_id(
            data.definition_id,
            data.location_id,
            location_id_provided,
        )

        return self._repo.create(
            definition_id=data.definition_id,
            location_id=resolved_location_id,
            quantity=qty,
            serial=data.serial,
            model_number=data.model_number,
            manufacturer=data.manufacturer,
            warranty_expires=data.warranty_expires,
            warranty_details=data.warranty_details,
            purchase_price=data.purchase_price,
            purchase_date=data.purchase_date,
            purchase_source=data.purchase_source,
        )

    def get(self, instance_id: int) -> StockInstance:
        """Return a stock instance by PK, or raise 404."""
        return self._get_or_404(instance_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        definition_id: int | None = None,
        location_id: int | None = None,
    ) -> list[StockInstance]:
        """Return a filtered flat list of stock instances."""
        return self._repo.list_all(q=q, definition_id=definition_id, location_id=location_id)

    def update(self, instance_id: int, data: InstanceUpdate) -> StockInstance:
        """Apply a partial update to a stock instance.

        Enforces ``serial ⇒ quantity = 1`` across the merged state (combining
        current values with the update payload).
        """
        inst = self._get_or_404(instance_id)

        # Determine effective serial and quantity after this update.
        serial_changed = "serial" in data.model_fields_set

        effective_serial = data.serial if serial_changed else inst.serial
        effective_qty: Decimal = data.quantity if data.quantity is not None else inst.quantity

        self._assert_serial_qty_1(effective_serial, effective_qty)

        # Uniqueness check for (definition_id, serial) on update — exclude self.
        if serial_changed and effective_serial is not None:
            self._assert_serial_unique(
                inst.definition_id, effective_serial, exclude_instance_id=instance_id
            )

        location_id_changed = "location_id" in data.model_fields_set
        if location_id_changed and data.location_id is not None:
            self._assert_location_exists(data.location_id)

        return self._repo.update(
            inst,
            set_location_id=location_id_changed,
            location_id=data.location_id,
            quantity=data.quantity,
            set_serial=serial_changed,
            serial=data.serial,
            set_model_number="model_number" in data.model_fields_set,
            model_number=data.model_number,
            set_manufacturer="manufacturer" in data.model_fields_set,
            manufacturer=data.manufacturer,
            set_warranty_expires="warranty_expires" in data.model_fields_set,
            warranty_expires=data.warranty_expires,
            set_warranty_details="warranty_details" in data.model_fields_set,
            warranty_details=data.warranty_details,
            set_purchase_price="purchase_price" in data.model_fields_set,
            purchase_price=data.purchase_price,
            set_purchase_date="purchase_date" in data.model_fields_set,
            purchase_date=data.purchase_date,
            set_purchase_source="purchase_source" in data.model_fields_set,
            purchase_source=data.purchase_source,
        )

    def delete(self, instance_id: int) -> None:
        """Delete a stock instance."""
        inst = self._get_or_404(instance_id)
        self._repo.delete(inst)
