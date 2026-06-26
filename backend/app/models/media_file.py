"""SQLAlchemy model for the MediaFile table (M5 §3.1).

A ``media_file`` is the content-addressed physical-file registry row.  One row
per unique byte-content, keyed by sha256 hash.  The corresponding file lives at
``DATA_DIR/media/<sha256[:2]>/<sha256>`` (sharded, no extension).

Design notes
------------
- ``sha256`` is the content hash and the storage key; the ``uq_media_files_sha256``
  index enforces uniqueness (de-dup: identical bytes ⇒ one row + one file, N
  attachment references).
- ``content_type`` is the **validated** MIME type (never the raw client header);
  written once on store, never updated.
- ``width`` / ``height`` are NULL for non-images; set by Pillow on store.
- ``byte_size`` is the size on disk (post-downscale for images).
- No ORM relationship to ``attachments`` here — the ref-count is computed by
  querying the attachments table; a back-reference would load all attachment rows
  on every media_file access.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MediaFile(Base):
    """Content-addressed physical-file registry row.

    Columns
    -------
    id            Auto-increment surrogate PK.
    sha256        SHA-256 hex digest of the (possibly re-encoded) file bytes.
                  Unique (``uq_media_files_sha256``).
    content_type  Our validated MIME type string (never the raw client header).
    byte_size     Size on disk in bytes (post-downscale for images).
    width         Pixel width for images (Pillow); NULL for non-images.
    height        Pixel height for images; NULL for non-images.
    created_at    Row-creation timestamp (UTC, set by DB on insert).
    """

    __tablename__ = "media_files"

    __table_args__ = (Index("uq_media_files_sha256", "sha256", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"MediaFile(id={self.id!r}, sha256={self.sha256[:12]!r}..., "
            f"content_type={self.content_type!r}, byte_size={self.byte_size!r})"
        )
