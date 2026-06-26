"""Repositories for the Tag and TagLink tables (M5 Step 2).

Pure data access — no business rules here.  Business logic (case-insensitive
duplicate guard, owner validation, idempotent attach) lives in
``app.services.tag``.

Public methods
--------------
TagRepository
    create(name, color)             Insert and flush a new Tag row.
    get(id)                         Return a Tag by PK, or None.
    get_by_name_ci(name)            Case-insensitive name lookup.
    list(q)                         List tags, optional substring filter.
    update(tag, name, color)        Apply partial field updates.
    delete(tag)                     Delete a Tag row.

TagLinkRepository
    link(tag_id, model_type, model_id)          Insert a TagLink (no-op on duplicate).
    unlink(tag_id, model_type, model_id)        Delete a TagLink by (tag_id, owner).
    list_for_owner(model_type, model_id)        All TagLinks for an owner.
    list_owners_for_tag(tag_id)                 All TagLinks for a tag.
    exists(tag_id, model_type, model_id)        Whether a specific link exists.
    delete_for_owner(model_type, model_id)      Delete all links for an owner.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tag import Tag, TagLink


class TagRepository:
    """Data-access object for the tags table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def get(self, tag_id: int) -> Tag | None:
        """Return a Tag by PK, or None if not found."""
        return self._db.get(Tag, tag_id)

    def get_by_name_ci(self, name: str) -> Tag | None:
        """Case-insensitive name lookup.  Returns the first match or None."""
        stmt = select(Tag).where(func.lower(Tag.name) == func.lower(name))
        return self._db.scalars(stmt).first()

    def list(self, *, q: str | None = None) -> list[Tag]:
        """Return all tags, optionally filtered by a case-insensitive substring.

        Parameters
        ----------
        q:
            When provided, only tags whose name contains ``q`` (case-insensitive)
            are returned.  Uses ``func.lower(...).contains(func.lower(q))`` for
            portable case-insensitive matching (roadmap §2.11).
        """
        stmt = select(Tag)
        if q is not None:
            stmt = stmt.where(func.lower(Tag.name).contains(func.lower(q)))
        stmt = stmt.order_by(Tag.id)
        return list(self._db.scalars(stmt).all())

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def create(self, *, name: str, color: str | None = None) -> Tag:
        """Insert a new Tag row and flush to get its PK."""
        tag = Tag(name=name, color=color)
        self._db.add(tag)
        self._db.flush()
        return tag

    def update(
        self,
        tag: Tag,
        *,
        name: str | None = None,
        color: str | None = None,
        set_color: bool = False,
    ) -> Tag:
        """Apply partial field updates to a Tag.

        Parameters
        ----------
        name:
            When provided, update the tag name.
        color:
            When ``set_color`` is True, set color to this value (may be None to clear).
        set_color:
            Must be True to update the color field (allows explicit set-to-None).
        """
        if name is not None:
            tag.name = name
        if set_color:
            tag.color = color
        self._db.flush()
        return tag

    def delete(self, tag: Tag) -> None:
        """Delete a Tag row (tag_links cascade via FK ondelete=CASCADE)."""
        self._db.delete(tag)
        self._db.flush()


class TagLinkRepository:
    """Data-access object for the tag_links table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read                                                                     #
    # ---------------------------------------------------------------------- #

    def exists(self, tag_id: int, model_type: str, model_id: int) -> bool:
        """Return True if a link (tag_id, model_type, model_id) already exists."""
        stmt = select(TagLink).where(
            TagLink.tag_id == tag_id,
            TagLink.model_type == model_type,
            TagLink.model_id == model_id,
        )
        return self._db.scalars(stmt).first() is not None

    def list_for_owner(self, model_type: str, model_id: int) -> list[TagLink]:
        """Return all TagLinks for a given (model_type, model_id) owner.

        Ordered by tag_id ascending (stable ordering).
        """
        stmt = (
            select(TagLink)
            .where(TagLink.model_type == model_type, TagLink.model_id == model_id)
            .order_by(TagLink.tag_id)
        )
        return list(self._db.scalars(stmt).all())

    def list_owners_for_tag(self, tag_id: int) -> list[TagLink]:
        """Return all TagLinks for a given tag_id."""
        stmt = select(TagLink).where(TagLink.tag_id == tag_id).order_by(TagLink.id)
        return list(self._db.scalars(stmt).all())

    def get_tag_ids_for_owner(self, model_type: str, model_id: int) -> set[int]:
        """Return the set of tag_ids currently attached to an owner."""
        links = self.list_for_owner(model_type, model_id)
        return {lnk.tag_id for lnk in links}

    # ---------------------------------------------------------------------- #
    # Write                                                                    #
    # ---------------------------------------------------------------------- #

    def link(self, tag_id: int, model_type: str, model_id: int) -> TagLink | None:
        """Insert a TagLink; return the row, or None if it already exists.

        The unique constraint ``uq_tag_links_tag_owner`` makes a duplicate insert
        an IntegrityError.  We catch it here so the caller can treat a repeat
        attach as idempotent.
        """
        if self.exists(tag_id, model_type, model_id):
            # Already linked — idempotent: return None (no new row inserted).
            return None

        lnk = TagLink(tag_id=tag_id, model_type=model_type, model_id=model_id)
        self._db.add(lnk)
        try:
            self._db.flush()
        except IntegrityError:
            self._db.rollback()
            return None
        return lnk

    def unlink(self, tag_id: int, model_type: str, model_id: int) -> bool:
        """Delete a specific TagLink.  Returns True if it existed, False otherwise."""
        stmt = select(TagLink).where(
            TagLink.tag_id == tag_id,
            TagLink.model_type == model_type,
            TagLink.model_id == model_id,
        )
        lnk = self._db.scalars(stmt).first()
        if lnk is None:
            return False
        self._db.delete(lnk)
        self._db.flush()
        return True

    def delete_for_owner(self, model_type: str, model_id: int) -> int:
        """Delete all TagLinks for an owner.  Returns the count of deleted rows."""
        links = self.list_for_owner(model_type, model_id)
        for lnk in links:
            self._db.delete(lnk)
        if links:
            self._db.flush()
        return len(links)
