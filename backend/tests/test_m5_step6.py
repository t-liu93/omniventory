"""Tests for M5 Step 6: global LIKE search across entities + SearchProvider seam.

Coverage
--------
- item_definition matched by **name** substring.
- item_definition matched by **custom_field value** (text LIKE on serialized JSON).
- stock_instance matched by **serial** substring.
- stock_instance matched by **barcode code** (definition join: instance → definition
  → barcodes.code).
- stock_instance matched by **custom_field value** (text LIKE).
- location matched by **name** substring.
- category matched by **name** substring.
- tag matched by **name** substring.
- **Per-type cap**: insert more matches than ``limit``; assert the returned list is
  capped and ``totals`` reflects the true match count.
- **Case-insensitive**: upper/lower/mixed query all match the same items.
- **Empty / whitespace q**: returns empty ``SearchResponse`` — no error, no full dump.
- **types filter**: restricts which groups are populated (unspecified types → empty
  list and zero total).
- **SearchProvider Protocol seam**: unit-test that ``LikeSearchProvider`` satisfies
  ``isinstance(..., SearchProvider)``; that a duck-typed class also satisfies it;
  and that ``SearchService`` iterates its providers.
- Unauthenticated request → 401.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixture infrastructure (mirrors test_m5_step5 — includes Barcode model)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    """Reset lru_cache on get_settings / get_engine before and after each test."""
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB; patches DATABASE_URL so get_engine() uses it."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step6_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step6")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient]:
    """TestClient with full schema (all models incl. Barcode), authenticated admin."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(setting_mod)
    importlib.reload(notif_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(barcode_mod)
    importlib.reload(audit_log_mod)

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(
                email="admin@example.com",
                password_hash=hash_password("adminpass"),
            )
            db.flush()

            for code, name in [
                ("durable", "Durable"),
                ("consumable", "Consumable"),
                ("perishable", "Perishable"),
            ]:
                db.add(ItemKind(code=code, name=name, is_system=True))
            db.commit()
        finally:
            db.close()

        resp = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert resp.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str,
    *,
    description: str | None = None,
    custom_fields: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    payload: dict[str, object] = {"name": name, "stock_tracking_mode": "none"}  # type: ignore[type-arg]
    if description is not None:
        payload["description"] = description
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    serial: str | None = None,
    model_number: str | None = None,
    manufacturer: str | None = None,
    custom_fields: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    payload: dict[str, object] = {"definition_id": definition_id}  # type: ignore[type-arg]
    if serial is not None:
        payload["serial"] = serial
    if model_number is not None:
        payload["model_number"] = model_number
    if manufacturer is not None:
        payload["manufacturer"] = manufacturer
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_location(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, f"create_location failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_category(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/categories", json={"name": name})
    assert resp.status_code == 201, f"create_category failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_tag(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/tags", json={"name": name, "color": "blue"})
    assert resp.status_code == 201, f"create_tag failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _bind_barcode(client: TestClient, definition_id: int, code: str) -> dict:  # type: ignore[type-arg]
    resp = client.post(f"/api/definitions/{definition_id}/barcodes", json={"code": code})
    assert resp.status_code == 201, f"bind_barcode failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _search(
    client: TestClient,
    q: str,
    *,
    types: str | None = None,
    limit: int | None = None,
) -> dict:  # type: ignore[type-arg]
    params: dict[str, str | int] = {"q": q}  # type: ignore[type-arg]
    if types is not None:
        params["types"] = types
    if limit is not None:
        params["limit"] = limit
    resp = client.get("/api/search", params=params)
    assert resp.status_code == 200, f"search failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. item_definition — name match
# ---------------------------------------------------------------------------


