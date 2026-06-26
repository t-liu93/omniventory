"""ExportService — streamed CSV / JSON data export (M5 Step 7).

Exports three entities:
  ``item_definitions`` — FK: category_id+name, default_location_id+name.
  ``stock_instances``  — FK: definition_id+name, location_id+name.
  ``locations``        — FK: parent_id+name.

Foreign keys are flattened to **id + resolved name** columns (e.g.
``category_id``, ``category_name``).  A NULL FK yields an empty name in CSV
and ``null`` in JSON.

``custom_fields`` is exported as its raw JSON string column (or empty/null
when NULL).  Tags are comma-joined into a single ``tags`` column.

``Decimal`` values are rendered via ``str()`` (locale-independent).
``date`` / ``datetime`` values are rendered via ``.isoformat()`` (ISO 8601).

CSV uses stdlib ``csv`` for correct quoting/escaping of commas, double-quotes,
and embedded newlines.  JSON is streamed as an array: ``[``, record chunks,
``]`` — never buffering the whole array in memory.

Design note — FK batch loading
-------------------------------
For each entity we issue one SELECT per FK table (categories, locations,
definitions) and one SELECT for tag_links + tags.  This avoids N+1 queries
across potentially many rows and is efficient for household-scale data.

All access is read-only through the repository layer.  No writes, no new
models, no migrations (§4.6, §4.1, roadmap §2.11).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode

# ---------------------------------------------------------------------------
# Allowed values (used for validation + documentation)
# ---------------------------------------------------------------------------

ALLOWED_ENTITIES: frozenset[str] = frozenset({"item_definitions", "stock_instances", "locations"})
ALLOWED_FORMATS: frozenset[str] = frozenset({"csv", "json"})

# ---------------------------------------------------------------------------
# Column definitions per entity (defines CSV header and JSON key order)
# ---------------------------------------------------------------------------

ITEM_DEFINITION_COLUMNS: list[str] = [
    "id",
    "name",
    "description",
    "kind_id",
    "unit",
    "category_id",
    "category_name",
    "default_location_id",
    "default_location_name",
    "stock_tracking_mode",
    "min_stock",
    "default_best_before_days",
    "reminder_lead_days",
    "custom_fields",
    "tags",
    "created_at",
]

STOCK_INSTANCE_COLUMNS: list[str] = [
    "id",
    "definition_id",
    "definition_name",
    "location_id",
    "location_name",
    "quantity",
    "stock_level",
    "received_at",
    "serial",
    "model_number",
    "manufacturer",
    "warranty_expires",
    "warranty_details",
    "best_before_date",
    "purchase_price",
    "purchase_date",
    "purchase_source",
    "custom_fields",
    "tags",
    "created_at",
]

LOCATION_COLUMNS: list[str] = [
    "id",
    "name",
    "description",
    "parent_id",
    "parent_name",
    "item_instance_id",
    "tags",
    "created_at",
]


# ---------------------------------------------------------------------------
# Value formatting helpers
# ---------------------------------------------------------------------------


def _fmt_csv(v: object) -> str:
    """Convert a DB value to a locale-independent string for CSV output.

    - ``None``     → ``""`` (empty cell)
    - ``Decimal``  → ``str(v)`` (e.g. ``"3.140000"``)
    - ``datetime`` → ISO-8601 string (check before ``date`` — datetime ⊆ date)
    - ``date``     → ISO-8601 date string
    - Everything else → ``str(v)``
    """
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):  # must come before date (datetime is a subclass)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _fmt_json(v: object) -> object:
    """Convert a DB value to a JSON-serialisable Python object.

    - ``None``     → ``None`` (serialised as JSON ``null``)
    - ``Decimal``  → ``str(v)`` (JSON string; stable, no float precision loss)
    - ``datetime`` → ISO-8601 string
    - ``date``     → ISO-8601 date string
    - Everything else → unchanged (int, str, bool all serialise natively)
    """
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _csv_encode_row(row: list[object]) -> str:
    """Encode one row via stdlib csv (handles commas / quotes / newlines)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_fmt_csv(cell) for cell in row])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tag-map helper
