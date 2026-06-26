"""TagService — flat tag lifecycle (M5 Step 2 §4.1).

Responsibilities
----------------
- Tag CRUD with case-insensitive duplicate-name guard → ``tag.duplicate_name`` (409).
- ``attach(model_type, model_id, tag_id)``    Idempotent: re-attaching is a no-op.
- ``detach(model_type, model_id, tag_id)``    No-op if the link doesn't exist.
- ``list_for_owner(model_type, model_id)``    All Tag objects for an owner.
- ``set_tags_for_owner(model_type, model_id, tag_ids)``
      Replace the owner's whole tag set: add missing links, remove extra links.
- ``detach_all_for_owner(model_type, model_id)``
      Cascade helper: remove all tag links for an owner (called from the three
      entity delete services before the owner row is deleted).

Owner type validation and owner existence checks are done via the
``OWNER_TYPES`` registry and ``resolve_owner`` helper from ``app.services.owners``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.tag import Tag
from app.repositories.tag import TagLinkRepository, TagRepository
from app.services.owners import OWNER_TYPES, resolve_owner


class TagService:
    """Business-logic facade for Tag operations."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = TagRepository(db)
        self._link_repo = TagLinkRepository(db)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _get_or_404(self, tag_id: int) -> Tag:
        """Return a Tag or raise 404 (tag.not_found)."""
        tag = self._repo.get(tag_id)
        if tag is None:
            raise AppError(
                ErrorCode.TAG_NOT_FOUND,
                status_code=404,
                params={"id": tag_id},
                message=f"Tag {tag_id} not found.",
            )
        return tag

    def _assert_no_duplicate_name(self, name: str, *, exclude_id: int | None = None) -> None:
        """Raise 409 (tag.duplicate_name) if a tag with this name already exists.

        The check is case-insensitive (M5 §2, §4.1).

        Parameters
        ----------
        name:
            The proposed tag name.
        exclude_id:
            When renaming an existing tag, pass its id so the check doesn't
            conflict with its own current name.
        """
        existing = self._repo.get_by_name_ci(name)
        if existing is not None and existing.id != exclude_id:
            raise AppError(
                ErrorCode.TAG_DUPLICATE_NAME,
                status_code=409,
                params={"name": name},
                message=f"A tag with the name {name!r} already exists (case-insensitive).",
            )

    def _validate_owner_type(self, model_type: str) -> None:
        """Raise 422 (validation.invalid_input) if model_type is not in OWNER_TYPES."""
        if model_type not in OWNER_TYPES:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                params={"model_type": model_type, "allowed": sorted(OWNER_TYPES)},
                message=(
                    f"Invalid model_type {model_type!r}. Allowed values: {sorted(OWNER_TYPES)}."
                ),
            )

    # ---------------------------------------------------------------------- #
    # Tag CRUD                                                                 #
    # ---------------------------------------------------------------------- #

    def create(self, *, name: str, color: str | None = None) -> Tag:
        """Create a new tag.

        Raises
        ------
        AppError(tag.duplicate_name, 409)
            When a tag with the same name already exists (case-insensitive).
        """
        self._assert_no_duplicate_name(name)
        return self._repo.create(name=name, color=color)

    def get(self, tag_id: int) -> Tag:
        """Return a tag by PK, or raise 404."""
        return self._get_or_404(tag_id)

    def list_tags(self, *, q: str | None = None) -> list[Tag]:
        """Return all tags, optionally filtered by name substring."""
        return self._repo.list(q=q)

    def update(
        self,
        tag_id: int,
        *,
        name: str | None = None,
        color: str | None = None,
        set_color: bool = False,
    ) -> Tag:
        """Patch name and/or color on an existing tag.

        Raises
        ------
        AppError(tag.not_found, 404)
            When the tag does not exist.
        AppError(tag.duplicate_name, 409)
            When renaming to a name already used by another tag (case-insensitive).
        """
        tag = self._get_or_404(tag_id)
        if name is not None:
            self._assert_no_duplicate_name(name, exclude_id=tag_id)
        return self._repo.update(tag, name=name, color=color, set_color=set_color)

    def delete(self, tag_id: int) -> None:
        """Delete a tag.

        The FK ``ondelete=CASCADE`` on ``tag_links.tag_id`` drops all links
        automatically at the DB level.

        Raises
        ------
        AppError(tag.not_found, 404)
            When the tag does not exist.
        """
        tag = self._get_or_404(tag_id)
        self._repo.delete(tag)

    # ---------------------------------------------------------------------- #
    # Tag-link operations                                                      #
    # ---------------------------------------------------------------------- #

    def attach(self, model_type: str, model_id: int, tag_id: int) -> None:
        """Attach a tag to an owner.  Idempotent — re-attaching is a no-op.

        Validates owner type and existence, and tag existence.

        Raises
        ------
        AppError(validation.invalid_input, 422)
            When model_type is not in OWNER_TYPES.
        AppError(<owner>.not_found, 404)
            When the owner does not exist.
        AppError(tag.not_found, 404)
            When the tag does not exist.
        """
        self._validate_owner_type(model_type)
        resolve_owner(self._db, model_type, model_id)
        self._get_or_404(tag_id)
        self._link_repo.link(tag_id, model_type, model_id)

    def detach(self, model_type: str, model_id: int, tag_id: int) -> None:
        """Detach a tag from an owner.  No-op if the link does not exist.

        Validates owner type, owner existence, and tag existence.

        Raises
        ------
        AppError(validation.invalid_input, 422)
            When model_type is not in OWNER_TYPES.
        AppError(<owner>.not_found, 404)
            When the owner does not exist.
        AppError(tag.not_found, 404)
            When the tag does not exist.
        """
        self._validate_owner_type(model_type)
        resolve_owner(self._db, model_type, model_id)
        self._get_or_404(tag_id)
        self._link_repo.unlink(tag_id, model_type, model_id)

    def list_for_owner(self, model_type: str, model_id: int) -> list[Tag]:
        """Return all Tag objects attached to an owner.

        Does NOT validate the owner — intentionally lenient so that this can be
        called in cascade helpers after the owner is gone.
        """
        links = self._link_repo.list_for_owner(model_type, model_id)
        tag_ids = [lnk.tag_id for lnk in links]
        if not tag_ids:
            return []
        # Fetch tags in order (preserving tag_id order from links).
        tags = [self._repo.get(tid) for tid in tag_ids]
        return [t for t in tags if t is not None]

    def set_tags_for_owner(self, model_type: str, model_id: int, tag_ids: list[int]) -> list[Tag]:
        """Replace the owner's whole tag set.

        Adds links for tag_ids not yet attached; removes links for tag_ids no
        longer in the list.  Validates owner type, owner existence, and all
        supplied tag_ids.

        Raises
        ------
        AppError(validation.invalid_input, 422)
            When model_type is not in OWNER_TYPES.
        AppError(<owner>.not_found, 404)
            When the owner does not exist.
        AppError(tag.not_found, 404)
            When any of the supplied tag_ids does not exist.
        """
        self._validate_owner_type(model_type)
        resolve_owner(self._db, model_type, model_id)

        # Validate all supplied tags exist upfront.
        for tid in tag_ids:
            self._get_or_404(tid)

        desired: set[int] = set(tag_ids)
        current: set[int] = self._link_repo.get_tag_ids_for_owner(model_type, model_id)

        # Add missing links.
        for tid in desired - current:
            self._link_repo.link(tid, model_type, model_id)

        # Remove extra links.
        for tid in current - desired:
            self._link_repo.unlink(tid, model_type, model_id)

        return self.list_for_owner(model_type, model_id)

    def detach_all_for_owner(self, model_type: str, model_id: int) -> int:
        """Cascade helper: remove all tag links for an owner.

        Called by entity delete services BEFORE removing the owner row.  Works
        within the same transaction — no post-commit step needed (tag links are
        pure DB rows; no filesystem involvement).

        Returns
        -------
        The count of deleted links.
        """
        return self._link_repo.delete_for_owner(model_type, model_id)
