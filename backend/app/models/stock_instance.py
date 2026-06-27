"""SQLAlchemy model for the StockInstance table.

A ``stock_instance`` is the "this specific lot / unit" record — it captures a
specific physical unit or bulk lot of an item (quantity, location, serial,
warranty, purchase value, stock level).

Design notes
------------
- ``definition_id`` is a non-nullable FK → ``item_definitions.id`` (required).
- ``location_id`` is a nullable FK → ``locations.id`` (where it physically sits).
- ``quantity`` is ``Numeric(18,6)`` **nullable** (M2 Step 3) — never float
  (roadmap §2.9).  ``exact``-mode lots carry a ledger-derived Decimal; ``level``
  and ``none`` mode lots carry NULL.
- ``stock_level`` is nullable String(16) — for ``level``-mode lots only
  (``high`` / ``medium`` / ``low``); validated app-layer against STOCK_LEVELS.
- ``received_at`` is nullable DateTime(tz) with server_default=now() — the FIFO
  ordering key and physical-receipt timestamp.  Distinct from ``created_at`` so
  back-dated intake orders correctly (M2 §2).
- ``purchase_price`` is ``Numeric(18,2)`` — never float (roadmap §2.9).
- ``serial`` is nullable; when set and the lot is ``exact``-mode, ``quantity``
  MUST be 1 (see rewritten DB CHECK below and service-layer enforcement).
- DB constraints (permitted per roadmap §2.11):
    - ``CHECK (serial IS NULL OR quantity IS NULL OR quantity = 1)`` — the
      rewritten serial constraint (M2 §3.2 / §10 Step 3).  Allows NULL quantity
      for non-exact lots while still blocking serial+qty>1.
    - Partial unique index on ``(definition_id, serial) WHERE serial IS NOT NULL``
      — the same physical serial cannot be registered twice for the same product.
      Two NULLs are allowed (bulk lots without serials — roadmap §3.5).
- ``item_instance_id`` on ``locations`` (the container-as-item bridge) is added
  in migration 0008 via batch mode after this table is created (§3.6 of M1.md).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.item_definition import ItemDefinition

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StockInstance(Base):
    """A specific lot or unit of an inventory item.

    Columns
    -------
    id               Auto-increment surrogate PK.
    definition_id    FK → item_definitions.id; NOT NULL.
    location_id      FK → locations.id; nullable (where it physically sits).
    quantity         Numeric(18,6) nullable; ledger-derived for exact-mode lots,
                     NULL for level/none-mode lots.
    stock_level      String(16) nullable; 'high'/'medium'/'low' for level-mode lots.
    received_at      DateTime(tz) nullable, server_default=now(); FIFO key and
                     physical-receipt timestamp (backdatable on intake).
    serial           Optional serial number; triggers qty=1 constraint.
    model_number     Durable identity field.
    manufacturer     Durable identity field.
    warranty_expires Date of warranty expiry; stored only (reminders in M4).
    warranty_details Optional notes about the warranty.
    best_before_date Per-lot best-before date (M3); NULL = no expiry tracked.
                     Mode-independent (mirrors warranty_expires shape/lifecycle).
                     Set explicitly or auto-computed on create from the
                     definition's default_best_before_days; editable via PATCH.
    purchase_price   Numeric(18,2); currency from household.currency.
    purchase_date    Date of purchase.
    purchase_source  Where it was bought.
    custom_fields    Text; nullable; JSON object string holding a flat map
                     ``str → (str|int|float|bool|null)``. NULL = none.
                     (De)serialized + validated app-layer (M5 Step 4). No DB JSON
                     functions (roadmap §2.11).
    responsible_user_id
                     FK → users.id; nullable; ondelete=SET NULL. Per-lot override
                     of the definition's default responsible party (M6 Step 4).
                     NULL = inherit from definition → fallback to all active users.
    created_at       Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "stock_instances"

    __table_args__ = (
        # DB-level serial constraint (rewritten in migration 0012):
        # When serial is set, quantity must be 1 OR NULL.
        # NULL quantity is allowed for non-exact-mode lots (level/none).
        # The service layer also enforces this (422 before hitting the DB).
        CheckConstraint(
            "serial IS NULL OR quantity IS NULL OR quantity = 1",
            name="ck_stock_instances_serial_qty_1",
        ),
        # Partial unique index: same (definition_id, serial) pair is rejected;
        # NULL serials are excluded so multiple bulk lots can coexist.
        Index(
            "uq_stock_instances_definition_serial",
            "definition_id",
            "serial",
            unique=True,
            sqlite_where=text("serial IS NOT NULL"),
        ),
        # Non-unique index for responsible-party lookups (M6 Step 4).
        Index("ix_stock_instances_responsible_user_id", "responsible_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    definition_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "item_definitions.id",
            name="fk_stock_instances_definition_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "locations.id",
            name="fk_stock_instances_location_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )
    quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
        default=None,
    )
    stock_level: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
    )
    serial: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    model_number: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    warranty_expires: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    warranty_details: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    best_before_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    purchase_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, default=None
    )
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    purchase_source: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    custom_fields: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    responsible_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_stock_instances_responsible_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship to ItemDefinition — used by ExpiryService to read
    # definition.name without a separate round-trip (M3 Step 4 §4.4 / §12).
    # lazy="select" is the SQLAlchemy default; ExpiryService eager-loads it
    # via joinedload in list_expiring so the service can access .name safely.
    definition: Mapped[ItemDefinition] = relationship(
        "ItemDefinition",
        foreign_keys=[definition_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"StockInstance(id={self.id!r}, definition_id={self.definition_id!r}, "
            f"serial={self.serial!r}, quantity={self.quantity!r})"
        )
