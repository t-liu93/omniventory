"""Repository for the MediaFile table.

Pure data access — no business rules here.  Business logic (validation, hashing,
file I/O) lives in ``app.services.media_storage``.

Public methods
--------------
get_by_hash(sha256)           Return a MediaFile by SHA-256 hash, or None.
create(sha256, ...)           Insert and flush a new MediaFile row.
delete(media_file)            Delete a MediaFile row.
count_references_for(id)      COUNT attachments that reference this media_file_id.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.attachment import Attachment
from app.models.media_file import MediaFile


class MediaFileRepository:
    """Data-access object for the media_files table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get_by_hash(self, sha256: str) -> MediaFile | None:
        """Return a MediaFile by SHA-256 hex digest, or None if not found."""
        stmt = select(MediaFile).where(MediaFile.sha256 == sha256)
        return self._db.scalars(stmt).first()

    def count_references_for(self, media_file_id: int) -> int:
        """Return the number of attachments that reference this media_file_id."""
        stmt = select(func.count()).where(Attachment.media_file_id == media_file_id)
        result = self._db.execute(stmt).scalar_one()
        return int(result)

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        sha256: str,
        content_type: str,
        byte_size: int,
        width: int | None = None,
        height: int | None = None,
    ) -> MediaFile:
        """Insert a new MediaFile row and flush to get its PK."""
        mf = MediaFile(
            sha256=sha256,
            content_type=content_type,
            byte_size=byte_size,
            width=width,
            height=height,
        )
        self._db.add(mf)
        self._db.flush()
        return mf

    def delete(self, media_file: MediaFile) -> None:
        """Delete a MediaFile row (caller must ensure no references remain)."""
        self._db.delete(media_file)
        self._db.flush()
