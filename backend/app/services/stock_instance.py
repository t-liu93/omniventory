"""Service layer for StockInstance CRUD.

Business rules handled here (M2 Step 3 — ledger-wiring, tracking modes):

- **Mode-aware create (M2 §3.4 / §4.1)**:
    - ``exact``: create the row, record an initial ``intake`` movement
      (delta = requested quantity, defaulting to 1), recompute
      ``quantity = SUM(deltas)`` from the ledger, then re-check serial⇒qty=1.
    - ``level``: require ``stock_level`` (one of STOCK_LEVELS); quantity must
      be absent (rejected if provided); no movement.
    - ``none``: neither quantity nor stock_level; both are rejected if supplied;
      no movement.

- **recompute_quantity(instance)**: ``SUM(quantity_delta)`` for the instance's
  movements — the only way an ``exact`` lot's quantity is ever set (M2 §4.2,
  the red line: never ``quantity += delta``).

- **serial ⇒ quantity = 1**: for ``exact`` lots, enforced both at create
  (service 422, then DB CHECK backstop) and after every recompute (if the
  post-recompute quantity != 1 for a serialized lot, the service rejects it).
  For non-exact lots quantity is NULL, which the rewritten DB CHECK also allows.

- **Mode-aware update**: quantity is gone from InstanceUpdate (M2 §2). For
  ``level`` lots, ``stock_level`` may be updated. Field/mode validation runs
  on update too.

- **Cross-field validation** (§3.4): a lot's fields must match its definition's
  mode — ``exact`` ⇒ no stock_level; ``level`` ⇒ stock_level set, no quantity;
  ``none`` ⇒ neither. Bad stock_level value → ``validation.unsupported_stock_level``.
  Mismatch between field and mode → ``instance.field_mode_mismatch``.

- **Default-location resolution**: when a new instance omits ``location_id``,
  the service looks up the definition's ``default_location_id`` and uses it.
  If the definition also has no default location, ``location_id`` stays NULL.

- **FK existence checks**: ``definition_id`` and (when provided) ``location_id``
  are validated to exist before any write.

All DB access goes through ``StockInstanceRepository`` and
``StockMovementRepository``; no raw queries in this layer.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.stock import STOCK_LEVELS
from app.models.item_definition import ItemDefinition
from app.models.stock_instance import StockInstance
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.location import LocationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.repositories.stock_movement import StockMovementRepository
from app.schemas.stock_instance import InstanceCreate, InstanceUpdate

_ONE = Decimal("1")
_ZERO = Decimal("0")


class StockInstanceService:
    """Business-logic facade for StockInstance operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = StockInstanceRepository(db)
        self._def_repo = ItemDefinitionRepository(db)
        self._loc_repo = LocationRepository(db)
        self._movement_repo = StockMovementRepository(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, instance_id: int) -> StockInstance:
        """Return a StockInstance or raise 404."""
        inst = self._repo.get(instance_id)
        if inst is None:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_NOT_FOUND,
                status_code=404,
                params={"id": instance_id},
                message=f"Stock instance {instance_id} not found.",
            )
        return inst

    def _get_definition_or_404(self, definition_id: int) -> ItemDefinition:
        """Return an ItemDefinition or raise 404."""
        defn = self._def_repo.get(definition_id)
        if defn is None:
            raise AppError(
                ErrorCode.ITEM_DEFINITION_NOT_FOUND,
                status_code=404,
                params={"id": definition_id},
                message=f"Item definition {definition_id} not found.",
            )
        return defn

    def _assert_location_exists(self, location_id: int) -> None:
        """Raise 404 if the location does not exist."""
        if self._loc_repo.get(location_id) is None:
            raise AppError(
                ErrorCode.LOCATION_NOT_FOUND,
                status_code=404,
                params={"id": location_id},
                message=f"Location {location_id} not found.",
            )

    def _assert_serial_unique(
        self,
        definition_id: int,
        serial: str,
        *,
        exclude_instance_id: int | None = None,
    ) -> None:
        """Raise HTTP 409 if another instance already holds this serial for the definition."""
        stmt = select(StockInstance).where(
            StockInstance.definition_id == definition_id,
            StockInstance.serial == serial,
        )
        if exclude_instance_id is not None:
            stmt = stmt.where(StockInstance.id != exclude_instance_id)
        conflict = self._db.scalars(stmt).first()
        if conflict is not None:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_SERIAL_DUPLICATE,
                status_code=409,
                params={"serial": serial},
                message=(
                    f"Serial number {serial!r} is already registered for definition "
                    f"{definition_id} (instance id={conflict.id}). "
                    "Serial numbers must be unique per item definition."
                ),
            )

    def _assert_serial_qty_1(self, serial: str | None, quantity: Decimal | None) -> None:
        """Raise 422 if serial is set but quantity (when non-NULL) != 1.

        NULL quantity is allowed (for non-exact lots, the rewritten DB CHECK
        also permits it).  Only non-NULL quantity != 1 with a serial is invalid.
        """
        if serial is not None and quantity is not None and quantity != _ONE:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE,
                status_code=422,
                message=(
                    "When a serial number is provided, quantity must be exactly 1 "
                    f"(serial={serial!r}, quantity={quantity})."
                ),
            )

    def _assert_valid_stock_level(self, value: str) -> None:
        """Raise 422 if stock_level value is not in STOCK_LEVELS."""
        if value not in STOCK_LEVELS:
            raise AppError(
                ErrorCode.UNSUPPORTED_STOCK_LEVEL,
                status_code=422,
                params={"value": value, "supported": list(STOCK_LEVELS)},
                message=(
                    f"Unsupported stock level {value!r}. Supported values: {list(STOCK_LEVELS)}."
                ),
            )

    def _validate_mode_fields_create(
        self,
        mode: str,
        quantity: Decimal | None,
        stock_level: str | None,
    ) -> None:
        """Validate that field values match the definition's tracking mode.

        Raises AppError 422 with ``instance.field_mode_mismatch`` on mismatch,
        or ``validation.unsupported_stock_level`` for an invalid level value.
        """
        if mode == "exact":
            if stock_level is not None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "stock_level"},
                    message=(
                        f"stock_level must not be provided for 'exact'-mode lots (mode={mode!r})."
                    ),
                )
        elif mode == "level":
            if quantity is not None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "quantity"},
                    message=(
                        "quantity must not be provided for 'level'-mode lots. "
                        "Set stock_level instead."
                    ),
                )
            if stock_level is None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "stock_level"},
                    message="stock_level is required for 'level'-mode lots.",
                )
            self._assert_valid_stock_level(stock_level)
        elif mode == "none":
            if quantity is not None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "quantity"},
                    message="quantity must not be provided for 'none'-mode lots.",
                )
            if stock_level is not None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "stock_level"},
                    message="stock_level must not be provided for 'none'-mode lots.",
                )

    def _validate_mode_fields_update(
        self,
        mode: str,
        stock_level_provided: bool,
        stock_level: str | None,
    ) -> None:
        """Validate update fields against the lot's definition mode."""
        if mode == "exact":
            if stock_level_provided and stock_level is not None:
                raise AppError(
                    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                    status_code=422,
                    params={"mode": mode, "field": "stock_level"},
                    message="stock_level must not be provided for 'exact'-mode lots.",
                )
        elif mode == "level":
            if stock_level_provided and stock_level is not None:
                self._assert_valid_stock_level(stock_level)
        elif mode == "none" and stock_level_provided and stock_level is not None:
            raise AppError(
                ErrorCode.INSTANCE_FIELD_MODE_MISMATCH,
                status_code=422,
                params={"mode": mode, "field": "stock_level"},
                message="stock_level must not be provided for 'none'-mode lots.",
            )

    def _resolve_location_id(
        self,
        definition_id: int,
        location_id: int | None,
        location_id_provided: bool,
    ) -> int | None:
        """Resolve the effective location_id for a new instance."""
        if location_id_provided:
            if location_id is not None:
                self._assert_location_exists(location_id)
            return location_id

        # Not provided — try definition's default.
        defn = self._def_repo.get(definition_id)
        if defn is None:
            return None
        return defn.default_location_id

    # ---------------------------------------------------------------------- #
    # Ledger helpers                                                           #
    # ---------------------------------------------------------------------- #

    def recompute_quantity(self, instance: StockInstance) -> Decimal:
        """Recompute and persist the ledger-derived quantity for an exact-mode lot.

        Rule (M2 §4.2 — the red line):
            quantity = SUM(quantity_delta)  [never += delta]

        Returns the recomputed Decimal and updates ``instance.quantity`` in place.
        The caller must flush/commit the session after calling this.
        """
        new_qty = self._movement_repo.sum_delta_for_instance(instance.id)
        instance.quantity = new_qty
        return new_qty

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: InstanceCreate) -> StockInstance:
        """Create a new stock instance with mode-aware field validation.

        For ``exact`` mode:
            1. Validate mode fields (no stock_level).
            2. Create the row with quantity=NULL initially.
            3. Record the initial intake movement (delta = requested qty or 1).
            4. Recompute quantity = SUM(deltas).
            5. Re-check serial⇒qty=1 on the recomputed quantity.

        For ``level`` mode:
            Validate stock_level present and valid; create row with quantity=NULL.

        For ``none`` mode:
            Validate neither field present; create row with quantity=NULL.
        """
        defn = self._get_definition_or_404(data.definition_id)
        mode = defn.stock_tracking_mode

        # Validate mode-specific fields.
        self._validate_mode_fields_create(mode, data.quantity, data.stock_level)

        # Serial uniqueness check.
        if data.serial is not None:
            self._assert_serial_unique(data.definition_id, data.serial)

        location_id_provided = "location_id" in data.model_fields_set
        resolved_location_id = self._resolve_location_id(
            data.definition_id,
            data.location_id,
            location_id_provided,
        )

        if mode == "exact":
            # Initial intake quantity — default to 1 when not supplied.
            intake_qty = data.quantity if data.quantity is not None else _ONE

            # Pre-create serial check with the intended intake qty.
            self._assert_serial_qty_1(data.serial, intake_qty)

            # Create the row with quantity=NULL (will be set by recompute).
            inst = self._repo.create(
                definition_id=data.definition_id,
                location_id=resolved_location_id,
                quantity=None,  # ledger will set this
                stock_level=None,
                serial=data.serial,
                model_number=data.model_number,
                manufacturer=data.manufacturer,
                warranty_expires=data.warranty_expires,
                warranty_details=data.warranty_details,
                purchase_price=data.purchase_price,
                purchase_date=data.purchase_date,
                purchase_source=data.purchase_source,
            )
            # Record the initial intake movement.
            self._movement_repo.append(
                instance_id=inst.id,
                type="intake",
                quantity_delta=intake_qty,
                to_location_id=resolved_location_id,
                user_id=None,  # no request-context user at this layer; Step 4 wires this
            )
            # Recompute quantity from ledger (the only legitimate way to set it).
            new_qty = self.recompute_quantity(inst)
            self._db.flush()
            # Re-check serial⇒qty=1 on the recomputed value.
            self._assert_serial_qty_1(inst.serial, new_qty)
            return inst

        elif mode == "level":
            inst = self._repo.create(
                definition_id=data.definition_id,
                location_id=resolved_location_id,
                quantity=None,
                stock_level=data.stock_level,
                serial=data.serial,
                model_number=data.model_number,
                manufacturer=data.manufacturer,
                warranty_expires=data.warranty_expires,
                warranty_details=data.warranty_details,
                purchase_price=data.purchase_price,
                purchase_date=data.purchase_date,
                purchase_source=data.purchase_source,
            )
            self._db.flush()
            return inst

        else:  # mode == "none"
            inst = self._repo.create(
                definition_id=data.definition_id,
                location_id=resolved_location_id,
                quantity=None,
                stock_level=None,
                serial=data.serial,
                model_number=data.model_number,
                manufacturer=data.manufacturer,
                warranty_expires=data.warranty_expires,
                warranty_details=data.warranty_details,
                purchase_price=data.purchase_price,
                purchase_date=data.purchase_date,
                purchase_source=data.purchase_source,
            )
            self._db.flush()
            return inst

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

        Quantity is NOT updatable (removed from InstanceUpdate in M2 Step 3).
        stock_level can be updated for level-mode lots.
        All other fields (serial, location, durable fields) are updatable
        as before, subject to mode constraints.
        """
        inst = self._get_or_404(instance_id)
        defn = self._get_definition_or_404(inst.definition_id)
        mode = defn.stock_tracking_mode

        serial_changed = "serial" in data.model_fields_set
        effective_serial = data.serial if serial_changed else inst.serial

        # Validate stock_level update against mode.
        stock_level_provided = "stock_level" in data.model_fields_set
        self._validate_mode_fields_update(mode, stock_level_provided, data.stock_level)

        # For exact-mode lots, re-check serial⇒qty=1 on the current quantity
        # if serial is being changed.
        if mode == "exact" and serial_changed:
            self._assert_serial_qty_1(effective_serial, inst.quantity)

        # Serial uniqueness check on update — exclude self.
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
            set_stock_level=stock_level_provided,
            stock_level=data.stock_level,
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
        """Delete a stock instance (cascade-deletes its movements)."""
        inst = self._get_or_404(instance_id)
        self._repo.delete(inst)
