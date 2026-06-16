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

All DB access goes through the repositories; no raw queries in this layer.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.item_definition import ItemDefinition
from app.models.item_kind import ItemKind
from app.repositories.category import CategoryRepository
from app.repositories.item_definition import ItemDefinitionRepository
from app.repositories.item_kind import ItemKindRepository
from app.repositories.location import LocationRepository
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

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, definition_id: int) -> ItemDefinition:
        """Return an ItemDefinition or raise HTTP 404."""
        defn = self._repo.get(definition_id)
        if defn is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item definition {definition_id} not found.",
            )
        return defn

    def _resolve_kind_id(self, kind_id: int | None) -> int:
        """Resolve the kind_id to use.

        - If ``kind_id`` is None, look up the seeded ``durable`` kind and
          return its id (default-kind resolution, M1.md §2 / §9 Step 3).
        - If ``kind_id`` is provided, verify it exists in ``item_kinds``;
          raise HTTP 422 if not.
        """
        if kind_id is None:
            durable = self._kind_repo.get_by_code(_DURABLE_CODE)
            if durable is None:
                # Should never happen after migration 0006 seeding.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Default kind 'durable' is missing from item_kinds. "
                        "Ensure migration 0006 has been applied."
                    ),
                )
            return durable.id

        # Validate that the supplied kind_id exists.
        kind_row = self._db.get(ItemKind, kind_id)
        if kind_row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Item kind with id={kind_id} does not exist.",
            )
        return kind_id

    def _assert_category_exists(self, category_id: int) -> None:
        """Raise HTTP 404 if the category does not exist."""
        if self._cat_repo.get(category_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found.",
            )

    def _assert_location_exists(self, location_id: int) -> None:
        """Raise HTTP 404 if the location does not exist."""
        if self._loc_repo.get(location_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location {location_id} not found.",
            )

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def create(self, data: DefinitionCreate) -> ItemDefinition:
        """Create a new item definition.

        - Resolves ``kind_id`` (defaults to ``durable`` when omitted).
        - Validates ``category_id`` and ``default_location_id`` if provided.
        """
        resolved_kind_id = self._resolve_kind_id(data.kind_id)

        if data.category_id is not None:
            self._assert_category_exists(data.category_id)

        if data.default_location_id is not None:
            self._assert_location_exists(data.default_location_id)

        return self._repo.create(
            name=data.name,
            kind_id=resolved_kind_id,
            description=data.description,
            category_id=data.category_id,
            unit=data.unit,
            default_location_id=data.default_location_id,
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
        """Return a filtered flat list of item definitions."""
        return self._repo.list_all(q=q, category_id=category_id)

    def update(self, definition_id: int, data: DefinitionUpdate) -> ItemDefinition:
        """Apply a partial update to an item definition.

        - Validates ``kind_id`` if changed.
        - Validates ``category_id`` and ``default_location_id`` if changed.
        """
        defn = self._get_or_404(definition_id)

        # Resolve kind_id if provided.
        new_kind_id: int | None = None
        if data.kind_id is not None:
            new_kind_id = self._resolve_kind_id(data.kind_id)

        category_id_changed = "category_id" in data.model_fields_set
        location_id_changed = "default_location_id" in data.model_fields_set

        if category_id_changed and data.category_id is not None:
            self._assert_category_exists(data.category_id)

        if location_id_changed and data.default_location_id is not None:
            self._assert_location_exists(data.default_location_id)

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
        )

    def delete(self, definition_id: int) -> None:
        """Delete an item definition."""
        defn = self._get_or_404(definition_id)
        self._repo.delete(defn)
