"""Search service package (M5 Step 6).

Public surface
--------------
SearchProvider      typing.Protocol — the reserved seam for future providers (M9 semantic).
SearchResults       Dataclass holding per-type hit lists + totals.
SearchService       Iterates/fronts the configured provider list.
build_search_service(db)  Factory: returns SearchService([LikeSearchProvider(db)]).
"""

from __future__ import annotations

from app.services.search.provider import SearchProvider, SearchResults
from app.services.search.service import SearchService, build_search_service

__all__ = [
    "SearchProvider",
    "SearchResults",
    "SearchService",
    "build_search_service",
]
