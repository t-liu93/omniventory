"""Service layer for the Location tree.

Holds the easy-to-get-wrong tree logic by delegating to ``TreeServiceMixin``:

1. **Cycle prevention** on reparent: the new parent must not be the node
   itself, nor any of its descendants (roadmap §2.11, M1 §3.1).
2. **Delete-guard**: deleting a non-empty node (one with children) is blocked
   with HTTP 409 (M1 §2 "Tree delete semantics").  Step 4 extends this: a
   location is also blocked if it has assigned stock instances or is linked
   as a container (``item_instance_id`` is set).
3. **Nested tree DTO building**: assembles the recursive ``LocationTreeNode``
   structure from a flat list of all locations (single DB read, recursive
   nesting in Python — no recursive SQL, per roadmap §2.11).
4. **Container-as-item link/unlink** (Step 4): ``item_instance_id`` can be
   set via ``PATCH /locations/{id}``; the service enforces uniqueness
   (one instance ↔ one location) and that the target instance exists.

All DB access goes through ``LocationRepository`` (and
``StockInstanceRepository`` for the instance-existence check).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.item_definition import ItemDefinition
from app.models.location import Location
from app.models.stock_instance import StockInstance
from app.repositories.location import LocationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.schemas.location import LocationCreate, LocationResponse, LocationTreeNode, LocationUpdate
from app.services.tree import TreeServiceMixin


class LocationService(TreeServiceMixin):
    """Business-logic facade for Location tree operations."""

    _repo: LocationRepository  # narrows the mixin's _TreeRepoProtocol for mypy

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = LocationRepository(db)
        self._inst_repo = StockInstanceRepository(db)

    # ---------------------------------------------------------------------- #
    # Helpers                                                                  #
    # ---------------------------------------------------------------------- #

    def _build_container_label_map(self, instance_ids: list[int]) -> dict[int, str]:
        """Return a map from instance_id → human-readable asset label.

        Executes a single JOIN query to resolve definition name (+ serial if
        present) for a given list of instance IDs.  The result is used to
        populate ``container_asset_label`` on ``LocationResponse`` /
        ``LocationTreeNode`` objects without an N+1 fetch pattern.

        Label format:
          - ``"<definition name>"`` when serial is absent.
          - ``"<definition name> · SN <serial>"`` when serial is present.
        """
        if not instance_ids:
            return {}

        stmt = (
            select(StockInstance.id, StockInstance.serial, ItemDefinition.name)
            .join(ItemDefinition, StockInstance.definition_id == ItemDefinition.id)
            .where(StockInstance.id.in_(instance_ids))
        )
        rows = self._db.execute(stmt).all()

        result: dict[int, str] = {}
        for inst_id, serial, def_name in rows:
            label = def_name if not serial else f"{def_name} · SN {serial}"
            result[inst_id] = label
        return result

    def _get_or_404(self, location_id: int) -> Location:
        """Return a Location or raise HTTP 404."""
        loc = self._repo.get(location_id)
        if loc is None:
            raise AppError(
                ErrorCode.LOCATION_NOT_FOUND,
                status_code=404,
                params={"id": location_id},
                message=f"Location {location_id} not found.",
            )
        return loc

    def _assert_parent_exists(self, parent_id: int) -> None:
        """Raise HTTP 404 if the proposed parent does not exist."""
        if self._repo.get(parent_id) is None:
            raise AppError(
                ErrorCode.LOCATION_PARENT_NOT_FOUND,
                status_code=404,
                params={"id": parent_id},
                message=f"Parent location {parent_id} not found.",
            )

    def _assert_instance_exists(self, instance_id: int) -> None:
        """Raise HTTP 404 if the stock instance does not exist."""
        if self._inst_repo.get(instance_id) is None:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_NOT_FOUND,
                status_code=404,
                params={"id": instance_id},
                message=f"Stock instance {instance_id} not found.",
            )

    def _assert_instance_id_unique(
        self,
        instance_id: int,
        *,
        exclude_location_id: int | None = None,
    ) -> None:
        """Raise HTTP 409 if another location is already linked to this instance.

        The ``item_instance_id`` column has a DB-level UNIQUE constraint, but
        we enforce it here (in the service layer) first to return a meaningful
        409 instead of a raw DB IntegrityError.
        """
        stmt = select(Location).where(Location.item_instance_id == instance_id)
        if exclude_location_id is not None:
            stmt = stmt.where(Location.id != exclude_location_id)
        existing = self._db.scalars(stmt).first()
        if existing is not None:
            raise AppError(
                ErrorCode.LOCATION_CONTAINER_LINK_CONFLICT,
                status_code=409,
                params={"id": instance_id},
                message=(
                    f"Stock instance {instance_id} is already linked to location "
                    f"'{existing.name}' (id={existing.id}). "
                    "Unlink it there first."
                ),
            )

    def _assert_deletable_location(self, loc: Location) -> None:
        """Raise HTTP 409 if the location cannot be deleted.

        Blocks deletion when:
        - The location has child locations (inherited tree-guard).
        - The location has stock instances assigned to it.
        - The location is linked as a container (item_instance_id is set).
        """
        # 1. Child locations (tree-guard from mixin).
        self._assert_deletable(loc.id, loc.name, kind="location")

        # 2. Assigned stock instances.
        if self._inst_repo.has_instances_at_location(loc.id):
            raise AppError(
                ErrorCode.LOCATION_DELETE_IN_USE,
                status_code=409,
                params={"id": loc.id},
                message=(
                    f"Location '{loc.name}' (id={loc.id}) cannot be deleted "
                    "because it has stock instances assigned to it. "
                    "Move or delete the instances first."
                ),
            )

        # 3. Linked as a container-as-item.
        if loc.item_instance_id is not None:
            raise AppError(
                ErrorCode.LOCATION_DELETE_IN_USE,
                status_code=409,
                params={"id": loc.id},
                message=(
                    f"Location '{loc.name}' (id={loc.id}) cannot be deleted "
                    "because it is linked as a container to stock instance "
                    f"{loc.item_instance_id}. Unlink it first."
                ),
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
        """Return a location ORM object by PK, or raise 404 (internal use)."""
        return self._get_or_404(location_id)

    def get_with_label(self, location_id: int) -> LocationResponse:
        """Return a ``LocationResponse`` (with ``container_asset_label``) by PK.

        Raises HTTP 404 if the location does not exist.
        """
        loc = self._get_or_404(location_id)
        resp = LocationResponse.model_validate(loc)
        if loc.item_instance_id is not None:
            label_map = self._build_container_label_map([loc.item_instance_id])
            resp.container_asset_label = label_map.get(loc.item_instance_id)
        return resp

    def list_all(
        self,
        *,
        q: str | None = None,
        parent_id: int | None = None,
        parent_id_filter: bool = False,
    ) -> list[LocationResponse]:
        """Return a filtered flat list of locations as ``LocationResponse`` DTOs.

        Each response includes ``container_asset_label`` for container-as-item
        locations (resolved in a single join query — no N+1).
        """
        locs = self._repo.list_all(q=q, parent_id=parent_id, parent_id_filter=parent_id_filter)

        # Resolve container asset labels in one query.
        container_instance_ids = [
            loc.item_instance_id for loc in locs if loc.item_instance_id is not None
        ]
        label_map = self._build_container_label_map(container_instance_ids)

        responses: list[LocationResponse] = []
        for loc in locs:
            resp = LocationResponse.model_validate(loc)
            resp.container_asset_label = (
                label_map.get(loc.item_instance_id) if loc.item_instance_id is not None else None
            )
            responses.append(resp)
        return responses

    def update(self, location_id: int, data: LocationUpdate) -> Location:
        """Apply a partial update to a location.

        If ``parent_id`` is present in the payload, cycle-checks are run.
        If ``item_instance_id`` is present, container-as-item link/unlink
        is validated and applied.
        """
        loc = self._get_or_404(location_id)

        # If a reparent is requested, validate it.
        new_parent_id = data.parent_id
        parent_id_changed = "parent_id" in data.model_fields_set

        if parent_id_changed and new_parent_id is not None:
            self._assert_parent_exists(new_parent_id)
            self._assert_no_cycle(location_id, new_parent_id, kind="location")

        # Container-as-item link/unlink.
        item_instance_id_changed = "item_instance_id" in data.model_fields_set
        if item_instance_id_changed and data.item_instance_id is not None:
            self._assert_instance_exists(data.item_instance_id)
            self._assert_instance_id_unique(data.item_instance_id, exclude_location_id=location_id)

        return self._repo.update(
            loc,
            name=data.name,
            description=data.description,
            set_parent_id=parent_id_changed,
            parent_id=new_parent_id,
            set_item_instance_id=item_instance_id_changed,
            item_instance_id=data.item_instance_id,
        )

    def delete(self, location_id: int) -> list[Path]:
        """Delete a location (guarded — 409 if it has children, instances, or is a container).

        Cascades attachments (M5 Step 1) before removing the row.

        Returns
        -------
        List of on-disk media paths to unlink after ``db.commit()`` (best-effort).
        """
        from app.config import get_settings
        from app.services.attachment import AttachmentService

        loc = self._get_or_404(location_id)
        self._assert_deletable_location(loc)
        settings = get_settings()
        media_dir = Path(settings.data_dir) / "media"
        paths = AttachmentService(self._db, media_dir=media_dir).delete_for_owner(
            "location", location_id
        )
        self._repo.delete(loc)
        return paths

    # ---------------------------------------------------------------------- #
    # Tree                                                                     #
    # ---------------------------------------------------------------------- #

    def get_tree(self) -> list[LocationTreeNode]:
        """Build the full nested location tree.

        Fetches all locations in a single DB query and nests them in Python.
        Resolves ``container_asset_label`` for all container-as-item nodes in
        one additional join query (no N+1).
        Returns a list of root-level ``LocationTreeNode`` objects.
        """
        all_locations = self._repo.list_all()

        # Resolve container asset labels in one join query.
        container_instance_ids = [
            loc.item_instance_id for loc in all_locations if loc.item_instance_id is not None
        ]
        label_map = self._build_container_label_map(container_instance_ids)

        return _build_tree(all_locations, label_map)


# ---------------------------------------------------------------------------
# Module-level helper (no DB access)
# ---------------------------------------------------------------------------


def _build_tree(
    locations: list[Location],
    label_map: dict[int, str] | None = None,
) -> list[LocationTreeNode]:
    """Nest a flat list of Location rows into a recursive tree structure.

    Algorithm: two-pass Python — O(n).

    1. Build a dict from ``id → LocationTreeNode`` (children=[]).
    2. Iterate again: each node with a ``parent_id`` appends itself to the
       parent's ``children`` list.  Nodes with ``parent_id = NULL`` are
       collected as root nodes.

    Ordering within each level is by ``id`` (ascending), preserving insertion
    order from the DB query (which orders by ``id``).

    Parameters
    ----------
    locations:
        Flat list of all Location rows.
    label_map:
        Optional mapping of ``item_instance_id → container_asset_label``
        (resolved by the service layer via a single join query).  When
        provided, each node's ``container_asset_label`` is set accordingly.
    """
    _label_map = label_map or {}
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
            item_instance_id=loc.item_instance_id,
            container_asset_label=(
                _label_map.get(loc.item_instance_id) if loc.item_instance_id is not None else None
            ),
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
