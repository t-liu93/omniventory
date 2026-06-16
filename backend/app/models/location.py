"""SQLAlchemy model for the Location self-referential tree.

A Location represents a physical place in the household (room, drawer, box, etc.)
and can be nested at arbitrary depth via the self-referential ``parent_id`` FK.

Design notes
------------
- ``parent_id = NULL`` denotes a root node (top-level location).
- Cycle prevention is enforced in the **service layer** (``LocationService``),
  not via a DB trigger — per roadmap §2.11 (logic in the app layer).
- ``item_instance_id`` is the container-as-item bridge (nullable, unique FK →
  ``stock_instances.id``).  It expresses "this location *is* that tracked
  durable asset" (e.g. the toolbox location IS the tracked toolbox instance).
  Added in Step 4 / migration 0008, after ``stock_instances`` is created
  (§3.6 of M1.md).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Location(Base):
    """A physical location in the household.

    Columns
    -------
    id                Auto-increment surrogate PK.
    name              Human-readable label (e.g. "Garage", "Top drawer").
    description       Optional longer description.
    parent_id         FK → locations.id; NULL = root node.
    item_instance_id  FK → stock_instances.id; nullable, unique.
                      The container-as-item bridge: when set, this location
                      physically *is* the tracked durable instance.
    created_at        Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("locations.id", name="fk_locations_parent_id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    item_instance_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "stock_instances.id",
            name="fk_locations_item_instance_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        unique=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Self-referential relationships.
    parent: Mapped[Location | None] = relationship(
        "Location",
        back_populates="children",
        remote_side="Location.id",
        foreign_keys="[Location.parent_id]",
    )
    children: Mapped[list[Location]] = relationship(
        "Location",
        back_populates="parent",
        foreign_keys="[Location.parent_id]",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"Location(id={self.id!r}, name={self.name!r}, "
            f"parent_id={self.parent_id!r}, item_instance_id={self.item_instance_id!r})"
        )
