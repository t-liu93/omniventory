"""Service layer for the Category tree.

Delegates shared tree logic to ``TreeServiceMixin``:

1. **Cycle prevention** on reparent (roadmap §2.11, M1 §3.2).
2. **Delete-guard**: deleting a non-empty node → HTTP 409 (M1 §2).
3. **Nested tree DTO building**: assembles the recursive ``CategoryTreeNode``
   from a flat list of all categories (single DB read, Python nesting).

All DB access goes through ``CategoryRepository``.  The shared guards in
``TreeServiceMixin`` are the same code path used by ``LocationService``,
ensuring no copy-paste divergence (M1.md §10 Step-2 checkpoint).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.category import Category
from app.repositories.category import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryTreeNode, CategoryUpdate
from app.services.tree import TreeServiceMixin


class CategoryService(TreeServiceMixin):
    """Business-logic facade for Category tree operations."""

    _repo: CategoryRepository  # narrows the mixin's _TreeRepoProtocol for mypy

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = CategoryRepository(db)

    # ---------------------------------------------------------------------- #
    # Helpers                                                                  #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, category_id: int) -> Category:
        """Return a Category or raise HTTP 404."""
        cat = self._repo.get(category_id)
        if cat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found.",
            )
        return cat

    def _assert_parent_exists(self, parent_id: int) -> None:
        """Raise HTTP 404 if the proposed parent does not exist."""
        if self._repo.get(parent_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent category {parent_id} not found.",
            )

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: CategoryCreate) -> Category:
        """Create a new category.

        Validates that the parent exists (if provided).
        """
        if data.parent_id is not None:
            self._assert_parent_exists(data.parent_id)
        return self._repo.create(
            name=data.name,
            description=data.description,
            parent_id=data.parent_id,
        )

    def get(self, category_id: int) -> Category:
        """Return a category by PK, or raise 404."""
        return self._get_or_404(category_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        parent_id: int | None = None,
        parent_id_filter: bool = False,
    ) -> list[Category]:
        """Return a filtered flat list of categories."""
        return self._repo.list_all(q=q, parent_id=parent_id, parent_id_filter=parent_id_filter)

    def update(self, category_id: int, data: CategoryUpdate) -> Category:
        """Apply a partial update to a category.

        If ``parent_id`` is present in the payload, cycle-checks are run.
        """
        cat = self._get_or_404(category_id)

        new_parent_id = data.parent_id
        parent_id_changed = "parent_id" in data.model_fields_set

        if parent_id_changed and new_parent_id is not None:
            self._assert_parent_exists(new_parent_id)
            self._assert_no_cycle(category_id, new_parent_id, kind="category")

        return self._repo.update(
            cat,
            name=data.name,
            description=data.description,
            set_parent_id=parent_id_changed,
            parent_id=new_parent_id,
        )

    def delete(self, category_id: int) -> None:
        """Delete a category (guarded — 409 if it has children)."""
        cat = self._get_or_404(category_id)
        self._assert_deletable(category_id, cat.name, kind="category")
        self._repo.delete(cat)

    # ---------------------------------------------------------------------- #
    # Tree                                                                     #
    # ---------------------------------------------------------------------- #

    def get_tree(self) -> list[CategoryTreeNode]:
        """Build the full nested category tree.

        Fetches all categories in a single DB query and nests them in Python.
        Returns a list of root-level ``CategoryTreeNode`` objects.
        """
        all_categories = self._repo.list_all()
        return _build_tree(all_categories)


# ---------------------------------------------------------------------------
# Module-level helper (no DB access)
# ---------------------------------------------------------------------------


def _build_tree(categories: list[Category]) -> list[CategoryTreeNode]:
    """Nest a flat list of Category rows into a recursive tree structure.

    Algorithm: two-pass Python — O(n).

    1. Build a dict from ``id → CategoryTreeNode`` (children=[]).
    2. Iterate again: each node with a ``parent_id`` appends itself to the
       parent's ``children`` list.  Nodes with ``parent_id = NULL`` are
       collected as root nodes.

    Ordering within each level is by ``id`` (ascending), preserving insertion
    order from the DB query (which orders by ``id``).
    """
    node_map: dict[int, CategoryTreeNode] = {}
    for cat in categories:
        node_map[cat.id] = CategoryTreeNode(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            parent_id=cat.parent_id,
            created_at=cat.created_at,
            children=[],
        )

    roots: list[CategoryTreeNode] = []
    for cat in categories:
        node = node_map[cat.id]
        if cat.parent_id is None:
            roots.append(node)
        else:
            parent_node = node_map.get(cat.parent_id)
            if parent_node is not None:
                parent_node.children.append(node)

    return roots
