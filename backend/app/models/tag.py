"""SQLAlchemy models for the Tag and TagLink tables (M5 §3.3).

``Tag`` is a flat, colour-able label with a globally unique name (case-insensitive
uniqueness enforced in the service layer; the DB constraint is on the raw name).

``TagLink`` is the polymorphic join from a tag to an owner entity identified by
``(model_type, model_id)``.  No hard FK on ``model_id`` — the owner is polymorphic
(item_definition, stock_instance, location).  Allowed types are validated by the
service layer via the ``OWNER_TYPES`` registry.

``tag_id`` → ``tags.id`` with ``ondelete=CASCADE``: deleting a tag drops all its
links automatically at the DB level.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tag(Base):
    """A flat, colour-able label with a globally unique name.

    Columns
    -------
    id          Auto-increment surrogate PK.
    name        Unique tag name (String(64)); case-insensitive uniqueness
                enforced in the service layer.
    color       Optional Mantine colour name or hex string (String(32)).
    created_at  Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "tags"

    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"Tag(id={self.id!r}, name={self.name!r}, color={self.color!r})"


class TagLink(Base):
    """One polymorphic association from a tag to an owner entity.

    Columns
    -------
    id          Auto-increment surrogate PK.
    tag_id      FK → tags.id (CASCADE on delete).  The referenced tag.
    model_type  Owner type string: ``item_definition`` / ``stock_instance`` /
                ``location`` (validated app-layer; no DB CHECK).
    model_id    Owner PK (no hard FK — polymorphic).
    created_at  Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "tag_links"

    __table_args__ = (
        # Prevent tagging the same owner twice with the same tag.
        UniqueConstraint("tag_id", "model_type", "model_id", name="uq_tag_links_tag_owner"),
        # Fast lookup of all tags for a given owner.
        Index("ix_tag_links_owner", "model_type", "model_id", unique=False),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tags.id", name="fk_tag_links_tag_id", ondelete="CASCADE"),
        nullable=False,
    )
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"TagLink(id={self.id!r}, tag_id={self.tag_id!r}, "
            f"model_type={self.model_type!r}, model_id={self.model_id!r})"
        )
