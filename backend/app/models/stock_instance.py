"""SQLAlchemy model for the StockInstance table.

A ``stock_instance`` is the "this specific lot / unit" record — it captures a
specific physical unit or bulk lot of an item (quantity, location, serial,
warranty, purchase value).

Design notes
------------
- ``definition_id`` is a non-nullable FK → ``item_definitions.id`` (required).
- ``location_id`` is a nullable FK → ``locations.id`` (where it physically sits).
- ``quantity`` is ``Numeric(18,6)`` — never float (roadmap §2.9).
- ``purchase_price`` is ``Numeric(18,2)`` — never float (roadmap §2.9).
- ``serial`` is nullable; when set, ``quantity`` MUST be 1 (see DB CHECK below
  and ``StockInstanceService`` for the service-layer enforcement).
- DB constraints (permitted per roadmap §2.11):
    - ``CHECK (serial IS NULL OR quantity = 1)`` — the serial constraint.
    - Partial unique index on ``(definition_id, serial) WHERE serial IS NOT NULL``
      — the same physical serial cannot be registered twice for the same product.
      Two NULLs are allowed (bulk lots without serials — roadmap §3.5).
- ``item_instance_id`` on ``locations`` (the container-as-item bridge) is added
  in migration 0008 via batch mode after this table is created (§3.6 of M1.md).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StockInstance(Base):
    """A specific lot or unit of an inventory item.

    Columns
    -------
    id               Auto-increment surrogate PK.
    definition_id    FK → item_definitions.id; NOT NULL.
    location_id      FK → locations.id; nullable (where it physically sits).
    quantity         Numeric(18,6) default 1; directly stored in M1.
    serial           Optional serial number; triggers qty=1 constraint.
    model_number     Durable identity field.
    manufacturer     Durable identity field.
    warranty_expires Date of warranty expiry; stored only (reminders in M4).
    warranty_details Optional notes about the warranty.
    purchase_price   Numeric(18,2); currency from household.currency.
    purchase_date    Date of purchase.
    purchase_source  Where it was bought.
    created_at       Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "stock_instances"

    __table_args__ = (
        # DB-level serial constraint: when serial is set, quantity must be 1.
        # The service layer also enforces this (422 before hitting the DB).
        CheckConstraint(
            "serial IS NULL OR quantity = 1",
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
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        server_default="1",
    )
    serial: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    model_number: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    warranty_expires: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    warranty_details: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    purchase_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, default=None
    )
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    purchase_source: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"StockInstance(id={self.id!r}, definition_id={self.definition_id!r}, "
            f"serial={self.serial!r}, quantity={self.quantity!r})"
        )
