"""Service layer for the Location tree.

Holds the easy-to-get-wrong tree logic by delegating to ``TreeServiceMixin``:

1. **Cycle prevention** on reparent: the new parent must not be the node
   itself, nor any of its descendants (roadmap §2.11, M1 §3.1).
2. **Delete-guard**: deleting a non-empty node (one with children) is blocked
   with HTTP 409 (M1 §2 "Tree delete semantics").
3. **Nested tree DTO building**: assembles the recursive ``LocationTreeNode``
   structure from a flat list of all locations (single DB read, recursive
   nesting in Python — no recursive SQL, per roadmap §2.11).

All DB access goes through ``LocationRepository``.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.location import Location
from app.repositories.location import LocationRepository
from app.schemas.location import LocationCreate, LocationTreeNode, LocationUpdate
from app.services.tree import TreeServiceMixin


class LocationService(TreeServiceMixin):
    """Business-logic facade for Location tree operations."""

    _repo: LocationRepository  # narrows the mixin's _TreeRepoProtocol for mypy

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = LocationRepository(db)

    # ---------------------------------------------------------------------- #
    # Helpers                                                                  #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, location_id: int) -> Location:
        """Return a Location or raise HTTP 404."""
        loc = self._repo.get(location_id)
        if loc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location {location_id} not found.",
            )
        return loc

    def _assert_parent_exists(self, parent_id: int) -> None:
        """Raise HTTP 404 if the proposed parent does not exist."""
        if self._repo.get(parent_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent location {parent_id} not found.",
            )

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: LocationCreate) -> Location:
        """Create a new location.

        Validates that the parent exists (if provided).
        """
        if data.parent_id is not None:
            self._assert_parent_exists(data.parent_id)
        return self._repo.create(
            name=data.name,
            description=data.description,
            parent_id=data.parent_id,
        )

    def get(self, location_id: int) -> Location:
        """Return a location by PK, or raise 404."""
        return self._get_or_404(location_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        parent_id: int | None = None,
        parent_id_filter: bool = False,
    ) -> list[Location]:
        """Return a filtered flat list of locations."""
        return self._repo.list_all(q=q, parent_id=parent_id, parent_id_filter=parent_id_filter)

    def update(self, location_id: int, data: LocationUpdate) -> Location:
        """Apply a partial update to a location.

        If ``parent_id`` is present in the payload, cycle-checks are run.
        """
        loc = self._get_or_404(location_id)

        # If a reparent is requested, validate it.
        new_parent_id = data.parent_id
        parent_id_changed = "parent_id" in data.model_fields_set

        if parent_id_changed and new_parent_id is not None:
            self._assert_parent_exists(new_parent_id)
            self._assert_no_cycle(location_id, new_parent_id, kind="location")

        return self._repo.update(
            loc,
            name=data.name,
            description=data.description,
            set_parent_id=parent_id_changed,
            parent_id=new_parent_id,
        )

    def delete(self, location_id: int) -> None:
        """Delete a location (guarded — 409 if it has children)."""
        loc = self._get_or_404(location_id)
        self._assert_deletable(location_id, loc.name, kind="location")
        self._repo.delete(loc)

    # ---------------------------------------------------------------------- #
    # Tree                                                                     #
    # ---------------------------------------------------------------------- #

    def get_tree(self) -> list[LocationTreeNode]:
        """Build the full nested location tree.

        Fetches all locations in a single DB query and nests them in Python.
        Returns a list of root-level ``LocationTreeNode`` objects.
        """
        all_locations = self._repo.list_all()
        return _build_tree(all_locations)


# ---------------------------------------------------------------------------
# Module-level helper (no DB access)
# ---------------------------------------------------------------------------


def _build_tree(locations: list[Location]) -> list[LocationTreeNode]:
    """Nest a flat list of Location rows into a recursive tree structure.

    Algorithm: two-pass Python — O(n).

    1. Build a dict from ``id → LocationTreeNode`` (children=[]).
    2. Iterate again: each node with a ``parent_id`` appends itself to the
       parent's ``children`` list.  Nodes with ``parent_id = NULL`` are
       collected as root nodes.

    Ordering within each level is by ``id`` (ascending), preserving insertion
    order from the DB query (which orders by ``id``).
    """
    node_map: dict[int, LocationTreeNode] = {}
    for loc in locations:
        # Build each node from the scalar columns only (ignore the ORM
        # ``children`` relationship — we populate children ourselves below to
        # avoid duplicating nodes that are already loaded by the relationship).
        node_map[loc.id] = LocationTreeNode(
            id=loc.id,
            name=loc.name,
            description=loc.description,
            parent_id=loc.parent_id,
            created_at=loc.created_at,
            children=[],
        )

    roots: list[LocationTreeNode] = []
    for loc in locations:
        node = node_map[loc.id]
        if loc.parent_id is None:
            roots.append(node)
        else:
            parent_node = node_map.get(loc.parent_id)
            if parent_node is not None:
                parent_node.children.append(node)

    return roots
