"""LikeSearchProvider — case-insensitive LIKE search across all entity types (M5 Step 6).

Design (M5.md §4.5)
--------------------
``LikeSearchProvider.search(q, types, limit)`` runs, for each requested type,
a case-insensitive substring query using ``func.lower(...).contains(func.lower(q))``
— the established portable pattern (roadmap §2.11; no SQLite FTS, no raw SQL).

Per-type field coverage
-----------------------
- **item_definition**: ``name`` OR ``description`` OR ``custom_fields`` (text LIKE
  on the serialized JSON blob — custom-field *values* are findable).
- **stock_instance**: ``serial`` OR ``model_number`` OR ``manufacturer`` OR
  ``custom_fields`` text LIKE, **plus a join to ``barcodes.code``** via
  ``stock_instances.definition_id == barcodes.definition_id`` — an instance
  matches if its definition has a barcode whose code contains ``q``.
  ``DISTINCT`` de-duplicates instances whose definition has multiple matching
  codes.  ``joinedload(definition)`` gets the definition name in the same round-
  trip.
- **location**: ``name``.
- **category**: ``name``.
- **tag**: ``name``.

All queries fetch all matching rows (no DB-side LIMIT) so that the true total
count can be recorded in ``totals``; Python-side slicing to ``limit`` produces
the capped hit list.  For the expected dataset sizes of a personal inventory
this is perfectly efficient.

No raw SQL strings anywhere — everything is expressed through SQLAlchemy
``select`` / ``func`` / ``or_``.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.barcode import Barcode
from app.models.category import Category
from app.models.item_definition import ItemDefinition
from app.models.location import Location
from app.models.stock_instance import StockInstance
from app.models.tag import Tag
from app.services.search.provider import (
    CategoryHit,
    DefinitionHit,
    InstanceHit,
    LocationHit,
    SearchResults,
    TagHit,
)

# Canonical set of searchable type identifiers.
ALL_TYPES: frozenset[str] = frozenset(
    {"item_definition", "stock_instance", "location", "category", "tag"}
)


class LikeSearchProvider:
    """Case-insensitive LIKE search across entity tables (M5 §4.5).

    Implements the ``SearchProvider`` Protocol via duck typing — any class with
    a ``search(q, types, limit) -> SearchResults`` method satisfies the Protocol.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def search(self, q: str, types: set[str], limit: int) -> SearchResults:
        """Run per-type LIKE queries and return grouped, capped results.

        Parameters
        ----------
        q:
            Non-empty search string (caller is responsible for stripping whitespace
            and short-circuiting blank queries before calling this method).
        types:
            Set of type identifiers to search (subset of ``ALL_TYPES``).
        limit:
            Per-type cap: each result list will have at most this many items;
            the corresponding ``totals`` entry records the true match count.
        """
        results = SearchResults()

        if "item_definition" in types:
            hits, total = self._search_definitions(q, limit)
            results.item_definitions = hits
            results.totals["item_definitions"] = total

        if "stock_instance" in types:
            hits_i, total_i = self._search_instances(q, limit)
            results.stock_instances = hits_i
            results.totals["stock_instances"] = total_i

        if "location" in types:
            hits_l, total_l = self._search_locations(q, limit)
            results.locations = hits_l
            results.totals["locations"] = total_l

        if "category" in types:
            hits_c, total_c = self._search_categories(q, limit)
            results.categories = hits_c
            results.totals["categories"] = total_c

        if "tag" in types:
            hits_t, total_t = self._search_tags(q, limit)
            results.tags = hits_t
            results.totals["tags"] = total_t

        return results

    # ---------------------------------------------------------------------- #
    # Per-type query helpers                                                    #
    # ---------------------------------------------------------------------- #

    def _search_definitions(self, q: str, limit: int) -> tuple[list[DefinitionHit], int]:
        """Search item_definitions by name, description, or custom_fields text."""
        lower_q = func.lower(q)
        stmt = (
            select(ItemDefinition)
            .where(
                or_(
                    func.lower(ItemDefinition.name).contains(lower_q),
                    func.lower(ItemDefinition.description).contains(lower_q),
                    func.lower(ItemDefinition.custom_fields).contains(lower_q),
                )
            )
            .order_by(ItemDefinition.id)
        )
        rows = list(self._db.scalars(stmt).all())
        total = len(rows)
        hits = [DefinitionHit(id=r.id, name=r.name) for r in rows[:limit]]
        return hits, total

    def _search_instances(self, q: str, limit: int) -> tuple[list[InstanceHit], int]:
        """Search stock_instances by durable-identity fields and barcode code.

        Fields searched:
        - ``serial`` — direct string match.
        - ``model_number`` — direct string match.
        - ``manufacturer`` — direct string match.
        - ``custom_fields`` — text LIKE on the serialized JSON blob.
        - ``barcodes.code`` (joined via ``definition_id``) — an instance matches
          if its definition has at least one barcode whose code contains ``q``.

        ``DISTINCT`` prevents duplicate StockInstance rows when a definition has
        multiple barcodes that all match.  ``joinedload(definition)`` fetches the
        definition name in the same query round-trip.
        """
        lower_q = func.lower(q)
        stmt = (
            select(StockInstance)
            .outerjoin(Barcode, StockInstance.definition_id == Barcode.definition_id)
            .where(
                or_(
                    func.lower(StockInstance.serial).contains(lower_q),
                    func.lower(StockInstance.model_number).contains(lower_q),
                    func.lower(StockInstance.manufacturer).contains(lower_q),
                    func.lower(StockInstance.custom_fields).contains(lower_q),
                    func.lower(Barcode.code).contains(lower_q),
                )
            )
            .distinct()
            .order_by(StockInstance.id)
            .options(joinedload(StockInstance.definition))
        )
        # .unique() required when joinedload is combined with DISTINCT/outerjoin
        # to prevent duplicate ORM objects from the result set.
        rows = list(self._db.scalars(stmt).unique().all())
        total = len(rows)
        hits = [
            InstanceHit(
                id=r.id,
                definition_id=r.definition_id,
                definition_name=r.definition.name if r.definition else "",
                serial=r.serial,
                model_number=r.model_number,
                manufacturer=r.manufacturer,
            )
            for r in rows[:limit]
        ]
        return hits, total

    def _search_locations(self, q: str, limit: int) -> tuple[list[LocationHit], int]:
        """Search locations by name."""
        stmt = (
            select(Location)
            .where(func.lower(Location.name).contains(func.lower(q)))
            .order_by(Location.id)
        )
        rows = list(self._db.scalars(stmt).all())
        total = len(rows)
        hits = [LocationHit(id=r.id, name=r.name) for r in rows[:limit]]
        return hits, total

    def _search_categories(self, q: str, limit: int) -> tuple[list[CategoryHit], int]:
        """Search categories by name."""
        stmt = (
            select(Category)
            .where(func.lower(Category.name).contains(func.lower(q)))
            .order_by(Category.id)
        )
        rows = list(self._db.scalars(stmt).all())
        total = len(rows)
        hits = [CategoryHit(id=r.id, name=r.name) for r in rows[:limit]]
        return hits, total

    def _search_tags(self, q: str, limit: int) -> tuple[list[TagHit], int]:
        """Search tags by name."""
        stmt = select(Tag).where(func.lower(Tag.name).contains(func.lower(q))).order_by(Tag.id)
        rows = list(self._db.scalars(stmt).all())
        total = len(rows)
        hits = [TagHit(id=r.id, name=r.name, color=r.color) for r in rows[:limit]]
        return hits, total
