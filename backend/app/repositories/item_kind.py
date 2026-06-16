"""Repository for the ItemKind lookup table.

Read-only: ``item_kinds`` is seeded by migration 0006 and exposed as a
read-only list over the API.  No write methods are provided here; kinds CRUD
is deferred to M1.md §12.

Public methods
--------------
list_all()        Return all item kinds (ordered by id).
get_by_code(code) Return an ItemKind by its stable machine key, or None.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.item_kind import ItemKind


class ItemKindRepository:
    """Read-only data-access object for the item_kinds table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_all(self) -> list[ItemKind]:
        """Return all item kinds ordered by id."""
        stmt = select(ItemKind).order_by(ItemKind.id)
        return list(self._db.scalars(stmt).all())

    def get_by_code(self, code: str) -> ItemKind | None:
        """Return an ItemKind by its stable machine key, or None if not found."""
        stmt = select(ItemKind).where(ItemKind.code == code)
        return self._db.scalars(stmt).first()