class TestDefinitionNameMatch:
    """item_definition appears in results when its name contains q."""

    def test_definition_found_by_name(self, test_client: TestClient) -> None:
        _create_definition(test_client, "Bosch Drill Machine")
        result = _search(test_client, "Bosch")
        ids = [h["id"] for h in result["item_definitions"]]
        assert len(ids) >= 1
        names = [h["name"] for h in result["item_definitions"]]
        assert any("Bosch" in n for n in names)

    def test_definition_not_found_by_unrelated_query(self, test_client: TestClient) -> None:
        _create_definition(test_client, "AlphaWidgetXXX")
        result = _search(test_client, "zzz_no_match_zzz")
        assert result["item_definitions"] == []


# ---------------------------------------------------------------------------
# 2. item_definition — custom_field value match
# ---------------------------------------------------------------------------


class TestDefinitionCustomFieldMatch:
    """item_definition matches when a custom_field value contains q."""

    def test_definition_found_by_custom_field_value(self, test_client: TestClient) -> None:
        _create_definition(
            test_client,
            "Power Supply Unit",
            custom_fields={"voltage": "230V_EU", "brand": "Corsair"},
        )
        # Search for a substring of the custom field value.
        result = _search(test_client, "230V_EU")
        ids = [h["id"] for h in result["item_definitions"]]
        assert len(ids) == 1
        assert result["totals"]["item_definitions"] == 1

    def test_definition_custom_field_key_is_also_searchable(self, test_client: TestClient) -> None:
        """Keys are part of the serialized JSON text so they can also be matched."""
        _create_definition(
            test_client,
            "Unique Widget",
            custom_fields={"serial_unique_key_xyz": "value"},
        )
        result = _search(test_client, "serial_unique_key_xyz")
        assert len(result["item_definitions"]) == 1


# ---------------------------------------------------------------------------
# 3. stock_instance — serial match
# ---------------------------------------------------------------------------


