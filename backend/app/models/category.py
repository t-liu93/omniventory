"""SQLAlchemy model for the Category self-referential tree.

A Category classifies item definitions (e.g. "Tools → Power tools").
The tree is arbitrary depth via the self-referential ``parent_id`` FK.

Design notes
------------
- ``parent_id = NULL`` denotes a root node.
- Cycle prevention is enforced in the **service layer** (``CategoryService``),
  not via a DB trigger — per roadmap §2.11 (logic in the app layer).
- The tree pattern mirrors the Location tree (M1.md §3.2 / §10 Step-2).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Category(Base):
    """A category node in the item-definition taxonomy.

    Columns
    -------
    id            Auto-increment surrogate PK.
    name          Human-readable label (e.g. "Power tools").
    description   Optional longer description.
    parent_id     FK → categories.id; NULL = root node.
    created_at    Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("categories.id", name="fk_categories_parent_id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Self-referential relationships.
    parent: Mapped["Category | None"] = relationship(
        "Category",
        back_populates="children",
        remote_side="Category.id",
        foreign_keys="[Category.parent_id]",
    )
    children: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="parent",
        foreign_keys="[Category.parent_id]",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"Category(id={self.id!r}, name={self.name!r}, parent_id={self.parent_id!r})"
