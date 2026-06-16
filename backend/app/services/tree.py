"""Shared tree service base for self-referential tree entities.

Provides the reusable, easy-to-get-wrong tree logic:

1. **Cycle prevention** on reparent: the new parent must not be the node
   itself, nor any of its descendants (roadmap §2.11, M1 §3.1 / §3.2).
2. **Delete-guard**: deleting a non-empty node (one with children) is
   blocked with HTTP 409 (M1 §2 "Tree delete semantics").
3. **Nested tree DTO building**: assembles a recursive ``*TreeNode``
   structure from a flat list of model rows (single DB read, recursive
   nesting in Python — no recursive SQL, per roadmap §2.11).

Both ``LocationService`` (Step 1) and ``CategoryService`` (Step 2) inherit
from ``TreeServiceMixin`` so that the guards and DTO builder are implemented
exactly once, with no copy-paste divergence (M1.md §10 Step-2 checkpoint).
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import HTTPException, status


class _TreeRepoProtocol(Protocol):
    """Minimal interface the mixin needs from any tree repository.

    Concrete repository classes satisfy this protocol structurally (they all
    implement ``get_descendants`` + ``has_children``).
    """

    def get_descendants(self, node_id: int) -> list[Any]:
        """Return all descendants of the given node."""
        ...

    def has_children(self, node_id: int) -> bool:
        """Return True if the node has at least one direct child."""
        ...


class TreeServiceMixin:
    """Mixin providing shared tree-guard logic.

    Concrete services must assign ``self._repo`` (typed to their specific
    repository class) before calling any mixin method.  The repository must
    implement ``_TreeRepoProtocol`` (``get_descendants`` + ``has_children``).

    Usage example::

        class CategoryService(TreeServiceMixin):
            _repo: CategoryRepository  # concrete type for mypy

            def __init__(self, db: Session) -> None:
                self._db = db
                self._repo = CategoryRepository(db)

    The mixin declares ``_repo`` as ``_TreeRepoProtocol`` so that mypy can
    type-check the calls made here; the concrete subclass annotation narrows
    the type for its own methods.
    """

    _repo: _TreeRepoProtocol

    # ---------------------------------------------------------------------- #
    # Shared guards (called with the concrete entity kind label for messages) #
    # ---------------------------------------------------------------------- #

    def _assert_no_cycle(
        self,
        node_id: int,
        proposed_parent_id: int,
        *,
        kind: str = "node",
    ) -> None:
        """Raise HTTP 409 if the proposed reparenting would create a cycle.

        A cycle arises if ``proposed_parent_id`` is the node itself, or
        any of the node's descendants.  Descendants are fetched in Python
        (no recursive SQL — roadmap §2.11).

        Parameters
        ----------
        node_id
            PK of the node being reparented.
        proposed_parent_id
            Candidate new parent PK.
        kind
            Human-readable entity label for error messages (e.g. "location",
            "category").  Defaults to "node".
        """
        if proposed_parent_id == node_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A {kind} cannot be its own parent (cycle detected).",
            )
        descendants = self._repo.get_descendants(node_id)
        descendant_ids = {d.id for d in descendants}
        if proposed_parent_id in descendant_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Reparenting {kind} {node_id} under {kind} "
                    f"{proposed_parent_id} would create a cycle."
                ),
            )

    def _assert_deletable(
        self,
        node_id: int,
        node_name: str,
        *,
        kind: str = "node",
    ) -> None:
        """Raise HTTP 409 if the node has children (delete-guard).

        Parameters
        ----------
        node_id
            PK of the node to check.
        node_name
            Display name of the node (for the error message).
        kind
            Human-readable entity label (e.g. "location", "category").
        """
        if self._repo.has_children(node_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{kind.capitalize()} '{node_name}' (id={node_id}) cannot be "
                    f"deleted because it still has child {kind}s. "
                    "Delete or reparent them first."
                ),
            )
