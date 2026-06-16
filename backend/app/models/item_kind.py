"""SQLAlchemy model for the ItemKind lookup table.

``item_kinds`` is a small reference table so that ``kind`` on an item
definition is a real FK — not a baked-in string enum — chosen in M1 so that
future references (M3 best-before, M5 tags) never need a breaking contract
change.

Design notes
------------
- Seeded with three system kinds (``durable`` / ``consumable`` / ``perishable``)
  via migration ``0006`` using the M0 ``INSERT OR IGNORE`` pattern.
- Exposed **read-only** over the API (``GET /kinds`` only — no write endpoints
  in M1).  Kinds CRUD and per-kind behaviour flags are deferred (M1.md §12).
- The ``code`` column is the stable machine key; ``name`` is the display label.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ItemKind(Base):
    """A kind classification for item definitions.

    Columns
    -------
    id            Auto-increment surrogate PK.
    code          Stable machine key (e.g. ``durable``); unique.
    name          Human-readable display label.
    is_system     True for the three seeded system kinds.
    created_at    Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "item_kinds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"ItemKind(id={self.id!r}, code={self.code!r}, name={self.name!r})"
