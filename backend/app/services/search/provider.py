"""SearchProvider Protocol and result types (M5 Step 6).

Design (M5.md §4.5, roadmap §2.12)
------------------------------------
The provider seam exists so that a future LLM-assisted semantic search backend
(M9) can be added as a new provider class and appended to the configured list,
with **no** change to the ``GET /search`` endpoint or the frontend.  M5 ships
only the ``LikeSearchProvider``; the seam is a real ``typing.Protocol``
(not just a base class or hard-wired call).

Hit types (lightweight summaries — enough to render + link to the subject):

``DefinitionHit``
    id + name (item definition).

``InstanceHit``
    id + definition_id + definition_name + durable-identity fields
    (serial / model_number / manufacturer — whichever are set).

``LocationHit``
    id + name.

``CategoryHit``
    id + name.

``TagHit``
    id + name + color.

``SearchResults``
    Per-type lists + totals dict (``{"item_definitions": N, ...}``).
    Types not searched have empty lists and no totals entry.

``SearchProvider`` (Protocol)
    A callable seam: ``search(q, types, limit) -> SearchResults``.
    ``runtime_checkable`` allows isinstance checks in tests.
    Any class implementing ``search(q, types, limit)`` satisfies the Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class DefinitionHit:
    """Lightweight item-definition search hit."""

    id: int
    name: str


@dataclass
class InstanceHit:
    """Lightweight stock-instance search hit."""

    id: int
    definition_id: int
    definition_name: str
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None


@dataclass
class LocationHit:
    """Lightweight location search hit."""

    id: int
    name: str


@dataclass
class CategoryHit:
    """Lightweight category search hit."""

    id: int
    name: str


@dataclass
class TagHit:
    """Lightweight tag search hit."""

    id: int
    name: str
    color: str | None = None


@dataclass
class SearchResults:
    """Grouped per-type results returned by a ``SearchProvider``.

    Attributes
    ----------
    item_definitions:
        Matched item-definition summaries (capped at ``limit``).
    stock_instances:
        Matched stock-instance summaries (capped at ``limit``).
    locations:
        Matched location summaries (capped at ``limit``).
    categories:
        Matched category summaries (capped at ``limit``).
    tags:
        Matched tag summaries (capped at ``limit``).
    totals:
        Mapping of ``type_plural_key → true_match_count`` for each type that
        was searched.  Types not included in the requested ``types`` set have
        no key here.  Example: ``{"item_definitions": 42, "locations": 3}``.
    """

    item_definitions: list[DefinitionHit] = field(default_factory=list)
    stock_instances: list[InstanceHit] = field(default_factory=list)
    locations: list[LocationHit] = field(default_factory=list)
    categories: list[CategoryHit] = field(default_factory=list)
    tags: list[TagHit] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for pluggable search-provider implementations (M5 Step 6, roadmap §2.12).

    Every provider must implement ``search(q, types, limit) -> SearchResults``.
    Providers must never raise — catch and log internally, returning an empty
    ``SearchResults`` on error so the chain can continue.

    M5 ships one concrete implementation: ``LikeSearchProvider``.  Future
    semantic providers (M9) implement the same interface and are appended to
    the ``SearchService`` provider list without touching the endpoint or the
    frontend.
    """

    def search(self, q: str, types: set[str], limit: int) -> SearchResults:
        """Run a search for ``q`` across the requested entity types.

        Parameters
        ----------
        q:
            Non-empty, already-stripped search string.
        types:
            Subset of ``{"item_definition", "stock_instance", "location",
            "category", "tag"}`` to search.  Providers should only populate
            result lists for the types they were asked to search.
        limit:
            Per-type cap: each result list should be at most this long.

        Returns
        -------
        ``SearchResults`` with per-type hit lists and totals.
        """
        ...
