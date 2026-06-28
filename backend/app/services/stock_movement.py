"""Service layer for stock movement operations (M2 Step 4).

This service owns all the "easy-to-get-wrong" ledger logic (M2 §5):

- ``intake``       — add stock to a lot (+qty); serialized lots capped at 1.
- ``discard``      — write off stock (−qty); rejected if it drives the lot < 0.
- ``adjust``       — stock-take to an absolute counted value (signed delta).
- ``move``         — whole-lot location change (delta = 0).
- ``consume_fifo`` — FEFO consumption across a definition's lots,
                     nearest-expiry-first by (best_before_date NULLS LAST,
                     received_at, id).
- ``reverse``      — append a compensating correction movement; undo a past
                     movement without mutating the ledger.

Design invariants enforced here (M2 §4.2 — the red lines):

1. ``quantity = SUM(quantity_delta)`` after every operation — never ``+= delta``
   (``recompute_quantity`` from ``StockInstanceService`` is called in the SAME
   transaction as the movement append).
2. Every movement records the acting ``user_id`` from the ``RequestContext``.
3. All operations reject ``level``/``none`` definitions
   (``stock.movement_not_applicable``, 409).
4. Non-positive quantity inputs raise ``stock.negative_quantity`` (422) with
   the documented error code, not a bare Pydantic error.
5. Operations never drive a lot below 0; ``consume_fifo`` rejects insufficient
   stock with NOTHING written (transaction integrity).

All DB access goes through repository objects; no raw queries here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.errors import AppError, ErrorCode
from app.models.item_definition import ItemDefinition
from app.models.notification import Notification
from app.models.stock_instance import StockInstance
from app.models.stock_movement import StockMovement
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.location import LocationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.repositories.stock_movement import StockMovementRepository
from app.services.stock_instance import StockInstanceService

_ZERO = Decimal("0")
_ONE = Decimal("1")


class StockMovementService:
    """Business-logic facade for stock movement operations."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self._db = db
        self._ctx = ctx
        self._movement_repo = StockMovementRepository(db)
        self._instance_repo = StockInstanceRepository(db)
        self._def_repo = ItemDefinitionRepository(db)
        self._loc_repo = LocationRepository(db)
        self._instance_svc = StockInstanceService(db)
        # Accumulated new Notification rows from event hooks (evaluate_low_stock).
        # Route handlers read this AFTER db.commit() to dispatch instant channels
        # (Step 8 §4.6: event path → dispatch(pending, include_email_digest=False)).
        self.pending_notifications: list[Notification] = []

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _acting_user_id(self) -> int | None:
        """Return the acting user's id from the request context (None = system)."""
        return self._ctx.user.id if self._ctx.user is not None else None

    def _get_instance_or_404(self, instance_id: int) -> StockInstance:
        """Return a StockInstance or raise 404."""
        inst = self._instance_repo.get(instance_id)
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

    def _assert_exact_mode(self, defn: ItemDefinition, instance_id: int) -> None:
        """Raise 409 if the definition is not in 'exact' tracking mode.

        All ledger operations are only valid for exact-mode definitions (M2 §3.4).
        """
        if defn.stock_tracking_mode != "exact":
            raise AppError(
                ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE,
                status_code=409,
                params={"id": instance_id, "mode": defn.stock_tracking_mode},
                message=(
                    f"Stock movement operations are not applicable to instance {instance_id} "
                    f"because its definition uses '{defn.stock_tracking_mode}' tracking mode "
                    "(only 'exact' mode supports ledger movements)."
                ),
            )

    def _assert_positive_quantity(self, quantity: Decimal, instance_id: int) -> None:
        """Raise 422 if quantity is not strictly positive (> 0).

        Used for intake, discard, and consume operations where a non-positive
        input is always wrong (M2 §4.7 stock.negative_quantity).
        """
        if quantity <= _ZERO:
            raise AppError(
                ErrorCode.STOCK_NEGATIVE_QUANTITY,
                status_code=422,
                params={"id": instance_id},
                message=(f"Quantity must be positive (> 0) for this operation; got {quantity}."),
            )

    def _assert_non_negative_counted(self, counted: Decimal, instance_id: int) -> None:
        """Raise 422 if a counted (absolute) quantity is negative.

        Used for adjust: a counted_quantity < 0 is physically impossible (M2 §4.7).
        """
        if counted < _ZERO:
            raise AppError(
                ErrorCode.STOCK_NEGATIVE_QUANTITY,
                status_code=422,
                params={"id": instance_id},
                message=(f"Counted quantity must be >= 0 for adjust; got {counted}."),
            )

    def _assert_serial_qty_1(self, inst: StockInstance, new_qty: Decimal) -> None:
        """Raise 422 if a serialized lot's recomputed quantity != 1.

        Serialized lots must always have exactly 1 unit (M2 §4.2 / M1 §2).
        """
        if inst.serial is not None and new_qty != _ONE:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE,
                status_code=422,
                message=(
                    f"Serialized lot (serial={inst.serial!r}) quantity must be exactly 1 "
                    f"after this operation, but recomputed quantity is {new_qty}."
                ),
            )

    def _recompute(self, inst: StockInstance) -> Decimal:
        """Recompute and persist the ledger-derived quantity for an exact-mode lot.

        Delegates to StockInstanceService.recompute_quantity (M2 §4.2 red line:
        quantity = SUM(deltas), never += delta).

        Returns the recomputed Decimal.
        """
        return self._instance_svc.recompute_quantity(inst)

    # ---------------------------------------------------------------------- #
    # Operations                                                               #
    # ---------------------------------------------------------------------- #

    def list_movements_for_instance(self, instance_id: int) -> list[StockMovement]:
        """Return ledger history for a lot (newest-first).

        Thin wrapper over the repository so routes don't access private attrs.
        """
        return self._movement_repo.list_for_instance(instance_id)

    def intake(
        self,
        instance: StockInstance,
        quantity: Decimal,
        *,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> StockInstance:
        """Add stock to an exact-mode lot.

        Appends an ``intake`` movement (+quantity), recomputes the cache, and
        re-checks the serial⇒qty=1 constraint for serialized lots.

        Parameters
        ----------
        instance
            The lot to add stock to.
        quantity
            Positive Decimal quantity to add.
        occurred_at
            Physical receipt time; defaults to DB now().
        note
            Optional free-text annotation.

        Raises
        ------
        AppError 409
            If the definition is not in 'exact' mode.
        AppError 422
            If quantity <= 0, or the recomputed quantity would exceed 1 for a
            serialized lot (stock.negative_quantity /
            stock_instance.serial_requires_qty_one).
        """
        defn = self._get_definition_or_404(instance.definition_id)
        self._assert_exact_mode(defn, instance.id)
        self._assert_positive_quantity(quantity, instance.id)

        self._movement_repo.append(
            instance_id=instance.id,
            type="intake",
            quantity_delta=quantity,
            to_location_id=instance.location_id,
            occurred_at=occurred_at,
            note=note,
            user_id=self._acting_user_id(),
        )
        new_qty = self._recompute(instance)
        # Check serial⇒qty=1 BEFORE flushing so the service raises a clean 422
        # instead of letting the DB CHECK fire a raw IntegrityError.
        self._assert_serial_qty_1(instance, new_qty)
        self._db.flush()
        return instance

    def discard(
        self,
        instance: StockInstance,
        quantity: Decimal,
        *,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> StockInstance:
        """Write off stock from an exact-mode lot.

        Appends a ``discard`` movement (−quantity) and recomputes the cache.
        Rejected if the operation would drive the lot below 0.

        Parameters
        ----------
        instance
            The lot to remove stock from.
        quantity
            Positive Decimal quantity to discard.
        occurred_at
            Physical event time; defaults to DB now().
        note
            Optional free-text annotation.

        Raises
        ------
        AppError 409
            If the definition is not in 'exact' mode.
        AppError 422
            If quantity <= 0, or the remaining stock would be < 0.
        """
        defn = self._get_definition_or_404(instance.definition_id)
        self._assert_exact_mode(defn, instance.id)
        self._assert_positive_quantity(quantity, instance.id)

        current = instance.quantity if instance.quantity is not None else _ZERO
        if current - quantity < _ZERO:
            raise AppError(
                ErrorCode.STOCK_NEGATIVE_QUANTITY,
                status_code=422,
                params={"id": instance.id},
                message=(
                    f"Discard of {quantity} would drive lot {instance.id} below 0 "
                    f"(current quantity: {current})."
                ),
            )

        self._movement_repo.append(
            instance_id=instance.id,
            type="discard",
            quantity_delta=-quantity,
            occurred_at=occurred_at,
            note=note,
            user_id=self._acting_user_id(),
        )
        self._recompute(instance)
        self._db.flush()

        # Event hook: evaluate low-stock for this definition after the discard.
        # Best-effort + savepoint-isolated (failure must not roll back movement).
        # New notifications are accumulated in self.pending_notifications so that
        # the route handler can dispatch instant channels post-commit (Step 8).
        try:
            from app.services.reminder_engine import ReminderEngine

            with self._db.begin_nested():
                new_notifs = ReminderEngine(self._db).evaluate_low_stock(instance.definition_id)
                self.pending_notifications.extend(new_notifs)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "evaluate_low_stock after discard failed for definition %d "
                "(best-effort -- movement committed normally)",
                instance.definition_id,
                exc_info=True,
            )

        # Event hook: reconcile auto shopping-list rows after discard.
        # A separate best-effort + savepoint-isolated call so a reconcile failure
        # NEVER rolls back the movement.  Local import avoids any import cycle
        # (mirrors the ReminderEngine local import pattern above).
        try:
            from app.services.shopping_list import ShoppingListService

            with self._db.begin_nested():
                ShoppingListService(self._db).reconcile_auto_items()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "reconcile_auto_items after discard failed for definition %d "
                "(best-effort -- movement committed normally)",
                instance.definition_id,
                exc_info=True,
            )

        return instance

    def adjust(
        self,
        instance: StockInstance,
        counted_quantity: Decimal,
        *,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> StockInstance:
        """Set an exact-mode lot's quantity to an absolute counted value (stock-take).

        Computes ``delta = counted_quantity − current`` and appends an ``adjust``
        movement with that signed delta.

        Parameters
        ----------
        instance
            The lot to adjust.
        counted_quantity
            The absolute counted value (must be >= 0).
        occurred_at
            Physical stock-take time; defaults to DB now().
        note
            Optional free-text annotation.

        Raises
        ------
        AppError 409
            If the definition is not in 'exact' mode.
        AppError 422
            If counted_quantity < 0 (physically impossible).
        """
        defn = self._get_definition_or_404(instance.definition_id)
        self._assert_exact_mode(defn, instance.id)
        self._assert_non_negative_counted(counted_quantity, instance.id)

        current = instance.quantity if instance.quantity is not None else _ZERO
        delta = counted_quantity - current

        self._movement_repo.append(
            instance_id=instance.id,
            type="adjust",
            quantity_delta=delta,
            occurred_at=occurred_at,
            note=note,
            user_id=self._acting_user_id(),
        )
        self._recompute(instance)
        self._db.flush()

        # Event hook: evaluate low-stock for this definition after the adjust.
        # An upward adjust may close an open episode; a downward adjust may open
        # one.  Best-effort + savepoint-isolated (failure must not roll back
        # movement).
        # New notifications are accumulated in self.pending_notifications so that
        # the route handler can dispatch instant channels post-commit (Step 8).
        try:
            from app.services.reminder_engine import ReminderEngine

            with self._db.begin_nested():
                new_notifs = ReminderEngine(self._db).evaluate_low_stock(instance.definition_id)
                self.pending_notifications.extend(new_notifs)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "evaluate_low_stock after adjust failed for definition %d "
                "(best-effort -- movement committed normally)",
                instance.definition_id,
                exc_info=True,
            )

        # Event hook: reconcile auto shopping-list rows after adjust.
        # Separate best-effort + savepoint-isolated call so a reconcile failure
        # NEVER rolls back the movement.
        try:
            from app.services.shopping_list import ShoppingListService

            with self._db.begin_nested():
                ShoppingListService(self._db).reconcile_auto_items()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "reconcile_auto_items after adjust failed for definition %d "
                "(best-effort -- movement committed normally)",
                instance.definition_id,
                exc_info=True,
            )

        return instance

    def move(
        self,
        instance: StockInstance,
        to_location_id: int,
        *,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> StockInstance:
        """Relocate an exact-mode lot to a new location (whole-lot only).

        Appends a ``move`` movement (delta = 0) recording from/to locations, then
        updates ``inst.location_id``. The quantity is unchanged.

        Parameters
        ----------
        instance
            The lot to move.
        to_location_id
            The destination location PK (must exist).
        occurred_at
            Physical move time; defaults to DB now().
        note
            Optional free-text annotation.

        Raises
        ------
        AppError 409
            If the definition is not in 'exact' mode.
        AppError 404
            If ``to_location_id`` does not exist.
        """
        defn = self._get_definition_or_404(instance.definition_id)
        self._assert_exact_mode(defn, instance.id)

        if self._loc_repo.get(to_location_id) is None:
            raise AppError(
                ErrorCode.LOCATION_NOT_FOUND,
                status_code=404,
                params={"id": to_location_id},
                message=f"Location {to_location_id} not found.",
            )

        from_location_id = instance.location_id

        self._movement_repo.append(
            instance_id=instance.id,
            type="move",
            quantity_delta=_ZERO,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            occurred_at=occurred_at,
            note=note,
            user_id=self._acting_user_id(),
        )
        instance.location_id = to_location_id
        # No recompute needed: move has delta = 0, quantity unchanged.
        # But we flush to persist the location change.
        self._db.flush()
        return instance

    def consume_fifo(
        self,
        definition: ItemDefinition,
        quantity: Decimal,
        *,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> list[StockInstance]:
        """Consume stock from an exact-mode definition's lots in FEFO order.

        Walks the definition's active lots nearest-expiry-first by
        ``(best_before_date ASC NULLS LAST, received_at ASC, id ASC)``,
        decrementing each via a ``consume`` movement until the requested
        quantity is satisfied.  Lots with a best-before date are consumed
        before never-expiring lots (NULL best_before_date); lots sharing the
        same best-before date fall back to oldest-received-first (M2 tie-break).

        If the total available stock is insufficient, raises ``stock.insufficient``
        (422) and writes NOTHING (transaction integrity).

        Parameters
        ----------
        definition
            The item definition to consume from (must be 'exact' mode).
        quantity
            Positive Decimal quantity to consume.
        occurred_at
            Physical consumption time; defaults to DB now().
        note
            Optional free-text annotation.

        Returns
        -------
        list[StockInstance]
            All lots touched (with recomputed quantities).

        Raises
        ------
        AppError 409
            If the definition is not in 'exact' mode.
        AppError 422
            If quantity <= 0, or total available stock < quantity.
        """
        # Mode guard — use a sentinel instance_id for the error params.
        if definition.stock_tracking_mode != "exact":
            raise AppError(
                ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE,
                status_code=409,
                params={"id": definition.id, "mode": definition.stock_tracking_mode},
                message=(
                    f"FIFO consume is not applicable to definition {definition.id} "
                    f"because it uses '{definition.stock_tracking_mode}' tracking mode "
                    "(only 'exact' mode supports ledger movements)."
                ),
            )

        if quantity <= _ZERO:
            raise AppError(
                ErrorCode.STOCK_NEGATIVE_QUANTITY,
                status_code=422,
                params={"id": definition.id},
                message=f"Consume quantity must be positive (> 0); got {quantity}.",
            )

        # Load the lots eligible for FEFO: quantity > 0, ordered by
        # (best_before_date ASC NULLS LAST, received_at ASC, id ASC).
        lots = self._instance_repo.list_active_lots_for_definition(definition.id)

        # Check total availability BEFORE writing anything (M2 §4.3).
        total_available = sum((lot.quantity for lot in lots if lot.quantity is not None), _ZERO)
        if total_available < quantity:
            raise AppError(
                ErrorCode.STOCK_INSUFFICIENT,
                status_code=422,
                params={"requested": str(quantity), "available": str(total_available)},
                message=(
                    f"Insufficient stock for definition {definition.id}: "
                    f"requested {quantity}, available {total_available}."
                ),
            )

        # Walk nearest-expiry-first, append one consume per lot touched.
        remaining = quantity
        touched: list[StockInstance] = []
        for lot in lots:
            if remaining <= _ZERO:
                break
            lot_qty = lot.quantity if lot.quantity is not None else _ZERO
            if lot_qty <= _ZERO:
                continue
            take = min(lot_qty, remaining)
            self._movement_repo.append(
                instance_id=lot.id,
                type="consume",
                quantity_delta=-take,
                occurred_at=occurred_at,
                note=note,
                user_id=self._acting_user_id(),
            )
            self._recompute(lot)
            self._db.flush()
            touched.append(lot)
            remaining -= take

        # Event hook: evaluate low-stock for this definition right after the
        # consume, within the same transaction.  Best-effort + savepoint-isolated:
        # a failure in the reminder logic must never roll back the movement.
        # Local import to avoid circular imports (reminder_engine imports
        # services indirectly; keeping the import local breaks the cycle).
        # New notifications are accumulated in self.pending_notifications so that
        # the route handler can dispatch instant channels post-commit (Step 8).
        try:
            from app.services.reminder_engine import ReminderEngine

            with self._db.begin_nested():
                new_notifs = ReminderEngine(self._db).evaluate_low_stock(definition.id)
                self.pending_notifications.extend(new_notifs)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "evaluate_low_stock after consume_fifo failed for definition %d "
                "(best-effort -- movement committed normally)",
                definition.id,
                exc_info=True,
            )

        # Event hook: reconcile auto shopping-list rows after consume.
        # Separate best-effort + savepoint-isolated call so a reconcile failure
        # NEVER rolls back the movement.
        try:
            from app.services.shopping_list import ShoppingListService

            with self._db.begin_nested():
                ShoppingListService(self._db).reconcile_auto_items()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "reconcile_auto_items after consume_fifo failed for definition %d "
                "(best-effort -- movement committed normally)",
                definition.id,
                exc_info=True,
            )

        return touched

    def reverse(
        self,
        movement_id: int,
        *,
        note: str | None = None,
    ) -> StockInstance:
        """Append a compensating correction movement to undo a past operation.

        Implements the reversal rules from M2 §4.4:
        - The target must exist (404 if not).
        - The target must not itself be a reversal (409).
        - The target must not already have been reversed (409).
        - The reversal must not drive the lot below 0 (409).
        - For ``move`` movements, the location is restored.

        Parameters
        ----------
        movement_id
            PK of the movement to reverse.
        note
            Optional free-text annotation for the correction entry.

        Returns
        -------
        StockInstance
            The affected lot with its recomputed quantity.

        Raises
        ------
        AppError 404
            If the movement does not exist.
        AppError 409
            Various reversal guards (see above).
        """
        m = self._movement_repo.get(movement_id)
        if m is None:
            raise AppError(
                ErrorCode.STOCK_MOVEMENT_NOT_FOUND,
                status_code=404,
                params={"id": movement_id},
                message=f"Stock movement {movement_id} not found.",
            )

        # Cannot reverse a reversal.
        if m.reverses_movement_id is not None:
            raise AppError(
                ErrorCode.STOCK_CANNOT_REVERSE_REVERSAL,
                status_code=409,
                params={"id": movement_id},
                message=(
                    f"Movement {movement_id} is itself a reversal (reverses "
                    f"movement {m.reverses_movement_id}) and cannot be reversed again."
                ),
            )

        # Cannot reverse a movement that was already reversed.
        existing_reversal = self._movement_repo.find_reversal_of(movement_id)
        if existing_reversal is not None:
            raise AppError(
                ErrorCode.STOCK_MOVEMENT_ALREADY_REVERSED,
                status_code=409,
                params={"id": movement_id},
                message=(
                    f"Movement {movement_id} has already been reversed by "
                    f"movement {existing_reversal.id}."
                ),
            )

        # Load the affected lot.
        inst = self._instance_repo.get(m.instance_id)
        if inst is None:
            # Defensive: the CASCADE FK means this should never happen in normal operation.
            raise AppError(
                ErrorCode.STOCK_INSTANCE_NOT_FOUND,
                status_code=404,
                params={"id": m.instance_id},
                message=f"Stock instance {m.instance_id} not found (movement orphaned).",
            )

        current = inst.quantity if inst.quantity is not None else _ZERO
        prospective = current - m.quantity_delta
        if prospective < _ZERO:
            raise AppError(
                ErrorCode.STOCK_REVERSE_WOULD_GO_NEGATIVE,
                status_code=409,
                params={"id": movement_id},
                message=(
                    f"Reversing movement {movement_id} (delta={m.quantity_delta}) "
                    f"would drive lot {inst.id} to {prospective} (< 0). "
                    "Reversal rejected."
                ),
            )

        # Build the correction movement.
        reversal_from: int | None = None
        reversal_to: int | None = None
        if m.type == "move":
            # Restore location: swap from/to in the correction row.
            reversal_from = m.to_location_id
            reversal_to = m.from_location_id
            inst.location_id = m.from_location_id

        self._movement_repo.append(
            instance_id=inst.id,
            type="correction",
            quantity_delta=-m.quantity_delta,
            from_location_id=reversal_from,
            to_location_id=reversal_to,
            reverses_movement_id=movement_id,
            note=note,
            user_id=self._acting_user_id(),
        )
        self._recompute(inst)
        self._db.flush()
        return inst
