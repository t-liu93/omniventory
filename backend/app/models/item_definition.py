"""SQLAlchemy model for the ItemDefinition table.

An ``item_definition`` is the "what kind of thing" record — it captures a
product's identity (name, category, kind, unit, default location) without
tracking any specific physical unit or lot.  Physical units / lots are stored
in ``stock_instances`` (Step 4).

Design notes
------------
- ``kind_id`` is a real FK → ``item_kinds.id`` (NOT a string enum or CHECK).
  The application service resolves the ``durable`` kind when ``kind_id`` is
  omitted on create.
- ``stock_tracking_mode`` (M2) is validated app-layer against
  ``STOCK_TRACKING_MODES``; no DB CHECK constraint (roadmap §2.11).
- ``min_stock`` (M2) is the reorder-point threshold; meaningful only for
  ``exact`` mode.  NULL means no threshold.
- ``default_best_before_days`` (M3) is the default shelf life in days;
  auto-computes a lot's ``best_before_date`` on intake when none is given
  (M3 Step 2).  ``NULL`` means no default.  Validated ``≥ 0`` by Pydantic
  (no DB CHECK — roadmap §2.11).  Editing it is non-retroactive.
- ``default_location_id`` is a nullable FK → ``locations.id``; it is the
  *suggested* location for new instances of this definition.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.item_kind import ItemKind


class ItemDefinition(Base):
    """A definition of an inventory item type.

    Columns
    -------
    id                         Auto-increment surrogate PK.
    name                       Human-readable product name.
    description                Optional longer description.
    category_id                FK → categories.id; nullable.
    kind_id                    FK → item_kinds.id; NOT NULL (service defaults to durable).
    unit                       Free-text unit string (default ``pcs``).
    default_location_id        FK → locations.id; nullable; suggested location for instances.
    stock_tracking_mode        String(16); validated app-layer; default ``exact`` (M2).
    min_stock                  Numeric(18,6); nullable; low-stock threshold for ``exact`` mode (M2).
    default_best_before_days   Integer; nullable; default shelf life in days (M3). ``≥ 0``
                               (Pydantic-validated). Auto-computes best_before_date on lot
                               intake when none is provided. Editing is non-retroactive.
    created_at                 Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "item_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    category_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("categories.id", name="fk_item_definitions_category_id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    kind_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("item_kinds.id", name="fk_item_definitions_kind_id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="pcs")
    default_location_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "locations.id",
            name="fk_item_definitions_default_location_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )
    stock_tracking_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="exact",
    )
    min_stock: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
        default=None,
    )
    default_best_before_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship to ItemKind (eager-enough for response serialization).
    kind: Mapped[ItemKind] = relationship("ItemKind", lazy="select")

    def __repr__(self) -> str:
        return f"ItemDefinition(id={self.id!r}, name={self.name!r}, kind_id={self.kind_id!r})"
