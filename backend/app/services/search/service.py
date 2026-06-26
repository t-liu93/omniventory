"""SearchService — iterates the configured provider list (M5 Step 6 §4.5).

Design (M5.md §4.5)
--------------------
``SearchService`` iterates its configured ``providers`` list and merges the
results.  M5 ships ``[LikeSearchProvider]`` as the sole provider.  Future
semantic providers (M9) implement ``SearchProvider`` and are appended to the
list — with **no change** to this service or the endpoint.

``build_search_service(db)``
    Factory function: constructs a ``SearchService`` with the M5 default
    provider list ``[LikeSearchProvider(db)]``.  The route and tests call this
    factory rather than constructing the chain themselves, keeping the provider
    list in one place.

Merging
-------
With multiple providers, each provider's per-type hit lists are extended in
order; totals are summed.  The per-type ``limit`` is applied inside each
provider, so the merged list from N providers may exceed ``limit``.  For M5
(one provider) this is moot.  A future multi-provider merge strategy can
refine this without changing the endpoint contract.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.search.provider import SearchProvider, SearchResults


class SearchService:
    """Front the configured ``SearchProvider`` list and return merged results.

    Attributes
    ----------
    _providers:
        Ordered list of providers to query.  All providers are called; their
        results are merged in iteration order.
    """

    def __init__(self, providers: list[SearchProvider]) -> None:
        self._providers = providers

    def search(self, q: str, types: set[str], limit: int) -> SearchResults:
        """Run all providers for ``q`` over ``types`` and merge results.

        Parameters
        ----------
        q:
            Non-empty, stripped search string.
        types:
            Set of type identifiers to search.
        limit:
            Per-type result cap, applied inside each provider.

        Returns
        -------
        Merged ``SearchResults`` from all providers.
        """
        merged = SearchResults()
        for provider in self._providers:
            result = provider.search(q, types, limit)
            merged.item_definitions.extend(result.item_definitions)
            merged.stock_instances.extend(result.stock_instances)
            merged.locations.extend(result.locations)
            merged.categories.extend(result.categories)
            merged.tags.extend(result.tags)
            for key, count in result.totals.items():
                merged.totals[key] = merged.totals.get(key, 0) + count
        return merged


def build_search_service(db: Session) -> SearchService:
    """Build the default M5 ``SearchService`` for a given DB session.

    M5 provider list: ``[LikeSearchProvider(db)]``.

    Future milestones add providers here (behind settings toggles) without
    touching the service class or the call sites.
    """
    from app.services.search.like import LikeSearchProvider

    return SearchService([LikeSearchProvider(db)])