class TestInstanceSerialMatch:
    """stock_instance appears in results when its serial contains q."""

    def test_instance_found_by_serial(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Laptop")
        _create_instance(test_client, defn["id"], serial="SN-ABC-12345")
        result = _search(test_client, "ABC-12345")
        assert len(result["stock_instances"]) == 1
        hit = result["stock_instances"][0]
        assert hit["serial"] == "SN-ABC-12345"
        assert hit["definition_id"] == defn["id"]
        assert hit["definition_name"] == "Laptop"

    def test_instance_found_by_model_number(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Monitor")
        _create_instance(test_client, defn["id"], model_number="MON-DELL-27")
        result = _search(test_client, "DELL-27")
        assert any(h["model_number"] == "MON-DELL-27" for h in result["stock_instances"])

    def test_instance_found_by_manufacturer(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Keyboard")
        _create_instance(test_client, defn["id"], manufacturer="Logitech")
        result = _search(test_client, "Logitech")
        assert any(h["manufacturer"] == "Logitech" for h in result["stock_instances"])


# ---------------------------------------------------------------------------
# 4. stock_instance — barcode code match (definition join)
# ---------------------------------------------------------------------------


class TestInstanceBarcodeMatch:
    """stock_instance appears in results when its definition has a matching barcode."""

    def test_instance_found_via_barcode_code(self, test_client: TestClient) -> None:
        """Searching by barcode code surfaces the instance whose definition has that code."""
        defn = _create_definition(test_client, "Coffee Beans 500g")
        _bind_barcode(test_client, defn["id"], "5901234123457")
        inst = _create_instance(test_client, defn["id"])

        result = _search(test_client, "5901234123457")
        instance_ids = [h["id"] for h in result["stock_instances"]]
        assert inst["id"] in instance_ids

    def test_instance_with_multiple_barcodes_not_duplicated(self, test_client: TestClient) -> None:
        """When a definition has two matching barcodes, the instance appears only once."""
        defn = _create_definition(test_client, "Multi-Barcode Product")
        _bind_barcode(test_client, defn["id"], "BARCODE-ALPHA-1")
        _bind_barcode(test_client, defn["id"], "BARCODE-ALPHA-2")
        inst = _create_instance(test_client, defn["id"])

        # Search a prefix common to both barcodes → only one instance hit.
        result = _search(test_client, "BARCODE-ALPHA")
        instance_ids = [h["id"] for h in result["stock_instances"]]
        assert instance_ids.count(inst["id"]) == 1, "instance must not appear twice"

    def test_instance_not_found_for_unknown_barcode(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Another Product")
        _create_instance(test_client, defn["id"])
        result = _search(test_client, "UNKNOWN-BARCODE-XYZ")
        assert result["stock_instances"] == []


# ---------------------------------------------------------------------------
# 5. stock_instance — custom_field value match
# ---------------------------------------------------------------------------


class TestInstanceCustomFieldMatch:
    """stock_instance matches when a custom_field value contains q."""

    def test_instance_found_by_custom_field_value(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Hard Drive")
        _create_instance(
            test_client,
            defn["id"],
            custom_fields={"storage_tb": "2TB", "interface": "SATA_6Gbps"},
        )
        result = _search(test_client, "SATA_6Gbps")
        assert len(result["stock_instances"]) == 1
        assert result["totals"]["stock_instances"] == 1


# ---------------------------------------------------------------------------
# 6. location / category / tag — name match
# ---------------------------------------------------------------------------


class TestEntityNameMatch:
    """location, category, and tag each appear in results when name contains q."""

    def test_location_found_by_name(self, test_client: TestClient) -> None:
        _create_location(test_client, "Kitchen Pantry Shelf")
        result = _search(test_client, "Pantry")
        assert len(result["locations"]) >= 1
        assert any("Pantry" in h["name"] for h in result["locations"])

    def test_category_found_by_name(self, test_client: TestClient) -> None:
        _create_category(test_client, "Power Tools XYZ")
        result = _search(test_client, "Tools XYZ")
        assert len(result["categories"]) >= 1

    def test_tag_found_by_name(self, test_client: TestClient) -> None:
        _create_tag(test_client, "fragile-glassware")
        result = _search(test_client, "glassware")
        assert len(result["tags"]) >= 1
        assert any(h["name"] == "fragile-glassware" for h in result["tags"])


# ---------------------------------------------------------------------------
# 7. Per-type cap + totals
# ---------------------------------------------------------------------------


class TestPerTypeCap:
    """Each type's result list is independently capped; totals reflect the true count."""

    def test_definition_cap_and_totals(self, test_client: TestClient) -> None:
        """Insert 5 definitions with 'captest' in the name, search with limit=2."""
        for i in range(5):
            _create_definition(test_client, f"CapTest Definition {i}")

        result = _search(test_client, "CapTest", limit=2)
        assert len(result["item_definitions"]) == 2, "result list must be capped at limit"
        assert result["totals"]["item_definitions"] == 5, "totals must reflect all 5 matches"

    def test_instance_cap_and_totals(self, test_client: TestClient) -> None:
        """Insert 4 instances with 'capserial' in serial, search with limit=2."""
        defn = _create_definition(test_client, "Capped Item")
        for i in range(4):
            _create_instance(test_client, defn["id"], serial=f"capserial-{i:03d}")

        result = _search(test_client, "capserial", limit=2)
        assert len(result["stock_instances"]) == 2
        assert result["totals"]["stock_instances"] == 4

    def test_location_cap_and_totals(self, test_client: TestClient) -> None:
        for i in range(3):
            _create_location(test_client, f"CapLoc Room {i}")
        result = _search(test_client, "CapLoc", limit=2)
        assert len(result["locations"]) == 2
        assert result["totals"]["locations"] == 3

    def test_tag_cap_and_totals(self, test_client: TestClient) -> None:
        for i in range(3):
            _create_tag(test_client, f"captag-item-{i}")
        result = _search(test_client, "captag", limit=2)
        assert len(result["tags"]) == 2
        assert result["totals"]["tags"] == 3


# ---------------------------------------------------------------------------
# 8. Case-insensitive matching
# ---------------------------------------------------------------------------


class TestCaseInsensitive:
    """Queries match regardless of case (upper / lower / mixed)."""

    def test_uppercase_query_matches_lowercase_name(self, test_client: TestClient) -> None:
        _create_definition(test_client, "logitech mouse")
        result = _search(test_client, "LOGITECH")
        assert len(result["item_definitions"]) >= 1

    def test_lowercase_query_matches_uppercase_name(self, test_client: TestClient) -> None:
        _create_location(test_client, "BASEMENT STORAGE")
        result = _search(test_client, "basement")
        assert len(result["locations"]) >= 1

    def test_mixed_case_query_matches_mixed_name(self, test_client: TestClient) -> None:
        _create_tag(test_client, "Electronics-CASE-Test")
        result = _search(test_client, "electronics-case")
        assert len(result["tags"]) >= 1

    def test_uppercase_query_matches_lowercase_serial(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "CI Serial Item")
        _create_instance(test_client, defn["id"], serial="sn-mixedcase-001")
        result = _search(test_client, "SN-MIXEDCASE")
        assert len(result["stock_instances"]) >= 1


# ---------------------------------------------------------------------------
# 9. Empty / whitespace q
# ---------------------------------------------------------------------------


class TestEmptyQuery:
    """An empty or whitespace-only q returns an empty SearchResponse — no error."""

    def test_empty_string_returns_empty(self, test_client: TestClient) -> None:
        _create_definition(test_client, "Should Not Appear")
        result = _search(test_client, "")
        assert result["item_definitions"] == []
        assert result["stock_instances"] == []
        assert result["locations"] == []
        assert result["categories"] == []
        assert result["tags"] == []
        assert result["totals"]["item_definitions"] == 0

    def test_whitespace_only_returns_empty(self, test_client: TestClient) -> None:
        _create_location(test_client, "Hidden Location")
        result = _search(test_client, "   ")
        assert result["locations"] == []
        assert result["totals"]["locations"] == 0

    def test_empty_query_status_200(self, test_client: TestClient) -> None:
        """Explicitly assert 200 (not 422) for empty q."""
        resp = test_client.get("/api/search", params={"q": ""})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 10. types filter
# ---------------------------------------------------------------------------


class TestTypesFilter:
    """The types query parameter restricts which entity groups are populated."""

    def test_types_location_only_suppresses_definitions(self, test_client: TestClient) -> None:
        _create_definition(test_client, "TypeFilter Widget")
        _create_location(test_client, "TypeFilter Room")

        result = _search(test_client, "TypeFilter", types="location")
        assert result["item_definitions"] == [], "definitions should be empty"
        assert result["totals"]["item_definitions"] == 0
        assert len(result["locations"]) >= 1

    def test_types_definition_and_tag(self, test_client: TestClient) -> None:
        _create_definition(test_client, "MultiType Apple")
        _create_tag(test_client, "MultiType-Fruit")
        _create_location(test_client, "MultiType Storage")

        result = _search(test_client, "MultiType", types="item_definition,tag")
        assert len(result["item_definitions"]) >= 1
        assert len(result["tags"]) >= 1
        assert result["locations"] == [], "locations must be suppressed"

    def test_unknown_type_silently_ignored(self, test_client: TestClient) -> None:
        _create_tag(test_client, "visible-tag-xyz")
        result = _search(test_client, "visible-tag-xyz", types="tag,nonexistent_type")
        assert len(result["tags"]) >= 1

    def test_all_unknown_types_returns_empty(self, test_client: TestClient) -> None:
        result = _search(test_client, "anything", types="bogus,fake")
        assert result["item_definitions"] == []
        assert result["tags"] == []


# ---------------------------------------------------------------------------
# 11. Unauthenticated access → 401
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    """GET /search requires a valid session."""

    def test_no_session_returns_401(self, test_client: TestClient) -> None:
        """A new client (no session cookie) must get 401."""
        # Use a fresh client that shares the app but has no auth cookie.
        from fastapi.testclient import TestClient as FreshClient

        # Re-use the same app object but without the session cookie.
        app = test_client.app
        with FreshClient(app) as anon:
            resp = anon.get("/api/search", params={"q": "test"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 12. SearchProvider Protocol seam (unit tests — no HTTP)
# ---------------------------------------------------------------------------


class TestSearchProviderProtocol:
    """Verify the SearchProvider seam is a real runtime-checkable Protocol."""

    def test_like_provider_satisfies_protocol(self, test_client: TestClient) -> None:  # noqa: ARG002
        """LikeSearchProvider is accepted by isinstance(…, SearchProvider)."""
        from unittest.mock import MagicMock

        from sqlalchemy.orm import Session

        from app.services.search.like import LikeSearchProvider
        from app.services.search.provider import SearchProvider

        fake_db = MagicMock(spec=Session)
        provider = LikeSearchProvider(fake_db)
        assert isinstance(provider, SearchProvider)

    def test_duck_typed_class_satisfies_protocol(self) -> None:
        """A plain class with search() satisfies SearchProvider (structural subtyping)."""
        from app.services.search.provider import SearchProvider, SearchResults

        class FakeProvider:
            def search(self, q: str, types: set[str], limit: int) -> SearchResults:
                return SearchResults()

        assert isinstance(FakeProvider(), SearchProvider)

    def test_class_missing_search_does_not_satisfy_protocol(self) -> None:
        """A class without search() does NOT satisfy SearchProvider."""
        from app.services.search.provider import SearchProvider

        class NotAProvider:
            def lookup(self, code: str) -> None:
                return None

        assert not isinstance(NotAProvider(), SearchProvider)

    def test_search_service_iterates_providers(self) -> None:
        """SearchService calls each provider's search() and merges results."""
        from app.services.search.provider import DefinitionHit, SearchResults
        from app.services.search.service import SearchService

        calls: list[tuple[str, set[str], int]] = []

        class FakeProvider1:
            def search(self, q: str, types: set[str], limit: int) -> SearchResults:
                calls.append(("p1", types, limit))
                return SearchResults(
                    item_definitions=[DefinitionHit(id=1, name="Alpha")],
                    totals={"item_definitions": 1},
                )

        class FakeProvider2:
            def search(self, q: str, types: set[str], limit: int) -> SearchResults:
                calls.append(("p2", types, limit))
                return SearchResults(
                    item_definitions=[DefinitionHit(id=2, name="Beta")],
                    totals={"item_definitions": 1},
                )

        service = SearchService([FakeProvider1(), FakeProvider2()])  # type: ignore[list-item]
        result = service.search("test", {"item_definition"}, 20)

        assert len(calls) == 2, "both providers must be called"
        assert calls[0][0] == "p1"
        assert calls[1][0] == "p2"
        # Merged: both hits present, total = 2.
        assert len(result.item_definitions) == 2
        assert result.totals["item_definitions"] == 2


# ---------------------------------------------------------------------------
# 13. Combined scenario: one query hits multiple entity types
# ---------------------------------------------------------------------------


class TestMultiTypeHit:
    """A single query can simultaneously match definitions, instances, and tags."""

    def test_query_matches_definition_instance_and_tag(self, test_client: TestClient) -> None:
        shared_token = "omnitoken"

        defn = _create_definition(test_client, f"{shared_token}-Widget")
        _create_instance(test_client, defn["id"], serial=f"SN-{shared_token}-001")
        _create_tag(test_client, f"{shared_token}-label")

        result = _search(test_client, shared_token)
        assert len(result["item_definitions"]) >= 1
        assert len(result["stock_instances"]) >= 1
        assert len(result["tags"]) >= 1
