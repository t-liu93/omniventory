"""Repository for the Location self-referential tree.

Pure data access — no business rules here.  Tree-specific logic (cycle
prevention, delete-guarding, nested DTO building) lives in
``app.services.location.LocationService``.

Public methods
--------------
get(id)                 Return a Location by PK, or None.
list_all(q, parent_id)  Filtered flat list.
get_children(id)        Direct children of a node.
get_descendants(id)     All descendants (recursive BFS/DFS in Python — no
                        recursive SQL, per roadmap §2.11).
create(name, ...)       Insert and flush a new Location.
update(loc, ...)        Apply field updates.
delete(loc)             Delete a location row.
has_children(id)        True if the node has at least one child.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.location import Location


class LocationRepository:
    """Data-access object for the locations table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, location_id: int) -> Location | None:
        """Return a Location by PK, or None if not found."""
        return self._db.get(Location, location_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        parent_id: int | None = None,
        parent_id_filter: bool = False,
    ) -> list[Location]:
        """Return a filtered flat list of locations.

        Parameters
        ----------
        q
            Case-insensitive substring match against ``name``.
        parent_id
            When ``parent_id_filter`` is True, filter to only locations with
            this parent_id (pass ``None`` to get root nodes).
        parent_id_filter
            Must be set to True to activate the ``parent_id`` filter (so that
            callers can explicitly filter on NULL parent_id = root nodes).
        """
        stmt = select(Location)

        if q is not None:
            stmt = stmt.where(func.lower(Location.name).contains(func.lower(q)))

        if parent_id_filter:
            if parent_id is None:
                stmt = stmt.where(Location.parent_id.is_(None))
            else:
                stmt = stmt.where(Location.parent_id == parent_id)

        stmt = stmt.order_by(Location.id)
        return list(self._db.scalars(stmt).all())

    def get_children(self, location_id: int) -> list[Location]:
        """Return direct children of the given location."""
        stmt = select(Location).where(Location.parent_id == location_id).order_by(Location.id)
        return list(self._db.scalars(stmt).all())

    def get_descendants(self, location_id: int) -> list[Location]:
        """Return all descendants of the given location (recursive BFS).

        Implemented in Python (no recursive SQL) per roadmap §2.11.
        Returns an empty list if the node has no descendants.
        """
        result: list[Location] = []
        queue: list[Location] = self.get_children(location_id)
        while queue:
            node = queue.pop(0)
            result.append(node)
            queue.extend(self.get_children(node.id))
        return result

    def has_children(self, location_id: int) -> bool:
        """Return True if the location has at least one direct child."""
        stmt = select(Location.id).where(Location.parent_id == location_id).limit(1)
        return self._db.scalars(stmt).first() is not None

    def get_all_roots(self) -> list[Location]:
        """Return all root locations (parent_id IS NULL)."""
        stmt = select(Location).where(Location.parent_id.is_(None)).order_by(Location.id)
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
    ) -> Location:
        """Insert a new Location and flush to get its PK."""
        loc = Location(name=name, description=description, parent_id=parent_id)
        self._db.add(loc)
        self._db.flush()
        return loc

    def update(
        self,
        loc: Location,
        *,
        name: str | None = None,
        description: str | None = None,
        set_parent_id: bool = False,
        parent_id: int | None = None,
    ) -> Location:
        """Apply field updates to a Location.

        ``parent_id`` uses an explicit ``set_parent_id`` flag to distinguish
        "don't change parent_id" from "explicitly set parent_id = None" (root).
        When ``set_parent_id=True``, the ``parent_id`` value (which may be
        ``None`` for reparenting to root) is written.
        """
        if name is not None:
            loc.name = name
        if description is not None:
            loc.description = description
        if set_parent_id:
            loc.parent_id = parent_id
        self._db.flush()
        return loc

    def delete(self, loc: Location) -> None:
        """Delete a Location row (caller must ensure it is safe to delete)."""
        self._db.delete(loc)
        self._db.flush()
