"""ShoppingListService — CRUD + auto-reconcile for the shopping list.

Covers M7 §4.1 / §4.2 / §4.3 / §9 Step 1 (CRUD) + Step 2 (reconcile).

Step 1 responsibilities (CRUD)
---------------------------------------------------------------------------
``add_manual(definition_id?, name?, desired_quantity?, unit?, note?, created_by?)``
    Add a manual item.  Cross-field guard: at least one of definition_id / name.
    If definition_id is provided it must exist (item_definition.not_found → 404).
    Validates source='manual' against SHOPPING_LIST_SOURCES.

``edit(item_id, update_body)``
    PATCH an existing item.  Only fields present in ``update.model_fields_set``
    are applied.  Raises shopping_list.not_found (404) when the item is missing.

``check_off(item_id)``
    Stamp ``purchased_at = now(UTC)``.  Step 1 — **no intake** (no body, no
    delegation to StockInstanceService; that is Step 3).
    Raises shopping_list.not_found (404) when the item is missing.

``uncheck(item_id)``
    Clear ``purchased_at``.  Safe for auto rows because the per-def auto-row
    uniqueness is state-independent (§3.1).
    Raises shopping_list.not_found (404) when the item is missing.

``remove(item_id)``
    Hard-delete an item.
    Raises shopping_list.not_found (404) when the item is missing.

``clear_purchased()``
    Delete all rows where purchased_at IS NOT NULL.  Returns the count.

Step 2 responsibilities (auto-reconcile)
---------------------------------------------------------------------------
``reconcile_auto_items()``
    Idempotent reconcile: open one auto row per currently-low definition (any
    purchased state blocks a duplicate); prune open unchecked auto rows whose
    definition recovered.  Gated by ``shopping_list.auto_add_low_stock``
    setting (default True).  Reuses ``LowStockService.compute()`` — never
    re-derives the low-stock rule (roadmap §2.6 / M7 §4.3).

DB access only through ShoppingListRepository (roadmap §2.10).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.stock import SHOPPING_LIST_SOURCES
from app.models.shopping_list_item import ShoppingListItem
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.shopping_list import ShoppingListRepository
from app.schemas.shopping_list import ShoppingListIntake, ShoppingListItemUpdate
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


class ShoppingListService:
    """Business-logic facade for shopping-list operations.

    This is the **single mutation choke-point** for the shopping list (the
    TickTick seam reserved in M7 §12): all writes go through here, never
    directly to the repository.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ShoppingListRepository(db)
        self._def_repo = ItemDefinitionRepository(db)
        self._settings = SettingsService(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, item_id: int) -> ShoppingListItem:
        """Return a ShoppingListItem by PK or raise 404 (shopping_list.not_found)."""
        item = self._repo.get(item_id)
        if item is None:
            raise AppError(
                ErrorCode.SHOPPING_LIST_NOT_FOUND,
                status_code=404,
                params={"id": item_id},
                message=f"Shopping list item {item_id} not found.",
            )
        return item

    # ---------------------------------------------------------------------- #
    # CRUD operations                                                          #
    # ---------------------------------------------------------------------- #

    def add_manual(
        self,
        *,
        definition_id: int | None,
        name: str | None,
        desired_quantity: object | None,
        unit: str | None,
        note: str | None,
        created_by: int | None,
    ) -> ShoppingListItem:
        """Add a manual shopping-list item.

        Cross-field guard (M7 §3.1): at least one of ``definition_id`` /
        ``name`` must be provided, otherwise raises ``validation.invalid_input``
        (422).

        When ``definition_id`` is provided it must exist; raises
        ``item_definition.not_found`` (404) if not.

        Parameters
        ----------
        definition_id:
            FK to an item definition (optional for free-text items).
        name:
            Free-text label (required for definition-less items; ignored / kept
            NULL for definition-linked items because the display name is always
            read live from the definition).
        desired_quantity:
            How much to buy (Decimal / None).
        unit:
            Unit label for definition-less items.
        note:
            Optional free-text note.
        created_by:
            The acting user's id (or None if the request context carries no user).
        """
        # Cross-field guard: must have definition_id OR name.
        if definition_id is None and not name:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                message="A shopping list item must have either a definition_id or a name.",
            )

        # Validate definition exists when provided.
        if definition_id is not None:
            defn = self._def_repo.get(definition_id)
            if defn is None:
                raise AppError(
                    ErrorCode.ITEM_DEFINITION_NOT_FOUND,
                    status_code=404,
                    params={"id": definition_id},
                    message=f"Item definition {definition_id} not found.",
                )

        # For definition-linked rows, leave name NULL (live-resolved from def).
        stored_name = name if definition_id is None else None

        return self._repo.create(
            source=SHOPPING_LIST_SOURCES[1],  # "manual"
            definition_id=definition_id,
            name=stored_name,
            desired_quantity=desired_quantity,  # type: ignore[arg-type]
            unit=unit,
            note=note,
            created_by=created_by,
        )

    def edit(
        self,
        item_id: int,
        update: ShoppingListItemUpdate,
    ) -> ShoppingListItem:
        """PATCH an existing shopping-list item.

        Only the fields present in ``update.model_fields_set`` are applied to
        the row; absent fields leave the row unchanged.

        Raises ``shopping_list.not_found`` (404) when the item is missing.
        """
        item = self._get_or_404(item_id)

        fields: dict[str, object] = {}
        if "name" in update.model_fields_set:
            fields["name"] = update.name
        if "desired_quantity" in update.model_fields_set:
            fields["desired_quantity"] = update.desired_quantity
        if "note" in update.model_fields_set:
            fields["note"] = update.note

        if fields:
            self._repo.update(item, **fields)
        return item

    def check_off(
        self,
        item_id: int,
        intake: ShoppingListIntake | None = None,
    ) -> tuple[ShoppingListItem, int | None]:
        """Mark a shopping-list item as purchased, optionally delegating to the M2 ledger.

        Step 1 behaviour (``intake=None`` or row has no ``definition_id``):
            Only stamp ``purchased_at = now(UTC)``.  No stock creation, no new
            quantity math, no new ledger code.

        Step 3 behaviour (``intake`` provided + row has ``definition_id``):
            Algorithm from M7 §4.2 — implemented in **one transaction** (the
            route commits only at the end; a raised ``AppError`` propagates
            and the whole request transaction rolls back, leaving ``purchased_at``
            unchanged and no lot/movement created):

            1. Pre-check the definition is ``exact`` mode — if not, raise
               ``stock.movement_not_applicable`` (409).  This is the **only**
               check-off-specific stock logic; the lot creation itself is pure
               delegation (roadmap §2.3).

            2. Resolve intake quantity = ``intake.quantity ?? desired_quantity``.
               If **both** are NULL raise ``validation.invalid_input`` (422) —
               the caller must say how many were bought.

            3. Create a new lot by delegating to ``StockInstanceService.create``
               (records the initial ``intake`` movement; quantity stays
               **ledger-derived, never blind-set** — roadmap §2.3).
               ``location_id`` is passed only when explicitly provided in the
               intake body (non-None), so an omitted location falls through to
               the definition's ``default_location_id``.

            4. Stamp ``purchased_at = now(UTC)`` (AFTER the lot creation, so a
               failing intake propagates before this line and the stamp is never
               made within the same transaction).

        For auto rows this does NOT delete the row — it stays as the single
        auto row for its definition (M7 §3.1 / §4.2), removed only by
        ``clear_purchased``.

        Returns
        -------
        (item, created_instance_id)
            ``created_instance_id`` is ``None`` when no intake ran.

        Raises
        ------
        AppError(shopping_list.not_found, 404)
            When the item does not exist.
        AppError(stock.movement_not_applicable, 409)
            When ``intake`` is provided but the definition is not ``exact`` mode
            (§10 Step 3: use ``stock.movement_not_applicable``, NOT
            ``instance.field_mode_mismatch``).
        AppError(validation.invalid_input, 422)
            When ``intake`` is provided but neither ``intake.quantity`` nor
            ``desired_quantity`` is set.
        """
        item = self._get_or_404(item_id)
        now_utc = datetime.now(tz=UTC)
        created_instance_id: int | None = None

        if intake is not None and item.definition_id is not None:
            # Step 3 path: intake on a definition-linked item.
            # Fetch the definition to pre-check the tracking mode.
            defn = self._def_repo.get(item.definition_id)
            if defn is not None:
                # 1. Pre-check: only 'exact' mode definitions support intake.
                #    This is the only check-off-specific stock logic (M7 §4.2).
                if defn.stock_tracking_mode != "exact":
                    raise AppError(
                        ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE,
                        status_code=409,
                        message=(
                            "Stock movements are not applicable to this definition's tracking mode."
                        ),
                    )

                # 2. Resolve intake quantity: intake.quantity ?? desired_quantity.
                qty = intake.quantity if intake.quantity is not None else item.desired_quantity
                if qty is None:
                    raise AppError(
                        ErrorCode.INVALID_INPUT,
                        status_code=422,
                        message=(
                            "Cannot determine intake quantity: neither "
                            "intake.quantity nor desired_quantity is set."
                        ),
                    )

                # 3. Create a new lot by delegating to StockInstanceService.
                #    No new stock semantics or quantity math here — pure
                #    delegation (roadmap §2.3 / M7 §2 / §10 Step 3).
                #    Local imports to avoid circular dependencies (mirrors the
                #    LowStockService local import in reconcile_auto_items).
                from app.schemas.stock_instance import InstanceCreate
                from app.services.stock_instance import StockInstanceService

                if intake.location_id is not None:
                    # Explicit location: pass it so StockInstanceService validates
                    # and uses it (overrides definition default_location_id).
                    create_data = InstanceCreate(
                        definition_id=item.definition_id,
                        location_id=intake.location_id,
                        quantity=qty,
                    )
                else:
                    # No location specified: omit the field so StockInstanceService
                    # falls back to the definition's default_location_id.
                    create_data = InstanceCreate(
                        definition_id=item.definition_id,
                        quantity=qty,
                    )
                inst = StockInstanceService(self._db).create(create_data)
                created_instance_id = inst.id
            # If defn is None (definition deleted mid-request via concurrent
            # CASCADE — essentially impossible), fall through and just stamp.

        # 4. Stamp purchased_at = now.
        #    Placed AFTER the lot creation (step 3): if the intake raises,
        #    the AppError propagates before this line, purchased_at stays NULL,
        #    and the route's db.commit() is never reached — one transaction.
        self._repo.stamp_purchased(item, now_utc)
        return item, created_instance_id

    def uncheck(self, item_id: int) -> ShoppingListItem:
        """Revert a shopping-list item to the open/unchecked state.

        Clears ``purchased_at``.  Safe for auto rows because the per-def
        auto-row uniqueness is **state-independent** (``WHERE source='auto'``
        not ``… AND purchased_at IS NULL``), so clearing ``purchased_at``
        can never create a collision with a second auto row (M7 §3.1).

        Does NOT reverse any stock intake that may have occurred during check-
        off (a separate stock action; documented in M7 §4.2).

        Raises ``shopping_list.not_found`` (404) when the item is missing.
        """
        item = self._get_or_404(item_id)
        return self._repo.clear_purchased_at(item)

    def remove(self, item_id: int) -> None:
        """Hard-delete a shopping-list item.

        Raises ``shopping_list.not_found`` (404) when the item is missing.
        """
        item = self._get_or_404(item_id)
        self._repo.delete(item)

    def clear_purchased(self) -> int:
        """Delete all purchased (checked) items.

        Deletes all rows where ``purchased_at IS NOT NULL``, which includes
        both auto and manual rows that have been checked off.  Returns the
        count of deleted rows.
        """
        return self._repo.clear_purchased()

    def list_items(self, *, include_purchased: bool = False) -> list[ShoppingListItem]:
        """Return shopping-list items (with definition joinedloaded).

        Parameters
        ----------
        include_purchased:
            When ``True``, include checked items as well as open items.
        """
        return self._repo.list_all(include_purchased=include_purchased)

    # ---------------------------------------------------------------------- #
    # Auto-reconcile (Step 2)                                                  #
    # ---------------------------------------------------------------------- #

    def reconcile_auto_items(self) -> None:
        """Idempotent reconcile: open auto rows for low definitions, prune recovered ones.

        Implements M7 §4.3 (with post-M7 reopen-on-re-low fix):

        1. **Gate**: if ``shopping_list.auto_add_low_stock`` is False → return
           immediately (no-op).

        2. **Open**: for each currently-low definition (from
           ``LowStockService.compute()``), ensure exactly one *open* auto row
           exists.

           - If no auto row exists at all → create one.
           - If an auto row exists and is **open** (``purchased_at IS NULL``)
             → already satisfied, skip.
           - If an auto row exists but is **checked** (``purchased_at IS NOT
             NULL``) → **reopen it** by clearing ``purchased_at``.  This
             re-surfaces the suggestion when a definition goes low again after
             a prior check-off that was not backed by a real restock.  The
             one-auto-row-per-definition invariant is preserved (no new row).

           Intentional consequence: if a user checks off an auto row while its
           definition is still below ``min_stock`` (check without restock), the
           next reconcile/refresh will reopen it.  A genuine restock raises
           stock above ``min_stock``, so a recovered definition is never low
           and its checked row is never reopened.

           The partial-unique index + ``create``'s IntegrityError guard
           provide the DB backstop for concurrent reconcile calls.

        3. **Prune**: delete open (``purchased_at IS NULL``), unchecked auto
           rows whose definition is no longer low.  Never prune manual rows;
           never prune checked auto rows (they're cleared only by
           ``clear_purchased``).

        Invariants preserved:
        - Exactly one auto row per definition in any purchased state
          (``WHERE source='auto'`` partial-unique index).
        - A checked auto row whose definition is **still low** is reopened
          (``purchased_at`` cleared) so the suggestion resurfaces.
        - A check-off → uncheck round-trip on an auto row never collides
          (state-independent per-def uniqueness).

        Callers (M7 §4.3 / §2 locked-decisions):
        - ``_run_scan_job`` (daily scheduler)
        - ``POST /reminders/run`` route handler
        - ``StockMovementService`` event hook beside ``evaluate_low_stock``
          (best-effort + savepoint-isolated at the call site)
        - ``POST /shopping-list/refresh`` route handler

        All DB access is through ``ShoppingListRepository`` (roadmap §2.10).
        """
        # Gate: if auto-add is disabled, this is a no-op.
        if not self._settings.shopping_list_auto_add():
            logger.debug("reconcile_auto_items: auto_add_low_stock=false — skipping.")
            return

        # Reuse the M2/M4 low-stock signal — never re-derive the rule (roadmap §2.6).
        from app.services.low_stock import LowStockService

        low_items = LowStockService(self._db).compute()
        low_def_ids = {item.definition_id for item in low_items}

        # 1. Open: ensure ONE open auto row per currently-low definition.
        for item in low_items:
            existing = self._repo.get_auto_item(item.definition_id)
            if existing is None:
                # create() uses a savepoint + IntegrityError guard as the DB
                # backstop for a concurrent reconcile that inserted between our
                # check and this call.
                self._repo.create(
                    source="auto",
                    definition_id=item.definition_id,
                    desired_quantity=None,  # NULL = unspecified (M7 §4.3 / §1 level-mode)
                    created_by=None,
                )
            elif existing.purchased_at is not None:
                # Re-low after a prior purchase that was never cleared: reopen the
                # checked auto row so the suggestion resurfaces (fail-safe). Keeps the
                # one-auto-row-per-definition invariant — no new row, no collision.
                self._repo.clear_purchased_at(existing)

        # 2. Prune: drop OPEN, UNCHECKED auto rows whose definition recovered.
        #    Never prune manual rows; never prune checked auto rows.
        for row in self._repo.list_open_auto_items():
            if row.definition_id not in low_def_ids:
                self._repo.delete(row)
