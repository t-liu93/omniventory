"""MediaStorage service — the only component that touches the filesystem (M5 §4.2).

Responsibilities
----------------
- ``store(data, declared_type)``  Validate → downscale → sha256 → write-if-absent
  atomically → create/return MediaFile row.
- ``path_for(media_file)``        Derive the on-disk path from the hash.
- ``delete_physical(media_file)`` Best-effort unlink (never raises; logs failure).

Key design decisions (§4.2, §2)
---------------------------------
- **Images** are validated with Pillow (``Image.open`` + ``verify()``).  Corrupt or
  non-image bytes → ``attachment.unsupported_type``.  Over-large images are
  downscaled (edge cap = ``max_image_edge``, default 2048 px) with EXIF orientation
  preserved.  ``width`` / ``height`` come from the (possibly re-encoded) image.
- **Non-images** are allowed if the MIME type is in ``ALLOWED_NON_IMAGE_TYPES``
  (PDF, plain text, common docs).  SVG is **explicitly rejected** (active content).
- **Content-type** persisted is our validated value, never the raw client header.
- **Atomic write**: temp file + ``os.rename`` within the same directory.
- **De-dup**: if a ``media_files`` row with that sha256 already exists, return it
  (no second file write).
- **Physical I/O** is intentionally **outside** DB transactions.  The caller
  commits the DB, then calls ``delete_physical`` as a best-effort post-commit step.
  Failed unlinks are logged, not raised.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.media_file import MediaFile
from app.repositories.media_file import MediaFileRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum file size in bytes (25 MB).
MAX_BYTE_SIZE: int = 25 * 1024 * 1024

#: Maximum image edge (width or height) in pixels; over-large images are downscaled.
DEFAULT_MAX_IMAGE_EDGE: int = 2048

#: Non-image MIME types that are accepted as-is (no Pillow processing).
ALLOWED_NON_IMAGE_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

#: Image MIME types accepted for Pillow validation.
ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
    }
)

#: Canonical content-type for Pillow format → MIME mapping.
_PILLOW_FORMAT_TO_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


class MediaStorage:
    """Filesystem + DB facade for validated, content-addressed media files."""

    def __init__(
        self, db: Session, *, media_dir: Path, max_image_edge: int = DEFAULT_MAX_IMAGE_EDGE
    ) -> None:
        self._db = db
        self._media_dir = media_dir
        self._max_image_edge = max_image_edge
        self._repo = MediaFileRepository(db)

    # ---------------------------------------------------------------------- #
    # Public API                                                               #
    # ---------------------------------------------------------------------- #

    def store(self, data: bytes, declared_type: str) -> MediaFile:
        """Validate, (optionally) downscale, hash, and store media bytes.

        Parameters
        ----------
        data:
            Raw uploaded bytes.
        declared_type:
            The content-type string from the multipart upload header.  Used as
            a hint to route to image vs non-image handling, but the **persisted**
            content_type is always our validated value.

        Returns
        -------
        The (possibly pre-existing) MediaFile row for these bytes.

        Raises
        ------
        AppError(attachment.file_too_large, 413)
            When ``len(data) > MAX_BYTE_SIZE``.
        AppError(attachment.unsupported_type, 415)
            For SVG, corrupt images, or types not in the allow-lists.
        """
        # 1. Size cap
        if len(data) > MAX_BYTE_SIZE:
            raise AppError(
                ErrorCode.ATTACHMENT_FILE_TOO_LARGE,
                status_code=413,
                params={"max_bytes": MAX_BYTE_SIZE, "received_bytes": len(data)},
                message=(f"Uploaded file exceeds the {MAX_BYTE_SIZE // (1024 * 1024)} MB limit."),
            )

        # 2. SVG is always rejected (active content — §4.2 + §1 non-goals note).
        if "svg" in declared_type.lower():
            raise AppError(
                ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE,
                status_code=415,
                params={"content_type": declared_type},
                message="SVG files are not accepted (active content).",
            )

        # 3. Route to image or non-image handling.
        normalized_type = declared_type.lower().split(";")[0].strip()

        if normalized_type.startswith("image/"):
            return self._store_image(data, declared_type)
        else:
            return self._store_non_image(data, declared_type)

    def path_for(self, media_file: MediaFile) -> Path:
        """Return the on-disk path for a MediaFile row.

        Path: ``<media_dir>/<sha256[:2]>/<sha256>``  (sharded, no extension).
        """
        sha = media_file.sha256
        return self._media_dir / sha[:2] / sha

    def delete_physical(self, media_file: MediaFile) -> None:
        """Best-effort unlink of the on-disk file.

        A failed unlink is logged as a warning but never raised — a stray file is
        harmless and sweepable; the DB row is already gone.
        """
        path = self.path_for(media_file)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Failed to unlink media file %s (sha256=%s); ignoring.",
                path,
                media_file.sha256,
                exc_info=True,
            )

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _store_image(self, data: bytes, declared_type: str) -> MediaFile:
        """Validate, downscale if needed, and store image bytes."""
        from PIL import Image, ImageOps

        # 3a. Open + verify with Pillow.
        # .verify() detects truncated/corrupt files but consumes the image object.
        # We must re-open after verify to access pixel data.
        try:
            _tmp = Image.open(io.BytesIO(data))
            _tmp.verify()
        except Exception as exc:
            raise AppError(
                ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE,
                status_code=415,
                params={"content_type": declared_type},
                message="Uploaded file is not a valid or supported image.",
            ) from exc

        # Re-open after verify (verify() closes / corrupts the internal state).
        # Capture the format BEFORE exif_transpose because that call may return
        # a copy whose .format attribute is None.
        try:
            img_opened = Image.open(io.BytesIO(data))
            pillow_format = img_opened.format or ""
            img: Image.Image = ImageOps.exif_transpose(img_opened)
        except Exception as exc:
            raise AppError(
                ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE,
                status_code=415,
                params={"content_type": declared_type},
                message="Failed to decode image after verification.",
            ) from exc

        # Determine our validated content_type from Pillow's detected format.
        validated_type = _PILLOW_FORMAT_TO_MIME.get(pillow_format.upper())
        if validated_type is None:
            # Pillow parsed it but we don't have a canonical MIME for this format.
            raise AppError(
                ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE,
                status_code=415,
                params={"content_type": declared_type, "pillow_format": pillow_format},
                message=f"Image format {pillow_format!r} is not supported.",
            )

        width, height = img.size

        # 3b. Downscale if either edge exceeds the cap.
        needs_reencode = max(width, height) > self._max_image_edge
        if needs_reencode:
            scale = self._max_image_edge / max(width, height)
            new_w = max(1, int(width * scale))
            new_h = max(1, int(height * scale))
            resized: Image.Image = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            width, height = resized.size
            # Re-encode to bytes.
            buf = io.BytesIO()
            save_format = pillow_format if pillow_format else "JPEG"
            # Convert RGBA → RGB for JPEG (JPEG doesn't support alpha).
            if save_format.upper() == "JPEG" and resized.mode in ("RGBA", "P"):
                resized = resized.convert("RGB")
            resized.save(buf, format=save_format)
            data = buf.getvalue()
            # byte_size updated after re-encode.

        byte_size = len(data)
        sha256 = _sha256_hex(data)

        return self._upsert(
            data=data,
            sha256=sha256,
            content_type=validated_type,
            byte_size=byte_size,
            width=width,
            height=height,
        )

    def _store_non_image(self, data: bytes, declared_type: str) -> MediaFile:
        """Validate and store non-image bytes (PDF, text, etc.)."""
        normalized_type = declared_type.lower().split(";")[0].strip()

        if normalized_type not in ALLOWED_NON_IMAGE_TYPES:
            raise AppError(
                ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE,
                status_code=415,
                params={"content_type": declared_type},
                message=(
                    f"Content type {declared_type!r} is not accepted. "
                    "Supported non-image types: PDF, plain text, common office documents."
                ),
            )

        # Use the normalized (stripped) type as the validated content_type.
        sha256 = _sha256_hex(data)
        byte_size = len(data)

        return self._upsert(
            data=data,
            sha256=sha256,
            content_type=normalized_type,
            byte_size=byte_size,
            width=None,
            height=None,
        )

    def _upsert(
        self,
        *,
        data: bytes,
        sha256: str,
        content_type: str,
        byte_size: int,
        width: int | None,
        height: int | None,
    ) -> MediaFile:
        """De-dup: return existing MediaFile or create new row + write file."""
        existing = self._repo.get_by_hash(sha256)
        if existing is not None:
            return existing

        # Write the file atomically (temp + rename).
        self._write_file(data, sha256)

        # Insert the DB row.
        return self._repo.create(
            sha256=sha256,
            content_type=content_type,
            byte_size=byte_size,
            width=width,
            height=height,
        )

    def _write_file(self, data: bytes, sha256: str) -> None:
        """Write data to the sharded on-disk path atomically."""
        shard_dir = self._media_dir / sha256[:2]
        shard_dir.mkdir(parents=True, exist_ok=True)
        target = shard_dir / sha256

        if target.exists():
            # Already written (race with another upload of the same bytes).
            return

        # Write to a temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(dir=str(shard_dir))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.rename(tmp_path, str(target))
        except Exception:
            # Clean up the temp file on failure (best-effort).
            import contextlib

            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()