# ---------------------------------------------------------------------------


def _build_tag_map(db: Session, model_type: str) -> dict[int, list[str]]:
    """Return ``{model_id: [tag_name, …]}`` for all owners of *model_type*.

    Issues exactly two queries:
      1. SELECT all TagLinks for this model_type.
      2. SELECT Tags whose id is in the link set.

    Tags within each owner are ordered by ``tag_id`` (ascending, stable).
    """
    from app.models.tag import Tag, TagLink

    links = list(
        db.scalars(
            select(TagLink).where(TagLink.model_type == model_type).order_by(TagLink.tag_id)
        ).all()
    )
    if not links:
        return {}

    tag_ids = list({lnk.tag_id for lnk in links})
    tags = list(db.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all())
    tag_name_by_id: dict[int, str] = {t.id: t.name for t in tags}

    result: dict[int, list[str]] = {}
    for lnk in links:
        name = tag_name_by_id.get(lnk.tag_id)
        if name is not None:
            result.setdefault(lnk.model_id, []).append(name)
    return result


# ---------------------------------------------------------------------------
# ExportService
# ---------------------------------------------------------------------------


class ExportService:
    """Read-only service that yields streamed CSV / JSON rows for an entity.

    Instantiate with a SQLAlchemy session; call ``export(entity, format)``
    which validates inputs and returns a string ``Iterator`` ready to feed
    into a ``StreamingResponse``.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Public interface                                                         #
    # ---------------------------------------------------------------------- #

    def export(self, entity: str, fmt: str) -> Iterator[str]:
        """Return a streaming iterator of encoded text chunks.

        Parameters
        ----------
        entity:
            One of ``"item_definitions"``, ``"stock_instances"``,
            ``"locations"``.
        fmt:
            ``"csv"`` or ``"json"``.

        Raises
        ------
        AppError(validation.invalid_input, 422)
            When *entity* or *fmt* is not in the allowed sets.
        """
        if entity not in ALLOWED_ENTITIES:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                params={"entity": entity, "allowed": sorted(ALLOWED_ENTITIES)},
                message=(f"Unknown entity {entity!r}. Allowed values: {sorted(ALLOWED_ENTITIES)}."),
            )
        if fmt not in ALLOWED_FORMATS:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                params={"format": fmt, "allowed": sorted(ALLOWED_FORMATS)},
                message=(f"Unknown format {fmt!r}. Allowed values: {sorted(ALLOWED_FORMATS)}."),
            )

        records, columns = self._build_records(entity)

        if fmt == "csv":
            return self._iter_csv(columns, records)
        else:
            return self._iter_json(columns, records)

    # ---------------------------------------------------------------------- #
    # Record building (eager DB fetch; lazy encoding via the generators below) #
    # ---------------------------------------------------------------------- #

    def _build_records(self, entity: str) -> tuple[list[dict[str, object]], list[str]]:
        if entity == "item_definitions":
            return self._item_definition_records(), ITEM_DEFINITION_COLUMNS
        elif entity == "stock_instances":
            return self._stock_instance_records(), STOCK_INSTANCE_COLUMNS
        else:
            return self._location_records(), LOCATION_COLUMNS

    def _item_definition_records(self) -> list[dict[str, object]]:
        from app.models.category import Category
        from app.models.item_definition import ItemDefinition
        from app.models.location import Location

        db = self._db

        # Batch-load all FK targets.
        categories: dict[int, str] = {c.id: c.name for c in db.scalars(select(Category)).all()}
        locations: dict[int, str] = {loc.id: loc.name for loc in db.scalars(select(Location)).all()}
        tags_map = _build_tag_map(db, "item_definition")

        rows = list(db.scalars(select(ItemDefinition).order_by(ItemDefinition.id)).all())
        records: list[dict[str, object]] = []
        for row in rows:
            records.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "kind_id": row.kind_id,
                    "unit": row.unit,
                    "category_id": row.category_id,
                    "category_name": (
                        categories.get(row.category_id) if row.category_id is not None else None
                    ),
                    "default_location_id": row.default_location_id,
                    "default_location_name": (
                        locations.get(row.default_location_id)
                        if row.default_location_id is not None
                        else None
                    ),
                    "stock_tracking_mode": row.stock_tracking_mode,
                    "min_stock": row.min_stock,
                    "default_best_before_days": row.default_best_before_days,
                    "reminder_lead_days": row.reminder_lead_days,
                    "custom_fields": row.custom_fields,  # raw JSON string or None
                    "tags": ",".join(tags_map.get(row.id, [])),
                    "created_at": row.created_at,
                }
            )
        return records

    def _stock_instance_records(self) -> list[dict[str, object]]:
        from app.models.item_definition import ItemDefinition
        from app.models.location import Location
        from app.models.stock_instance import StockInstance

        db = self._db

        definitions: dict[int, str] = {
            d.id: d.name for d in db.scalars(select(ItemDefinition)).all()
        }
        locations: dict[int, str] = {loc.id: loc.name for loc in db.scalars(select(Location)).all()}
        tags_map = _build_tag_map(db, "stock_instance")

        rows = list(db.scalars(select(StockInstance).order_by(StockInstance.id)).all())
        records: list[dict[str, object]] = []
        for row in rows:
            records.append(
                {
                    "id": row.id,
                    "definition_id": row.definition_id,
                    "definition_name": definitions.get(row.definition_id),
                    "location_id": row.location_id,
                    "location_name": (
                        locations.get(row.location_id) if row.location_id is not None else None
                    ),
                    "quantity": row.quantity,
                    "stock_level": row.stock_level,
                    "received_at": row.received_at,
                    "serial": row.serial,
                    "model_number": row.model_number,
                    "manufacturer": row.manufacturer,
                    "warranty_expires": row.warranty_expires,
                    "warranty_details": row.warranty_details,
                    "best_before_date": row.best_before_date,
                    "purchase_price": row.purchase_price,
                    "purchase_date": row.purchase_date,
                    "purchase_source": row.purchase_source,
                    "custom_fields": row.custom_fields,
                    "tags": ",".join(tags_map.get(row.id, [])),
                    "created_at": row.created_at,
                }
            )
        return records

    def _location_records(self) -> list[dict[str, object]]:
        from app.models.location import Location

        db = self._db

        # Build name map for self-referential parent_name resolution.
        all_locs: dict[int, str] = {loc.id: loc.name for loc in db.scalars(select(Location)).all()}
        tags_map = _build_tag_map(db, "location")

        rows = list(db.scalars(select(Location).order_by(Location.id)).all())
        records: list[dict[str, object]] = []
        for row in rows:
            records.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "parent_id": row.parent_id,
                    "parent_name": (
                        all_locs.get(row.parent_id) if row.parent_id is not None else None
                    ),
                    "item_instance_id": row.item_instance_id,
                    "tags": ",".join(tags_map.get(row.id, [])),
                    "created_at": row.created_at,
                }
            )
        return records

    # ---------------------------------------------------------------------- #
    # Streaming encoders (generators — body executes lazily on iteration)     #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _iter_csv(columns: list[str], records: list[dict[str, object]]) -> Iterator[str]:
        """Yield CSV-encoded text: header row, then one data row per record."""
        yield _csv_encode_row(list(columns))
        for record in records:
            yield _csv_encode_row([record.get(col) for col in columns])

    @staticmethod
    def _iter_json(columns: list[str], records: list[dict[str, object]]) -> Iterator[str]:
        """Yield a JSON array incrementally: ``[``, object chunks, ``]``."""
        yield "["
        first = True
        for record in records:
            # Build an ordered dict preserving the column order.
            obj: dict[str, object] = {col: _fmt_json(record.get(col)) for col in columns}
            if not first:
                yield ","
            yield json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            first = False
        yield "]\n"
