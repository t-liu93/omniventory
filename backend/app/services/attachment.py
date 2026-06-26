"""AttachmentService — polymorphic attachment lifecycle (M5 §4.1).

Responsibilities
----------------
- ``upload(model_type, model_id, file)``   Resolve owner → MediaStorage.store →
  create reference row.
- ``list_for(model_type, model_id)``        List an owner's attachments.
- ``update(id, title, sort_order)``         Patch title / sort_order.
- ``delete(attachment_id)``                 Delete reference; if ref-count now 0,
  delete MediaFile row.  Returns paths to unlink post-commit (best-effort).
- ``delete_for_owner(model_type, id)``      Cascade helper: remove all attachments
  for an owner and return paths of now-unreferenced files to unlink post-commit.
- ``unlink_post_commit(paths)``             Best-effort unlink; logs on failure,
  never raises.

Design (§4.2)
-------------
Physical file deletion happens OUTSIDE the DB transaction (best-effort).  A
failed unlink is logged, never raised.

The pattern enforced here:
  1. Service deletes DB rows (reference + possibly media_files) within the
     open transaction.
  2. Service returns the list of on-disk paths that should be removed.
  3. Caller commits the transaction (``db.commit()``).
  4. Caller calls ``unlink_post_commit(paths)`` to do the physical deletion.

This ensures that a commit rollback can never leave a dangling DB reference to
a file that has already been physically deleted ("never orphan").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.attachment import Attachment
from app.models.media_file import MediaFile
from app.repositories.attachment import AttachmentRepository
from app.repositories.media_file import MediaFileRepository
from app.services.media_storage import MAX_BYTE_SIZE, MediaStorage
from app.services.owners import OWNER_TYPES, resolve_owner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def unlink_post_commit(paths: list[Path]) -> None:
    """Best-effort post-commit physical unlink of media files.

    A failed unlink is logged as a warning but never raised — a stray file is
    harmless and sweepable.  Must be called AFTER ``db.commit()`` succeeds.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Failed to unlink media file %s; ignoring.",
                path,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AttachmentService:
    """Business-logic facade for Attachment operations."""

    def __init__(self, db: Session, *, media_dir: Path) -> None:
        self._db = db
        self._repo = AttachmentRepository(db)
        self._mf_repo = MediaFileRepository(db)
        self._storage = MediaStorage(db, media_dir=media_dir)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, attachment_id: int) -> Attachment:
        """Return an Attachment or raise 404."""
        att = self._repo.get(attachment_id)
        if att is None:
            raise AppError(
                ErrorCode.ATTACHMENT_NOT_FOUND,
                status_code=404,
                params={"id": attachment_id},
                message=f"Attachment {attachment_id} not found.",
            )
        return att

    def _cleanup_if_last_reference(self, media_file_id: int) -> Path | None:
        """Delete the MediaFile DB row if no references remain; return path to unlink.

        Called after one or more Attachment rows have been flushed-deleted.
        Returns the on-disk path that should be unlinked post-commit, or None if the
        media file is still referenced (and must be kept).

        Physical unlink is NOT performed here — the caller must call
        ``unlink_post_commit([path])`` after ``db.commit()`` succeeds.
        """
        ref_count = self._mf_repo.count_references_for(media_file_id)
        if ref_count > 0:
            return None

        mf = self._db.get(MediaFile, media_file_id)
        if mf is None:
            return None

        path = self._storage.path_for(mf)
        self._mf_repo.delete(mf)
        # Return the path for post-commit unlink — do NOT unlink here.
        return path

    # ---------------------------------------------------------------------- #
    # CRUD                                                                     #
    # ---------------------------------------------------------------------- #

    def upload(
        self,
        model_type: str,
        model_id: int,
        file: UploadFile,
        *,
        title: str | None = None,
        uploaded_by: int | None = None,
    ) -> Attachment:
        """Validate, store, and create an attachment reference.

        Parameters
        ----------
        model_type:
            One of ``OWNER_TYPES``.  Bad value → ``validation.invalid_input``.
        model_id:
            The owner's PK.  Missing → owner's not-found error.
        file:
            The uploaded UploadFile from the multipart request.
        title:
            Optional caption.
        uploaded_by:
            The uploader's user_id (nullable).

        Returns
        -------
        The newly created Attachment row.

        Raises
        ------
        AppError(attachment.file_too_large, 413)
            As soon as the bounded read exceeds the 25 MB limit — the full
            payload is never buffered before the check.
        """
        # 1. Validate owner type + existence.
        if model_type not in OWNER_TYPES:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                params={"model_type": model_type, "allowed": sorted(OWNER_TYPES)},
                message=(
                    f"Invalid model_type {model_type!r}. Allowed values: {sorted(OWNER_TYPES)}."
                ),
            )
        resolve_owner(self._db, model_type, model_id)

        # 2. Read bytes from the upload — bounded read to avoid buffering the
        #    entire payload before the size check.
        data = _read_bounded(file.file, MAX_BYTE_SIZE)
        declared_type = file.content_type or "application/octet-stream"
        original_filename = file.filename or None

        # 3. Validate, (optionally) downscale, hash, and store.
        media_file = self._storage.store(data, declared_type)

        # 4. Create the attachment reference row.
        return self._repo.create(
            media_file_id=media_file.id,
            model_type=model_type,
            model_id=model_id,
            original_filename=original_filename,
            title=title,
            sort_order=0,
            uploaded_by=uploaded_by,
        )

    def list_for(self, model_type: str, model_id: int) -> list[Attachment]:
        """Return all attachments for a (model_type, model_id) owner.

        Does NOT validate the owner — callers that need existence validation
        should call resolve_owner first.  This method is intentionally lenient
        so that it can be called in cascade helpers after the owner is gone.
        """
        return self._repo.list_for_owner(model_type, model_id)

    def update(
        self,
        attachment_id: int,
        *,
        title: str | None = None,
        set_title: bool = False,
        sort_order: int | None = None,
    ) -> Attachment:
        """Patch title and/or sort_order on an existing attachment."""
        att = self._get_or_404(attachment_id)
        return self._repo.update(
            att,
            title=title,
            set_title=set_title,
            sort_order=sort_order,
        )

    def delete(self, attachment_id: int) -> list[Path]:
        """Delete an attachment reference; return paths to unlink post-commit.

        Sequence:
        1. Delete the attachment row (flush, within the open transaction).
        2. Check ref-count for the media_file_id.
        3. If ref-count == 0: delete the media_files row (flush); add its path
           to the returned list.
        4. Caller commits the transaction, then calls unlink_post_commit(paths).

        Returns
        -------
        A list of on-disk paths that should be unlinked after ``db.commit()``.
        Empty if the media file is still referenced by other attachments.
        """
        att = self._get_or_404(attachment_id)
        media_file_id = att.media_file_id

        # Delete the reference row.
        self._repo.delete(att)

        # Collect the path to unlink (if this was the last reference).
        path = self._cleanup_if_last_reference(media_file_id)
        return [path] if path is not None else []

    def delete_for_owner(self, model_type: str, model_id: int) -> list[Path]:
        """Cascade helper: delete all attachments for an owner.

        Called by entity delete services BEFORE removing the owner row.  Walks
        each attachment, removes the reference, and collects paths of any
        now-unreferenced MediaFile rows for post-commit unlink.

        Returns
        -------
        A list of on-disk paths to unlink after ``db.commit()``.
        """
        rows = self._repo.list_for_owner(model_type, model_id)
        if not rows:
            return []

        # Collect unique media_file_ids before deletion.
        media_file_ids: list[int] = list({r.media_file_id for r in rows})

        # Delete all attachment rows.
        for att in rows:
            self._repo.delete(att)

        # For each referenced media_file, collect the path if no references remain.
        paths: list[Path] = []
        for mf_id in media_file_ids:
            p = self._cleanup_if_last_reference(mf_id)
            if p is not None:
                paths.append(p)
        return paths


# ---------------------------------------------------------------------------
# Module-level private helpers
# ---------------------------------------------------------------------------


def _read_bounded(file_obj: IO[bytes], max_bytes: int) -> bytes:
    """Read from file_obj in 64 KiB chunks; raise file_too_large on overflow.

    Avoids buffering the full upload payload before the size check, so an
    over-large upload is rejected as soon as the limit is exceeded rather than
    after the entire body is read into memory.

    The raised ``AppError`` is handled by the route's exception handler and
    returned to the client as HTTP 413.
    """
    chunks: list[bytes] = []
    total = 0
    # UploadFile.file is a SpooledTemporaryFile; any read()-able object works.
    while True:
        chunk = file_obj.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(
                ErrorCode.ATTACHMENT_FILE_TOO_LARGE,
                status_code=413,
                params={"max_bytes": max_bytes},
                message=(f"Uploaded file exceeds the {max_bytes // (1024 * 1024)} MB limit."),
            )
        chunks.append(chunk)
    return b"".join(chunks)
