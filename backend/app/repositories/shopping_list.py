"""Repository for the ShoppingListItem table (M7 §4.1 / §9 Step 1 + Step 2).

All DB access to the ``shopping_list_items`` table goes through this class.
Route handlers and services must not issue raw queries; they call
``ShoppingListRepository`` methods.

Public methods (Step 1 — CRUD)
-----------------------------------------------------------------------
``create(...)``
    Insert a new ShoppingListItem row inside a SAVEPOINT.  If the partial-unique
    index ``uq_shopping_list_one_auto_per_def`` fires (concurrent reconcile for
    the same auto+definition), the IntegrityError is caught, the savepoint is
    rolled back, and the **existing** row is returned instead.  Manual creates
    (source='manual') are unaffected because the constraint only applies to
    ``source='auto'`` rows.

``get(item_id)``
    Return a ShoppingListItem by PK (with definition joinedloaded), or None.

``list_all(include_purchased, ...)``
    Return all items (open first, then purchased) with definition joinedloaded.

``update(item, **fields)``
    Apply field updates to an existing row and flush.

``delete(item)``
    Delete a ShoppingListItem row and flush.

``clear_purchased()``
    Delete all rows where ``purchased_at IS NOT NULL``; return the count of
    deleted rows.

Public methods (Step 2 — reconcile helpers)
-----------------------------------------------------------------------
``get_auto_item(definition_id)``
    Return the auto row for a definition in **any** purchased state (open or
    checked), or None.  Used by reconcile to decide whether to create a new
    auto row.

``list_open_auto_items()``
    Return all auto rows with ``purchased_at IS NULL`` (the prune candidates).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.shopping_list_item import ShoppingListItem
from app.repositories._update_guard import reject_null_on_non_nullable


class ShoppingListRepository:
    """Data-access object for the shopping_list_items table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, item_id: int) -> ShoppingListItem | None:
        """Return a ShoppingListItem by PK with its definition joinedloaded, or None."""
        stmt = (
            select(ShoppingListItem)
            .options(joinedload(ShoppingListItem.definition))
            .where(ShoppingListItem.id == item_id)
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_all(self, *, include_purchased: bool = False) -> list[ShoppingListItem]:
        """Return shopping list items with definition joinedloaded.

        Ordering: open items (``purchased_at IS NULL``) first, then purchased,
        each sub-group sorted by ``created_at ASC`` for stable ordering.

        Parameters
        ----------
        include_purchased:
            When ``True``, include checked/purchased items in the result.
            When ``False`` (default), return only open items.
        """
        stmt = (
            select(ShoppingListItem)
            .options(joinedload(ShoppingListItem.definition))
            # Open items (0) before purchased items (1); stable secondary order.
            .order_by(
                case((ShoppingListItem.purchased_at.is_(None), 0), else_=1),
                ShoppingListItem.created_at.asc(),
            )
        )
        if not include_purchased:
            stmt = stmt.where(ShoppingListItem.purchased_at.is_(None))
        return list(self._db.execute(stmt).scalars().all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        source: str,
        definition_id: int | None = None,
        name: str | None = None,
        desired_quantity: Decimal | None = None,
        unit: str | None = None,
        note: str | None = None,
        created_by: int | None = None,
    ) -> ShoppingListItem:
        """Insert a new ShoppingListItem row inside a SAVEPOINT and flush.

        The caller is responsible for source validation (app-layer) and the
        cross-field name/definition_id check before calling this method.

        Step 2 change: the INSERT is wrapped in a ``begin_nested()`` savepoint
        so that an ``IntegrityError`` from the partial-unique index
        ``uq_shopping_list_one_auto_per_def`` (which fires when a concurrent
        reconcile already inserted an auto row for the same definition) rolls
        back only the savepoint, **not** the outer transaction.  The method then
        re-fetches and returns the existing winning row.

        Manual creates (source='manual') are never subject to this constraint
        (the index predicate is ``WHERE source='auto'``), so their behaviour is
        unchanged.

        Implementation mirrors ``NotificationRepository.create_if_absent``
        (M4 §9 Step 4 F2 fix).
        """
        item = ShoppingListItem(
            source=source,
            definition_id=definition_id,
            name=name,
            desired_quantity=desired_quantity,
            unit=unit,
            note=note,
            created_by=created_by,
        )
        try:
            with self._db.begin_nested():
                self._db.add(item)
                # flush() inside the savepoint materialises the INSERT so the
                # unique-constraint check happens now (within the savepoint).
                self._db.flush()
            return item
        except IntegrityError:
            # Unique constraint hit: another concurrent reconcile inserted an
            # auto row for the same definition between our check and INSERT.
            # The savepoint was rolled back automatically; the outer transaction
            # is intact.  Re-fetch and return the winning row.
            if definition_id is not None:
                existing = self.get_auto_item(definition_id)
                if existing is not None:
                    return existing
            raise  # Unexpected integrity error — re-raise.

    def update(self, item: ShoppingListItem, **fields: object) -> ShoppingListItem:
        """Apply field updates to an existing ShoppingListItem and flush.

        Only the keys present in ``fields`` are updated.  Pass keyword
        arguments for each column you want to change.

        SQLAlchemy's ``onupdate=func.now()`` on ``updated_at`` ensures the
        timestamp is refreshed when the row is flushed.

        Raises
        ------
        AppError(validation.invalid_input, 422)
            When any field maps to a NOT NULL column and the supplied value is
            ``None`` (guard: converts a potential IntegrityError 500 to a clean
            422 before the flush).
        """
        reject_null_on_non_nullable(item, fields)
        for key, value in fields.items():
            setattr(item, key, value)
        self._db.flush()
        return item

    def delete(self, item: ShoppingListItem) -> None:
        """Delete a ShoppingListItem row and flush."""
        self._db.delete(item)
        self._db.flush()

    def clear_purchased(self) -> int:
        """Delete all rows where ``purchased_at IS NOT NULL``.

        Returns the number of deleted rows.

        Uses a bulk DELETE for efficiency.  After the DELETE the session is
        flushed so the caller's subsequent queries reflect the change.
        """
        # Count first (needed because bulk DELETE doesn't expose rowcount
        # reliably across all SQLAlchemy dialects; explicit count is safe).
        count_stmt = select(ShoppingListItem).where(ShoppingListItem.purchased_at.is_not(None))
        rows = list(self._db.execute(count_stmt).scalars().all())
        count = len(rows)
        if count == 0:
            return 0

        bulk_stmt = (
            delete(ShoppingListItem)
            .where(ShoppingListItem.purchased_at.is_not(None))
            .execution_options(synchronize_session="fetch")
        )
        self._db.execute(bulk_stmt)
        self._db.flush()
        return count

    # ---------------------------------------------------------------------- #
    # Helpers for auto-reconcile (Step 2)                                     #
    # ---------------------------------------------------------------------- #

    def get_auto_item(self, definition_id: int) -> ShoppingListItem | None:
        """Return the auto row for a definition in **any** purchased state, or None.

        The partial-unique index ``uq_shopping_list_one_auto_per_def`` guarantees
        there is at most one such row per definition.  Both open (purchased_at IS
        NULL) and checked (purchased_at IS NOT NULL) rows are considered —
        matching the state-independent uniqueness invariant from M7 §3.1.

        Used by ``reconcile_auto_items`` to decide whether an auto row already
        exists before attempting a create (which would be a no-op via the
        IntegrityError guard), and to block a duplicate open row when a checked
        auto row for the same definition is still present.
        """
        stmt = select(ShoppingListItem).where(
            ShoppingListItem.source == "auto",
            ShoppingListItem.definition_id == definition_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_open_auto_items(self) -> list[ShoppingListItem]:
        """Return all auto rows with ``purchased_at IS NULL`` (prune candidates).

        Used by ``reconcile_auto_items`` in the prune phase: auto rows whose
        definition has recovered above its threshold are deleted from this list.
        Only open/unchecked rows are pruned; checked auto rows are left alone
        (M7 §4.3).
        """
        stmt = select(ShoppingListItem).where(
            ShoppingListItem.source == "auto",
            ShoppingListItem.purchased_at.is_(None),
        )
        return list(self._db.execute(stmt).scalars().all())

    # ---------------------------------------------------------------------- #
    # Helpers used by check-off / uncheck (Step 1)                            #
    # ---------------------------------------------------------------------- #

    def stamp_purchased(self, item: ShoppingListItem, at: datetime) -> ShoppingListItem:
        """Set ``purchased_at`` on an item (check-off).

        Does not validate existence — the caller must fetch the item first.
        """
        item.purchased_at = at
        self._db.flush()
        return item

    def clear_purchased_at(self, item: ShoppingListItem) -> ShoppingListItem:
        """Clear ``purchased_at`` on an item (uncheck).

        Does not validate existence — the caller must fetch the item first.
        """
        item.purchased_at = None
        self._db.flush()
        return item
