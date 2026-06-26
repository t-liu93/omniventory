"""SQLAlchemy model for the Attachment table (M5 §3.2).

An ``attachment`` is one **reference** from a polymorphic owner entity to a
``media_files`` row.  The same ``media_files`` row can be referenced by multiple
attachment rows (de-dup / shared content).

Design notes
------------
- ``model_type`` + ``model_id`` identify the owner polymorphically (no hard FK on
  ``model_id`` — it can reference any of the allowed owner tables).  Allowed types
  are validated by the service layer via the ``OWNER_TYPES`` registry.
- ``media_file_id`` → ``media_files.id`` with ``ondelete=RESTRICT``: the DB
  prevents deleting a ``media_files`` row that still has references.  The service
  deletes the ``media_files`` row only after the last attachment reference is gone.
- ``uploaded_by`` → ``users.id`` with ``ondelete=SET NULL``: deleting a user
  nullifies the uploader field (the attachment itself remains).
- ``sort_order`` defaults to 0; callers can set it for ordering within an owner.
- ``original_filename`` is the browser-supplied filename for display / download
  (optional, display-only).
- ``title`` is an optional user caption.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.media_file import MediaFile


class Attachment(Base):
    """One polymorphic reference from an owner entity to a media_files row.

    Columns
    -------
    id                Auto-increment surrogate PK.
    media_file_id     FK → media_files.id (RESTRICT).  The referenced file.
    model_type        Owner type string: ``item_definition`` / ``stock_instance`` /
                      ``location`` (validated app-layer; no DB CHECK).
    model_id          Owner PK (no hard FK — polymorphic).
    original_filename Browser-supplied filename for display; optional.
    title             Optional user caption.
    sort_order        Ordering within an owner; default 0.
    uploaded_by       FK → users.id (SET NULL on user delete); nullable.
    created_at        Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "attachments"

    __table_args__ = (
        # List an owner's attachments.
        Index("ix_attachments_owner", "model_type", "model_id", unique=False),
        # Ref-count queries.
        Index("ix_attachments_media_file_id", "media_file_id", unique=False),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_file_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("media_files.id", name="fk_attachments_media_file_id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_attachments_uploaded_by", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship to MediaFile (eager-enough for response serialization).
    media_file: Mapped[MediaFile] = relationship("MediaFile", lazy="select")

    def __repr__(self) -> str:
        return (
            f"Attachment(id={self.id!r}, model_type={self.model_type!r}, "
            f"model_id={self.model_id!r}, media_file_id={self.media_file_id!r})"
        )
