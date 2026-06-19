"""Repository for the ItemDefinition table.

Pure data access — no business rules here.  Business logic (default-kind
resolution, FK validation) lives in ``app.services.item_definition``.

Public methods
--------------
get(id)                         Return an ItemDefinition by PK, or None.
list_all(q, category_ids)       Filtered flat list (case-insensitive name search + category subtree filter).
create(name, ...)               Insert and flush a new ItemDefinition.
update(defn, ...)               Apply partial field updates.
delete(defn)                    Delete an ItemDefinition row.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.item_definition import ItemDefinition


class ItemDefinitionRepository:
    """Data-access object for the item_definitions table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, definition_id: int) -> ItemDefinition | None:
        """Return an ItemDefinition by PK, or None if not found."""
        return self._db.get(ItemDefinition, definition_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        category_ids: Sequence[int] | None = None,
    ) -> list[ItemDefinition]:
        """Return a filtered flat list of item definitions.

        Parameters
        ----------
        q
            Case-insensitive substring match against ``name``.
        category_ids
            When provided (and non-empty), filter to only definitions whose
            ``category_id`` is in this collection.  Pass the selected category
            together with all its descendants to implement subtree filtering.
        """
        stmt = select(ItemDefinition)

        if q is not None:
            stmt = stmt.where(func.lower(ItemDefinition.name).contains(func.lower(q)))

        if category_ids is not None and len(category_ids) > 0:
            stmt = stmt.where(ItemDefinition.category_id.in_(category_ids))

        stmt = stmt.order_by(ItemDefinition.id)
        return list(self._db.scalars(stmt).all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        name: str,
        kind_id: int,
        description: str | None = None,
        category_id: int | None = None,
        unit: str = "pcs",
        default_location_id: int | None = None,
        stock_tracking_mode: str = "exact",
        min_stock: Decimal | None = None,
        default_best_before_days: int | None = None,
    ) -> ItemDefinition:
        """Insert a new ItemDefinition and flush to get its PK."""
        defn = ItemDefinition(
            name=name,
            kind_id=kind_id,
            description=description,
            category_id=category_id,
            unit=unit,
            default_location_id=default_location_id,
            stock_tracking_mode=stock_tracking_mode,
            min_stock=min_stock,
            default_best_before_days=default_best_before_days,
        )
        self._db.add(defn)
        self._db.flush()
        return defn

    def update(
        self,
        defn: ItemDefinition,
        *,
        name: str | None = None,
        description: str | None = None,
        kind_id: int | None = None,
        set_category_id: bool = False,
        category_id: int | None = None,
        unit: str | None = None,
        set_default_location_id: bool = False,
        default_location_id: int | None = None,
        stock_tracking_mode: str | None = None,
        set_min_stock: bool = False,
        min_stock: Decimal | None = None,
        set_default_best_before_days: bool = False,
        default_best_before_days: int | None = None,
    ) -> ItemDefinition:
        """Apply partial field updates to an ItemDefinition.

        Nullable FK fields (``category_id``, ``default_location_id``) use an
        explicit ``set_*`` flag to distinguish "don't change" from "set to
        NULL" — the same pattern as the Location/Category repositories.

        ``min_stock`` and ``default_best_before_days`` also use explicit
        ``set_*`` flags for the same reason (can legitimately be set to NULL
        to remove the threshold / shelf-life default).
        """
        if name is not None:
            defn.name = name
        if description is not None:
            defn.description = description
        if kind_id is not None:
            defn.kind_id = kind_id
        if set_category_id:
            defn.category_id = category_id
        if unit is not None:
            defn.unit = unit
        if set_default_location_id:
            defn.default_location_id = default_location_id
        if stock_tracking_mode is not None:
            defn.stock_tracking_mode = stock_tracking_mode
        if set_min_stock:
            defn.min_stock = min_stock
        if set_default_best_before_days:
            defn.default_best_before_days = default_best_before_days
        self._db.flush()
        return defn

    def delete(self, defn: ItemDefinition) -> None:
        """Delete an ItemDefinition row (caller must ensure it is safe to delete)."""
        self._db.delete(defn)
        self._db.flush()
