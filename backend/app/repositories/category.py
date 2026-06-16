"""Repository for the Category self-referential tree.

Pure data access — no business rules here.  Tree-specific logic (cycle
prevention, delete-guarding, nested DTO building) lives in
``app.services.category.CategoryService``.

Public methods
--------------
get(id)                  Return a Category by PK, or None.
list_all(q, parent_id)   Filtered flat list.
get_children(id)         Direct children of a node.
get_descendants(id)      All descendants (recursive BFS in Python — no
                         recursive SQL, per roadmap §2.11).
create(name, ...)        Insert and flush a new Category.
update(cat, ...)         Apply field updates.
delete(cat)              Delete a category row.
has_children(id)         True if the node has at least one child.
get_all_roots()          Root nodes (parent_id IS NULL).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.category import Category


class CategoryRepository:
    """Data-access object for the categories table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, category_id: int) -> Category | None:
        """Return a Category by PK, or None if not found."""
        return self._db.get(Category, category_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        parent_id: int | None = None,
        parent_id_filter: bool = False,
    ) -> list[Category]:
        """Return a filtered flat list of categories.

        Parameters
        ----------
        q
            Case-insensitive substring match against ``name``.
        parent_id
            When ``parent_id_filter`` is True, filter to only categories with
            this parent_id (pass ``None`` to get root nodes).
        parent_id_filter
            Must be set to True to activate the ``parent_id`` filter (so that
            callers can explicitly filter on NULL parent_id = root nodes).
        """
        stmt = select(Category)

        if q is not None:
            stmt = stmt.where(func.lower(Category.name).contains(func.lower(q)))

        if parent_id_filter:
            if parent_id is None:
                stmt = stmt.where(Category.parent_id.is_(None))
            else:
                stmt = stmt.where(Category.parent_id == parent_id)

        stmt = stmt.order_by(Category.id)
        return list(self._db.scalars(stmt).all())

    def get_children(self, category_id: int) -> list[Category]:
        """Return direct children of the given category."""
        stmt = select(Category).where(Category.parent_id == category_id).order_by(Category.id)
        return list(self._db.scalars(stmt).all())

    def get_descendants(self, category_id: int) -> list[Category]:
        """Return all descendants of the given category (recursive BFS).

        Implemented in Python (no recursive SQL) per roadmap §2.11.
        Returns an empty list if the node has no descendants.
        """
        result: list[Category] = []
        queue: list[Category] = self.get_children(category_id)
        while queue:
            node = queue.pop(0)
            result.append(node)
            queue.extend(self.get_children(node.id))
        return result

    def has_children(self, category_id: int) -> bool:
        """Return True if the category has at least one direct child."""
        stmt = select(Category.id).where(Category.parent_id == category_id).limit(1)
        return self._db.scalars(stmt).first() is not None

    def get_all_roots(self) -> list[Category]:
        """Return all root categories (parent_id IS NULL)."""
        stmt = select(Category).where(Category.parent_id.is_(None)).order_by(Category.id)
        return list(self._db.scalars(stmt).all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        parent_id: int | None = None,
    ) -> Category:
        """Insert a new Category and flush to get its PK."""
        cat = Category(name=name, description=description, parent_id=parent_id)
        self._db.add(cat)
        self._db.flush()
        return cat

    def update(
        self,
        cat: Category,
        *,
        name: str | None = None,
        description: str | None = None,
        set_parent_id: bool = False,
        parent_id: int | None = None,
    ) -> Category:
        """Apply field updates to a Category.

        ``parent_id`` uses an explicit ``set_parent_id`` flag to distinguish
        "don't change parent_id" from "explicitly set parent_id = None" (root).
        When ``set_parent_id=True``, the ``parent_id`` value (which may be
        ``None`` for reparenting to root) is written.
        """
        if name is not None:
            cat.name = name
        if description is not None:
            cat.description = description
        if set_parent_id:
            cat.parent_id = parent_id
        self._db.flush()
        return cat

    def delete(self, cat: Category) -> None:
        """Delete a Category row (caller must ensure it is safe to delete)."""
        self._db.delete(cat)
        self._db.flush()
