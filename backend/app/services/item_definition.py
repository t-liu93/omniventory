"""Service layer for ItemDefinition CRUD.

Business rules handled here:
- **Default-kind resolution**: when ``kind_id`` is omitted on create, the
  service looks up the seeded ``durable`` kind via ``ItemKindRepository`` and
  uses its id.  This keeps the FK real and avoids hard-coding a magic integer
  in the route.
- **Invalid ``kind_id`` rejection**: if the caller supplies a ``kind_id`` that
  does not exist in ``item_kinds``, the service raises HTTP 422 (unprocessable
  entity) — consistent with the validation error contract (M1.md §4.2).
- **FK existence checks**: optional ``category_id`` and
  ``default_location_id`` are validated to exist when provided.
- **``stock_tracking_mode`` validation** (M2): validated app-layer against
  ``STOCK_TRACKING_MODES``; raises 422 with ``validation.unsupported_tracking_mode``
  on unknown values (roadmap §2.11 — no DB CHECK).

All DB access goes through the repositories; no raw queries in this layer.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.stock import STOCK_TRACKING_MODES
from app.models.item_definition import ItemDefinition
from app.models.item_kind import ItemKind
from app.repositories.category import CategoryRepository
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.item_kind import ItemKindRepository
from app.repositories.location import LocationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate

_DURABLE_CODE = "durable"


class ItemDefinitionService:
    """Business-logic facade for ItemDefinition operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ItemDefinitionRepository(db)
        self._kind_repo = ItemKindRepository(db)
        self._cat_repo = CategoryRepository(db)
        self._loc_repo = LocationRepository(db)
        self._inst_repo = StockInstanceRepository(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, definition_id: int) -> ItemDefinition:
        """Return an ItemDefinition or raise HTTP 404."""
        defn = self._repo.get(definition_id)
        if defn is None:
            raise AppError(
                ErrorCode.ITEM_DEFINITION_NOT_FOUND,
                status_code=404,
                params={"id": definition_id},
                message=f"Item definition {definition_id} not found.",
            )
        return defn

    def _resolve_kind_id(self, kind_id: int | None) -> int:
        """Resolve the kind_id to use.

        - If ``kind_id`` is None, look up the seeded ``durable`` kind and
          return its id (default-kind resolution, M1.md §2 / §9 Step 3).
        - If ``kind_id`` is provided, verify it exists in ``item_kinds``;
          raise AppError(item_kind.not_found) if not.
        """
        if kind_id is None:
            durable = self._kind_repo.get_by_code(_DURABLE_CODE)
            if durable is None:
                # Should never happen after migration 0006 seeding.
                raise AppError(
                    ErrorCode.INTERNAL_ERROR,
                    status_code=500,
                    message=(
                        "Default kind 'durable' is missing from item_kinds. "
                        "Ensure migration 0006 has been applied."
                    ),
                )
            return durable.id

        # Validate that the supplied kind_id exists.
        kind_row = self._db.get(ItemKind, kind_id)
        if kind_row is None:
            raise AppError(
                ErrorCode.ITEM_KIND_NOT_FOUND,
                status_code=422,
                params={"id": kind_id},
                message=f"Item kind with id={kind_id} does not exist.",
            )
        return kind_id

    def _assert_category_exists(self, category_id: int) -> None:
        """Raise 404 if the category does not exist."""
        if self._cat_repo.get(category_id) is None:
            raise AppError(
                ErrorCode.CATEGORY_NOT_FOUND,
                status_code=404,
                params={"id": category_id},
                message=f"Category {category_id} not found.",
            )

    def _assert_location_exists(self, location_id: int) -> None:
        """Raise 404 if the location does not exist."""
        if self._loc_repo.get(location_id) is None:
            raise AppError(
                ErrorCode.LOCATION_NOT_FOUND,
                status_code=404,
                params={"id": location_id},
                message=f"Location {location_id} not found.",
            )

    def _validate_tracking_mode(self, mode: str) -> None:
        """Raise 422 if ``mode`` is not a supported stock-tracking mode (M2 §3.1 / §4.7).

        Validation is **app-layer only** — no DB CHECK constraint (roadmap §2.11).
        """
        if mode not in STOCK_TRACKING_MODES:
            raise AppError(
                ErrorCode.UNSUPPORTED_TRACKING_MODE,
                status_code=422,
                params={"value": mode, "supported": list(STOCK_TRACKING_MODES)},
                message=(
                    f"Unsupported stock_tracking_mode {mode!r}. "
                    f"Supported values: {list(STOCK_TRACKING_MODES)}."
                ),
            )

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: DefinitionCreate) -> ItemDefinition:
        """Create a new item definition.

        - Resolves ``kind_id`` (defaults to ``durable`` when omitted).
        - Validates ``category_id`` and ``default_location_id`` if provided.
        - Validates ``stock_tracking_mode`` against ``STOCK_TRACKING_MODES`` (M2).
        """
        resolved_kind_id = self._resolve_kind_id(data.kind_id)

        if data.category_id is not None:
            self._assert_category_exists(data.category_id)

        if data.default_location_id is not None:
            self._assert_location_exists(data.default_location_id)

        self._validate_tracking_mode(data.stock_tracking_mode)

        return self._repo.create(
            name=data.name,
            kind_id=resolved_kind_id,
            description=data.description,
            category_id=data.category_id,
            unit=data.unit,
            default_location_id=data.default_location_id,
            stock_tracking_mode=data.stock_tracking_mode,
            min_stock=data.min_stock,
        )

    def get(self, definition_id: int) -> ItemDefinition:
        """Return an item definition by PK, or raise 404."""
        return self._get_or_404(definition_id)

    def list_all(
        self,
        *,
        q: str | None = None,
        category_id: int | None = None,
    ) -> list[ItemDefinition]:
        """Return a filtered flat list of item definitions.

        When ``category_id`` is provided, results include definitions from that
        category **and all its descendants** (subtree filter).  The public
        signature is unchanged — callers still pass a single ``category_id``.
        """
        category_ids: list[int] | None = None
        if category_id is not None:
            descendants = self._cat_repo.get_descendants(category_id)
            category_ids = [category_id, *(c.id for c in descendants)]
        return self._repo.list_all(q=q, category_ids=category_ids)

    def update(self, definition_id: int, data: DefinitionUpdate) -> ItemDefinition:
        """Apply a partial update to an item definition.

        - Validates ``kind_id`` if changed.
        - Validates ``category_id`` and ``default_location_id`` if changed.
        - Validates ``stock_tracking_mode`` if provided (M2).
        - Rejects a tracking-mode change when the definition already has lots
          (M2 Step 4 — ``item_definition.tracking_mode_change_conflict``, 409).
        """
        defn = self._get_or_404(definition_id)

        # Resolve kind_id if provided.
        new_kind_id: int | None = None
        if data.kind_id is not None:
            new_kind_id = self._resolve_kind_id(data.kind_id)

        category_id_changed = "category_id" in data.model_fields_set
        location_id_changed = "default_location_id" in data.model_fields_set
        min_stock_changed = "min_stock" in data.model_fields_set

        if category_id_changed and data.category_id is not None:
            self._assert_category_exists(data.category_id)

        if location_id_changed and data.default_location_id is not None:
            self._assert_location_exists(data.default_location_id)

        if data.stock_tracking_mode is not None:
            self._validate_tracking_mode(data.stock_tracking_mode)

            # Mode-change guard (M2 Step 4): if the mode is actually changing
            # and the definition already has lots, reject the change.
            if (
                data.stock_tracking_mode != defn.stock_tracking_mode
                and self._inst_repo.has_instances_for_definition(definition_id)
            ):
                raise AppError(
                    ErrorCode.ITEM_DEFINITION_TRACKING_MODE_CHANGE_CONFLICT,
                    status_code=409,
                    params={
                        "id": definition_id,
                        "from": defn.stock_tracking_mode,
                        "to": data.stock_tracking_mode,
                    },
                    message=(
                        f"Cannot change stock_tracking_mode from '{defn.stock_tracking_mode}' "
                        f"to '{data.stock_tracking_mode}' for definition {definition_id} "
                        "because it already has stock instances. "
                        "Delete or reassign all instances first."
                    ),
                )

        return self._repo.update(
            defn,
            name=data.name,
            description=data.description,
            kind_id=new_kind_id,
            set_category_id=category_id_changed,
            category_id=data.category_id,
            unit=data.unit,
            set_default_location_id=location_id_changed,
            default_location_id=data.default_location_id,
            stock_tracking_mode=data.stock_tracking_mode,
            set_min_stock=min_stock_changed,
            min_stock=data.min_stock,
        )

    def delete(self, definition_id: int) -> None:
        """Delete an item definition.

        Blocked (HTTP 409) if any stock instance still references this
        definition — symmetric to the location delete-guard (M1.md §2
        "Tree delete semantics").  An instance must always have a definition;
        orphaning one is forbidden.
        """
        defn = self._get_or_404(definition_id)
        if self._inst_repo.has_instances_for_definition(definition_id):
            raise AppError(
                ErrorCode.ITEM_DEFINITION_HAS_INSTANCES,
                status_code=409,
                params={"id": definition_id},
                message=(
                    f"Item definition '{defn.name}' (id={definition_id}) cannot be "
                    "deleted because it still has stock instances referencing it. "
                    "Delete or reassign the instances first."
                ),
            )
        self._repo.delete(defn)
