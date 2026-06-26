"""Repository for the Attachment table.

Pure data access — no business rules here.  Business logic (owner validation,
ref-count cleanup) lives in ``app.services.attachment``.

Public methods
--------------
create(media_file_id, ...)     Insert and flush a new Attachment row.
list_for_owner(type, id)       List attachments for a (model_type, model_id) owner.
get(id)                        Return an Attachment by PK, or None.
delete(attachment)             Delete an Attachment row.
list_by_media_file(id)         List all attachments referencing a media_file_id.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attachment import Attachment


class AttachmentRepository:
    """Data-access object for the attachments table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, attachment_id: int) -> Attachment | None:
        """Return an Attachment by PK, or None if not found."""
        return self._db.get(Attachment, attachment_id)

    def list_for_owner(self, model_type: str, model_id: int) -> list[Attachment]:
        """Return all attachments for a given (model_type, model_id) owner.

        Ordered by sort_order ascending, then id ascending (stable ordering).
        """
        stmt = (
            select(Attachment)
            .where(Attachment.model_type == model_type, Attachment.model_id == model_id)
            .order_by(Attachment.sort_order, Attachment.id)
        )
        return list(self._db.scalars(stmt).all())

    def list_by_media_file(self, media_file_id: int) -> list[Attachment]:
        """Return all attachments that reference a given media_file_id."""
        stmt = select(Attachment).where(Attachment.media_file_id == media_file_id)
        return list(self._db.scalars(stmt).all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(
        self,
        *,
        media_file_id: int,
        model_type: str,
        model_id: int,
        original_filename: str | None = None,
        title: str | None = None,
        sort_order: int = 0,
        uploaded_by: int | None = None,
    ) -> Attachment:
        """Insert a new Attachment row and flush to get its PK."""
        att = Attachment(
            media_file_id=media_file_id,
            model_type=model_type,
            model_id=model_id,
            original_filename=original_filename,
            title=title,
            sort_order=sort_order,
            uploaded_by=uploaded_by,
        )
        self._db.add(att)
        self._db.flush()
        return att

    def update(
        self,
        attachment: Attachment,
        *,
        title: str | None = None,
        set_title: bool = False,
        sort_order: int | None = None,
    ) -> Attachment:
        """Apply partial field updates to an Attachment."""
        if set_title:
            attachment.title = title
        if sort_order is not None:
            attachment.sort_order = sort_order
        self._db.flush()
        return attachment

    def delete(self, attachment: Attachment) -> None:
        """Delete an Attachment row."""
        self._db.delete(attachment)
        self._db.flush()

    def delete_for_owner(self, model_type: str, model_id: int) -> list[int]:
        """Delete all attachments for an owner; return the media_file_ids they referenced.

        The caller is responsible for checking ref-counts and cleaning up
        media_files rows + physical files for any now-unreferenced media.
        """
        rows = self.list_for_owner(model_type, model_id)
        media_file_ids = [r.media_file_id for r in rows]
        for row in rows:
            self._db.delete(row)
        self._db.flush()
        return media_file_ids
