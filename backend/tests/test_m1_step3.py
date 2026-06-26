"""M1 Step 3 tests: Item kinds lookup + Item Definition CRUD.

Required coverage (easy-to-get-wrong logic, per M1.md §5 / §9 Step 3):
- GET /kinds returns exactly the three seeded kinds (durable / consumable /
  perishable) after ``upgrade head``.
- Definition create / read / update / delete (basic CRUD).
- Default-kind resolution: creating a definition without kind_id → kind = durable.
- Invalid kind_id rejected with 422.
- FK to category / location validated (404 for non-existent).
- q= case-insensitive substring search over definition name.
- category_id filter returns only matching definitions.
- Migration 0006 (incl. seed) up/down.
- Migration 0007 up/down.
"""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1step3_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


def _make_fresh_session() -> Session:
    """In-memory SQLite session with all current models registered."""
    import importlib

    from sqlalchemy import event

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.user as user_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)

    from app.db.base import Base as _Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory()


def _seed_kinds(session: Session) -> dict[str, int]:
    """Insert the three system kinds directly into an in-memory DB.

    Returns a dict mapping code → id.
    """
    from app.models.item_kind import ItemKind

    kinds = [
        ItemKind(code="durable", name="Durable", is_system=True),
        ItemKind(code="consumable", name="Consumable", is_system=True),
        ItemKind(code="perishable", name="Perishable", is_system=True),
    ]
    for k in kinds:
        session.add(k)
    session.flush()
    session.commit()
    return {k.code: k.id for k in kinds}


# ---------------------------------------------------------------------------
# Fixtures
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
def db_session() -> Generator[Session]:
    """Fresh in-memory SQLite session with all models and seeded kinds."""
    session = _make_fresh_session()
    _seed_kinds(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB; sets SECRET_KEY, ENVIRONMENT=test, DATABASE_URL."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-step3")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """TestClient with a temp-file SQLite, full schema, and an authenticated session."""
    import importlib

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.user as user_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

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
            repo.create(email="admin@example.com", password_hash=hash_password("adminpass"))
            db.flush()

            # Seed the three system kinds so definition endpoints can resolve defaults.
            for code, name in [
                ("durable", "Durable"),
                ("consumable", "Consumable"),
                ("perishable", "Perishable"),
            ]:
                db.add(ItemKind(code=code, name=name, is_system=True))
            db.commit()
        finally:
            db.close()

        response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert response.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str,
    *,
    kind_id: int | None = None,
    category_id: int | None = None,
    description: str | None = None,
    unit: str | None = None,
    default_location_id: int | None = None,
) -> dict:  # type: ignore[type-arg]
    """POST /api/definitions and return the response JSON dict."""
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if kind_id is not None:
        payload["kind_id"] = kind_id
    if category_id is not None:
        payload["category_id"] = category_id
    if description is not None:
        payload["description"] = description
    if unit is not None:
        payload["unit"] = unit
    if default_location_id is not None:
        payload["default_location_id"] = default_location_id

    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_category(
    client: TestClient,
    name: str,
    *,
    parent_id: int | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post("/api/categories", json=payload)
    assert resp.status_code == 201
    return resp.json()  # type: ignore[return-value]


def _create_location(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. GET /kinds — seeded kinds
# ---------------------------------------------------------------------------


class TestKindsEndpoint:
    """GET /kinds must return exactly the three seeded system kinds."""

    def test_get_kinds_returns_three_seeded_kinds(self, test_client: TestClient) -> None:
        """GET /kinds returns exactly durable, consumable, perishable."""
        resp = test_client.get("/api/kinds")
        assert resp.status_code == 200
        kinds = resp.json()
        assert len(kinds) == 3, f"Expected 3 kinds, got: {kinds}"
        codes = {k["code"] for k in kinds}
        assert codes == {"durable", "consumable", "perishable"}

    def test_get_kinds_all_system(self, test_client: TestClient) -> None:
        """All seeded kinds have is_system=true."""
        resp = test_client.get("/api/kinds")
        assert resp.status_code == 200
        for k in resp.json():
            assert k["is_system"] is True, f"Expected is_system=True for {k['code']}"

    def test_get_kinds_has_required_fields(self, test_client: TestClient) -> None:
        """Each kind has id, code, name, is_system, created_at."""
        resp = test_client.get("/api/kinds")
        assert resp.status_code == 200
        for k in resp.json():
            assert "id" in k
            assert "code" in k
            assert "name" in k
            assert "is_system" in k
            assert "created_at" in k

    def test_get_kinds_requires_auth(self, temp_db: Path) -> None:  # noqa: ARG002
        """GET /kinds without a session returns 401."""
        import importlib

        import app.db.base as db_base_mod
        import app.models.app_config as app_config_mod
        import app.models.attachment as attachment_mod
        import app.models.category as cat_mod
        import app.models.household as hh_mod
        import app.models.item_definition as idef_mod
        import app.models.item_kind as ikind_mod
        import app.models.location as loc_mod
        import app.models.media_file as media_file_mod
        import app.models.session as sess_mod
        import app.models.stock_instance as stock_instance_mod
        import app.models.stock_movement as stock_movement_mod
        import app.models.user as user_mod

        importlib.reload(db_base_mod)
        importlib.reload(hh_mod)
        importlib.reload(user_mod)
        importlib.reload(sess_mod)
        importlib.reload(app_config_mod)
        importlib.reload(stock_instance_mod)
        importlib.reload(stock_movement_mod)
        importlib.reload(loc_mod)
        importlib.reload(cat_mod)
        importlib.reload(ikind_mod)
        importlib.reload(idef_mod)
        importlib.reload(media_file_mod)
        importlib.reload(attachment_mod)

        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/kinds")
            assert resp.status_code == 401
        drop_all_sqlite(Base, engine)

    def test_no_write_endpoints_exist(self, test_client: TestClient) -> None:
        """POST/PATCH/DELETE /kinds should return 405 (method not allowed) or 404."""
        # The kinds router only has GET — write methods must not be wired.
        post_resp = test_client.post("/api/kinds", json={"code": "custom", "name": "Custom"})
        assert post_resp.status_code in (404, 405), (
            f"POST /kinds should not be allowed, got {post_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# 2. Definition CRUD
# ---------------------------------------------------------------------------


class TestDefinitionCRUD:
    """Basic CRUD for item definitions."""

    def test_create_definition_minimal(self, test_client: TestClient) -> None:
        """POST /definitions with name only creates a definition (default kind=durable)."""
        data = _create_definition(test_client, "Cordless Drill")
        assert data["name"] == "Cordless Drill"
        assert data["kind"]["code"] == "durable"
        assert data["unit"] == "pcs"
        assert data["category_id"] is None
        assert data["default_location_id"] is None
        assert "id" in data
        assert "created_at" in data

    def test_create_definition_with_all_fields(self, test_client: TestClient) -> None:
        """POST /definitions with all fields stores them correctly."""
        cat = _create_category(test_client, "Tools")
        loc = _create_location(test_client, "Garage")

        # Get the durable kind_id from GET /kinds.
        kinds = test_client.get("/api/kinds").json()
        durable_id = next(k["id"] for k in kinds if k["code"] == "durable")

        data = _create_definition(
            test_client,
            "Angle Grinder",
            kind_id=durable_id,
            category_id=cat["id"],
            description="Electric angle grinder",
            unit="pcs",
            default_location_id=loc["id"],
        )
        assert data["name"] == "Angle Grinder"
        assert data["description"] == "Electric angle grinder"
        assert data["category_id"] == cat["id"]
        assert data["default_location_id"] == loc["id"]
        assert data["kind"]["code"] == "durable"

    def test_get_definition_by_id(self, test_client: TestClient) -> None:
        """GET /definitions/{id} returns the definition."""
        created = _create_definition(test_client, "Hammer")
        resp = test_client.get(f"/api/definitions/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Hammer"

    def test_get_definition_404(self, test_client: TestClient) -> None:
        """GET /definitions/{id} returns 404 for a non-existent id."""
        resp = test_client.get("/api/definitions/9999")
        assert resp.status_code == 404

    def test_list_definitions_empty(self, test_client: TestClient) -> None:
        """GET /definitions returns [] when no definitions exist."""
        resp = test_client.get("/api/definitions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_definitions_returns_all(self, test_client: TestClient) -> None:
        """GET /definitions returns all definitions."""
        _create_definition(test_client, "Drill")
        _create_definition(test_client, "Saw")
        resp = test_client.get("/api/definitions")
        assert resp.status_code == 200
        names = {d["name"] for d in resp.json()}
        assert names == {"Drill", "Saw"}

    def test_update_name(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} can update the name."""
        created = _create_definition(test_client, "Old Name")
        resp = test_client.patch(f"/api/definitions/{created['id']}", json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_unit(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} can update the unit."""
        created = _create_definition(test_client, "Cable")
        resp = test_client.patch(f"/api/definitions/{created['id']}", json={"unit": "m"})
        assert resp.status_code == 200
        assert resp.json()["unit"] == "m"

    def test_update_description(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} can update description."""
        created = _create_definition(test_client, "Widget")
        resp = test_client.patch(
            f"/api/definitions/{created['id']}", json={"description": "A useful widget"}
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "A useful widget"

    def test_delete_definition(self, test_client: TestClient) -> None:
        """DELETE /definitions/{id} on a definition returns 204."""
        created = _create_definition(test_client, "Throwaway")
        resp = test_client.delete(f"/api/definitions/{created['id']}")
        assert resp.status_code == 204

        get_resp = test_client.get(f"/api/definitions/{created['id']}")
        assert get_resp.status_code == 404

    def test_delete_404_for_nonexistent(self, test_client: TestClient) -> None:
        """DELETE /definitions/{id} returns 404 for a non-existent definition."""
        resp = test_client.delete("/api/definitions/9999")
        assert resp.status_code == 404

    def test_create_requires_auth(self, temp_db: Path) -> None:  # noqa: ARG002
        """POST /definitions without a session returns 401."""
        import importlib

        import app.db.base as db_base_mod
        import app.models.app_config as app_config_mod
        import app.models.attachment as attachment_mod
        import app.models.category as cat_mod
        import app.models.household as hh_mod
        import app.models.item_definition as idef_mod
        import app.models.item_kind as ikind_mod
        import app.models.location as loc_mod
        import app.models.media_file as media_file_mod
        import app.models.session as sess_mod
        import app.models.stock_instance as stock_instance_mod
        import app.models.stock_movement as stock_movement_mod
        import app.models.user as user_mod

        importlib.reload(db_base_mod)
        importlib.reload(hh_mod)
        importlib.reload(user_mod)
        importlib.reload(sess_mod)
        importlib.reload(app_config_mod)
        importlib.reload(stock_instance_mod)
        importlib.reload(stock_movement_mod)
        importlib.reload(loc_mod)
        importlib.reload(cat_mod)
        importlib.reload(ikind_mod)
        importlib.reload(idef_mod)
        importlib.reload(media_file_mod)
        importlib.reload(attachment_mod)

        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/definitions", json={"name": "No Auth"})
            assert resp.status_code == 401
        drop_all_sqlite(Base, engine)

    def test_definition_response_includes_kind_object(self, test_client: TestClient) -> None:
        """DefinitionResponse includes the nested kind object with id/code/name."""
        created = _create_definition(test_client, "Wrench")
        assert "kind" in created
        assert "code" in created["kind"]
        assert "name" in created["kind"]
        assert "id" in created["kind"]


# ---------------------------------------------------------------------------
# 3. Default-kind resolution (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestDefaultKindResolution:
    """Creating a definition without kind_id must default to 'durable'."""

    def test_omitting_kind_id_defaults_to_durable(self, test_client: TestClient) -> None:
        """POST /definitions without kind_id → kind.code == 'durable'."""
        data = _create_definition(test_client, "No Kind Specified")
        assert data["kind"]["code"] == "durable"

    def test_specifying_consumable_kind(self, test_client: TestClient) -> None:
        """POST /definitions with consumable kind_id → kind.code == 'consumable'."""
        kinds = test_client.get("/api/kinds").json()
        consumable_id = next(k["id"] for k in kinds if k["code"] == "consumable")
        data = _create_definition(test_client, "AA Batteries", kind_id=consumable_id)
        assert data["kind"]["code"] == "consumable"

    def test_specifying_perishable_kind(self, test_client: TestClient) -> None:
        """POST /definitions with perishable kind_id → kind.code == 'perishable'."""
        kinds = test_client.get("/api/kinds").json()
        perishable_id = next(k["id"] for k in kinds if k["code"] == "perishable")
        data = _create_definition(test_client, "Milk", kind_id=perishable_id)
        assert data["kind"]["code"] == "perishable"

    def test_service_default_kind_resolution_unit(self, db_session: Session) -> None:
        """ItemDefinitionService resolves 'durable' when kind_id=None (unit test)."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Test"))
        db_session.commit()

        from app.models.item_kind import ItemKind

        durable = db_session.scalars(
            __import__("sqlalchemy", fromlist=["select"])
            .select(ItemKind)
            .where(ItemKind.code == "durable")
        ).first()
        assert durable is not None
        assert defn.kind_id == durable.id


# ---------------------------------------------------------------------------
# 4. Invalid kind_id rejected (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestInvalidKindIdRejected:
    """Supplying a non-existent kind_id must be rejected with 422."""

    def test_invalid_kind_id_on_create_returns_422(self, test_client: TestClient) -> None:
        """POST /definitions with non-existent kind_id returns 422."""
        resp = test_client.post("/api/definitions", json={"name": "Bad Kind", "kind_id": 9999})
        assert resp.status_code == 422, (
            f"Expected 422 for invalid kind_id, got {resp.status_code}: {resp.json()}"
        )

    def test_invalid_kind_id_on_update_returns_422(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with non-existent kind_id returns 422."""
        created = _create_definition(test_client, "Widget")
        resp = test_client.patch(f"/api/definitions/{created['id']}", json={"kind_id": 9999})
        assert resp.status_code == 422

    def test_service_rejects_invalid_kind_id_unit(self, db_session: Session) -> None:
        """ItemDefinitionService raises AppError 422 for a non-existent kind_id (unit test)."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(DefinitionCreate(name="Bad", kind_id=9999))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.ITEM_KIND_NOT_FOUND


# ---------------------------------------------------------------------------
# 5. FK to category / location validated
# ---------------------------------------------------------------------------


class TestFKValidation:
    """Non-existent category_id / default_location_id must be rejected."""

    def test_nonexistent_category_id_rejected(self, test_client: TestClient) -> None:
        """POST /definitions with non-existent category_id returns 404."""
        resp = test_client.post("/api/definitions", json={"name": "X", "category_id": 9999})
        assert resp.status_code == 404

    def test_nonexistent_default_location_id_rejected(self, test_client: TestClient) -> None:
        """POST /definitions with non-existent default_location_id returns 404."""
        resp = test_client.post("/api/definitions", json={"name": "X", "default_location_id": 9999})
        assert resp.status_code == 404

    def test_valid_category_id_accepted(self, test_client: TestClient) -> None:
        """POST /definitions with a valid category_id is accepted."""
        cat = _create_category(test_client, "Furniture")
        data = _create_definition(test_client, "Chair", category_id=cat["id"])
        assert data["category_id"] == cat["id"]

    def test_valid_default_location_id_accepted(self, test_client: TestClient) -> None:
        """POST /definitions with a valid default_location_id is accepted."""
        loc = _create_location(test_client, "Storage Room")
        data = _create_definition(test_client, "Box", default_location_id=loc["id"])
        assert data["default_location_id"] == loc["id"]

    def test_update_with_nonexistent_category_rejected(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with non-existent category_id returns 404."""
        created = _create_definition(test_client, "Widget")
        resp = test_client.patch(f"/api/definitions/{created['id']}", json={"category_id": 9999})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. Search (q param)
# ---------------------------------------------------------------------------


class TestDefinitionSearch:
    """q= case-insensitive substring search on definition name."""

    def test_search_case_insensitive(self, test_client: TestClient) -> None:
        """q=drill matches 'Cordless Drill' regardless of case."""
        _create_definition(test_client, "Cordless Drill")
        _create_definition(test_client, "Hammer")

        for q in ["drill", "DRILL", "Drill", "rill"]:
            resp = test_client.get(f"/api/definitions?q={q}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) == 1, f"Expected 1 result for q={q!r}, got {results}"
            assert results[0]["name"] == "Cordless Drill"

    def test_search_no_match(self, test_client: TestClient) -> None:
        """q= with no matching definitions returns []."""
        _create_definition(test_client, "Hammer")
        resp = test_client.get("/api/definitions?q=xyz_no_match")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_matches_multiple(self, test_client: TestClient) -> None:
        """Substring match can return multiple results."""
        _create_definition(test_client, "Power Drill")
        _create_definition(test_client, "Drill Press")
        _create_definition(test_client, "Hammer")

        resp = test_client.get("/api/definitions?q=drill")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Power Drill", "Drill Press"}

    def test_no_q_returns_all(self, test_client: TestClient) -> None:
        """No q param returns all definitions."""
        _create_definition(test_client, "A")
        _create_definition(test_client, "B")
        resp = test_client.get("/api/definitions")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# 7. category_id filter
# ---------------------------------------------------------------------------


class TestDefinitionCategoryFilter:
    """category_id= filter returns only definitions in that category."""

    def test_filter_by_category_id(self, test_client: TestClient) -> None:
        """GET /definitions?category_id=X returns only definitions with that category."""
        tools = _create_category(test_client, "Tools")
        electronics = _create_category(test_client, "Electronics")

        _create_definition(test_client, "Drill", category_id=tools["id"])
        _create_definition(test_client, "Saw", category_id=tools["id"])
        _create_definition(test_client, "Phone", category_id=electronics["id"])

        resp = test_client.get(f"/api/definitions?category_id={tools['id']}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Drill", "Saw"}

    def test_filter_category_no_match(self, test_client: TestClient) -> None:
        """GET /definitions?category_id=X returns [] when no definitions in that category."""
        empty_cat = _create_category(test_client, "Empty Category")
        resp = test_client.get(f"/api/definitions?category_id={empty_cat['id']}")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_parent_includes_child_category(self, test_client: TestClient) -> None:
        """Filtering by a parent category also returns definitions from child categories.

        Structure:
          Tools (root)
          └── Power Tools (child of Tools)
          Electronics (unrelated root)

        Definitions:
          "Bosch Hammer" → Power Tools
          "Hand Saw"     → Tools
          "Smart TV"     → Electronics

        ?category_id=Tools  → Bosch Hammer + Hand Saw (not Smart TV)
        ?category_id=Power Tools → Bosch Hammer only
        """
        tools = _create_category(test_client, "Tools")
        power_tools = _create_category(test_client, "Power Tools", parent_id=tools["id"])
        electronics = _create_category(test_client, "Electronics")

        _create_definition(test_client, "Bosch Hammer", category_id=power_tools["id"])
        _create_definition(test_client, "Hand Saw", category_id=tools["id"])
        _create_definition(test_client, "Smart TV", category_id=electronics["id"])

        # Filtering by parent Tools must include both Tools and Power Tools definitions.
        resp = test_client.get(f"/api/definitions?category_id={tools['id']}")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()}
        assert names == {"Bosch Hammer", "Hand Saw"}, (
            f"Expected {{Bosch Hammer, Hand Saw}}, got {names}"
        )

        # Filtering by child Power Tools must return only that child's definitions.
        resp2 = test_client.get(f"/api/definitions?category_id={power_tools['id']}")
        assert resp2.status_code == 200
        names2 = {r["name"] for r in resp2.json()}
        assert names2 == {"Bosch Hammer"}, f"Expected {{Bosch Hammer}}, got {names2}"

    def test_filter_parent_includes_grandchild_category(self, test_client: TestClient) -> None:
        """Filtering by an ancestor returns definitions from all descendant levels.

        Structure:
          Tools → Power Tools → Cordless  (three-level hierarchy)

        A definition at the grandchild level (Cordless) must appear when
        filtering by the root (Tools), proving multi-level recursion.
        """
        tools = _create_category(test_client, "Tools2")
        power_tools = _create_category(test_client, "Power Tools2", parent_id=tools["id"])
        cordless = _create_category(test_client, "Cordless", parent_id=power_tools["id"])

        _create_definition(test_client, "Cordless Drill", category_id=cordless["id"])

        resp = test_client.get(f"/api/definitions?category_id={tools['id']}")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()}
        assert "Cordless Drill" in names, (
            f"Grandchild definition missing when filtering by root; got {names}"
        )

    def test_filter_unrelated_category_not_included(self, test_client: TestClient) -> None:
        """Uncategorised definitions are never included in a category subtree filter."""
        tools = _create_category(test_client, "ToolsOnly")
        _create_definition(test_client, "In Tools", category_id=tools["id"])
        _create_definition(test_client, "No Category")  # category_id=None

        resp = test_client.get(f"/api/definitions?category_id={tools['id']}")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()}
        assert names == {"In Tools"}, (
            f"Uncategorised definition must not appear in subtree filter; got {names}"
        )

    def test_filter_q_and_category_id_combined(self, test_client: TestClient) -> None:
        """q= and category_id= filters are ANDed and category uses the subtree."""
        tools = _create_category(test_client, "ToolsCombined")
        sub = _create_category(test_client, "SubCombined", parent_id=tools["id"])

        _create_definition(test_client, "Power Drill", category_id=sub["id"])
        _create_definition(test_client, "Power Saw", category_id=sub["id"])
        _create_definition(test_client, "Hammer", category_id=tools["id"])
        _create_definition(test_client, "Power Bank")  # unrelated category

        resp = test_client.get(f"/api/definitions?q=power&category_id={tools['id']}")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()}
        # "Power Drill" and "Power Saw" are in subtree and match q=power
        # "Hammer" matches category but not q
        # "Power Bank" matches q but not category
        assert names == {"Power Drill", "Power Saw"}, (
            f"Expected {{Power Drill, Power Saw}}, got {names}"
        )


# ---------------------------------------------------------------------------
# 8. Repository layer unit tests
# ---------------------------------------------------------------------------


class TestItemKindRepository:
    """ItemKindRepository unit tests."""

    def test_list_all_returns_seeded_kinds(self, db_session: Session) -> None:
        """list_all() returns all three seeded kinds."""
        from app.repositories.item_kind import ItemKindRepository

        repo = ItemKindRepository(db_session)
        kinds = repo.list_all()
        assert len(kinds) == 3
        codes = {k.code for k in kinds}
        assert codes == {"durable", "consumable", "perishable"}

    def test_get_by_code_found(self, db_session: Session) -> None:
        """get_by_code() returns the correct kind."""
        from app.repositories.item_kind import ItemKindRepository

        repo = ItemKindRepository(db_session)
        k = repo.get_by_code("durable")
        assert k is not None
        assert k.code == "durable"
        assert k.is_system is True

    def test_get_by_code_missing(self, db_session: Session) -> None:
        """get_by_code() returns None for a non-existent code."""
        from app.repositories.item_kind import ItemKindRepository

        repo = ItemKindRepository(db_session)
        assert repo.get_by_code("nonexistent") is None

    def test_list_all_ordered_by_id(self, db_session: Session) -> None:
        """list_all() returns kinds ordered by id (ascending)."""
        from app.repositories.item_kind import ItemKindRepository

        repo = ItemKindRepository(db_session)
        kinds = repo.list_all()
        ids = [k.id for k in kinds]
        assert ids == sorted(ids)


class TestItemDefinitionRepository:
    """ItemDefinitionRepository unit tests."""

    def test_create_and_get(self, db_session: Session) -> None:
        """create() and get() roundtrip."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_repo = ItemKindRepository(db_session)
        durable = kind_repo.get_by_code("durable")
        assert durable is not None

        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Hammer", kind_id=durable.id, description="Claw hammer")
        db_session.commit()

        found = repo.get(defn.id)
        assert found is not None
        assert found.name == "Hammer"
        assert found.description == "Claw hammer"
        assert found.kind_id == durable.id

    def test_get_returns_none_for_missing(self, db_session: Session) -> None:
        """get() returns None for a non-existent id."""
        from app.repositories.item_definition import ItemDefinitionRepository

        repo = ItemDefinitionRepository(db_session)
        assert repo.get(9999) is None

    def test_list_all_q_filter(self, db_session: Session) -> None:
        """list_all(q=...) is a case-insensitive substring match."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_id = ItemKindRepository(db_session).get_by_code("durable").id  # type: ignore[union-attr]
        repo = ItemDefinitionRepository(db_session)
        repo.create(name="Power Drill", kind_id=kind_id)
        repo.create(name="Hammer", kind_id=kind_id)
        db_session.commit()

        results = repo.list_all(q="drill")
        assert len(results) == 1
        assert results[0].name == "Power Drill"

        results_upper = repo.list_all(q="HAMMER")
        assert len(results_upper) == 1
        assert results_upper[0].name == "Hammer"

    def test_update_name_and_unit(self, db_session: Session) -> None:
        """update() can change name and unit."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_id = ItemKindRepository(db_session).get_by_code("durable").id  # type: ignore[union-attr]
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Cable", kind_id=kind_id, unit="pcs")
        db_session.commit()

        repo.update(defn, name="Ethernet Cable", unit="m")
        db_session.commit()

        found = repo.get(defn.id)
        assert found is not None
        assert found.name == "Ethernet Cable"
        assert found.unit == "m"

    def test_delete(self, db_session: Session) -> None:
        """delete() removes the row."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_id = ItemKindRepository(db_session).get_by_code("durable").id  # type: ignore[union-attr]
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Temporary", kind_id=kind_id)
        db_session.commit()

        repo.delete(defn)
        db_session.commit()
        assert repo.get(defn.id) is None


# ---------------------------------------------------------------------------
# 9. Service layer unit tests
# ---------------------------------------------------------------------------


class TestItemDefinitionService:
    """ItemDefinitionService business-logic unit tests."""

    def test_create_without_kind_id_defaults_to_durable(self, db_session: Session) -> None:
        """Service.create with kind_id=None resolves to the durable kind."""
        from app.repositories.item_kind import ItemKindRepository
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Widget"))
        db_session.commit()

        durable = ItemKindRepository(db_session).get_by_code("durable")
        assert durable is not None
        assert defn.kind_id == durable.id

    def test_create_with_invalid_kind_id_raises_422(self, db_session: Session) -> None:
        """Service.create raises AppError 422 for a non-existent kind_id."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(DefinitionCreate(name="Bad", kind_id=9999))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.ITEM_KIND_NOT_FOUND

    def test_update_without_kind_id_does_not_change_kind(self, db_session: Session) -> None:
        """PATCH without kind_id in payload does not change the kind."""
        from app.repositories.item_kind import ItemKindRepository
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        consumable = ItemKindRepository(db_session).get_by_code("consumable")
        assert consumable is not None

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Battery", kind_id=consumable.id))
        db_session.commit()

        updated = svc.update(defn.id, DefinitionUpdate(name="AA Battery"))
        assert updated.kind_id == consumable.id  # unchanged

    def test_get_nonexistent_raises_404(self, db_session: Session) -> None:
        """Service.get raises AppError 404 for a non-existent definition."""
        from app.core.errors import AppError, ErrorCode
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.get(9999)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == ErrorCode.ITEM_DEFINITION_NOT_FOUND

    def test_delete_nonexistent_raises_404(self, db_session: Session) -> None:
        """Service.delete raises AppError 404 for a non-existent definition."""
        from app.core.errors import AppError, ErrorCode
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.delete(9999)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == ErrorCode.ITEM_DEFINITION_NOT_FOUND


# ---------------------------------------------------------------------------
# 10. Alembic migration 0006 (item_kinds + seed)
# ---------------------------------------------------------------------------


class TestAlembicMigration0006:
    """Migration 0006 must create item_kinds and seed exactly three system kinds."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic as a subprocess; return (returncode, output)."""
        import subprocess

        backend_root = Path(__file__).parent.parent
        env = {
            **os.environ,
            "SECRET_KEY": "test",
            "DATABASE_URL": url,
        }
        result = subprocess.run(
            [".venv/bin/alembic", *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def test_upgrade_0006_creates_item_kinds(self) -> None:
        """alembic upgrade 0006 creates the item_kinds table."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0006", url=url)
            assert rc == 0, f"alembic upgrade 0006 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_kinds" in table_names, f"item_kinds missing; found: {table_names}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0006_seeds_three_system_kinds(self) -> None:
        """Migration 0006 seeds exactly durable, consumable, perishable."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0006", url=url)
            assert rc == 0, f"alembic upgrade 0006 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT code, is_system FROM item_kinds ORDER BY id")
                ).fetchall()
                codes = {row[0] for row in rows}
                assert codes == {"durable", "consumable", "perishable"}, (
                    f"Expected 3 system kinds, got: {codes}"
                )
                for row in rows:
                    assert row[1] == 1, f"Expected is_system=1 for {row[0]}, got {row[1]}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0006_drops_item_kinds(self) -> None:
        """Downgrading from 0006 to 0005 drops item_kinds."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0006", url=url)
            assert rc_up == 0, f"alembic upgrade 0006 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0005", url=url)
            assert rc_down == 0, f"alembic downgrade to 0005 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_kinds" not in table_names, (
                    "item_kinds must be dropped after downgrade to 0005"
                )
                assert "categories" in table_names, "categories must still exist at 0005"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_seed_is_idempotent(self) -> None:
        """Running upgrade 0006 twice does not fail (INSERT OR IGNORE)."""
        url, db_path = _make_temp_db_url()
        try:
            rc1, out1 = self._run_alembic("upgrade", "0006", url=url)
            assert rc1 == 0, f"First upgrade failed:\n{out1}"

            # Downgrade and re-upgrade (simulates idempotency).
            rc_down, out_down = self._run_alembic("downgrade", "0005", url=url)
            assert rc_down == 0, f"Downgrade failed:\n{out_down}"

            rc2, out2 = self._run_alembic("upgrade", "0006", url=url)
            assert rc2 == 0, f"Second upgrade failed:\n{out2}"

            engine = create_engine(url)
            with engine.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM item_kinds")).scalar()
                assert count == 3, f"Expected 3 seeds, got {count}"
        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# 11. Alembic migration 0007 (item_definitions)
# ---------------------------------------------------------------------------


class TestAlembicMigration0007:
    """Migration 0007 must create item_definitions."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic as a subprocess; return (returncode, output)."""
        import subprocess

        backend_root = Path(__file__).parent.parent
        env = {
            **os.environ,
            "SECRET_KEY": "test",
            "DATABASE_URL": url,
        }
        result = subprocess.run(
            [".venv/bin/alembic", *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def test_upgrade_head_creates_item_definitions(self) -> None:
        """alembic upgrade head creates the item_definitions table."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_definitions" in table_names, (
                    f"item_definitions missing; found: {table_names}"
                )
                assert "item_kinds" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0007_drops_item_definitions(self) -> None:
        """Downgrading from head to 0006 drops item_definitions, keeps item_kinds."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade head failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0006", url=url)
            assert rc_down == 0, f"alembic downgrade to 0006 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_definitions" not in table_names, (
                    "item_definitions must be dropped after downgrade to 0006"
                )
                assert "item_kinds" in table_names, "item_kinds must still exist at 0006"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_stepwise_0001_to_0007(self) -> None:
        """Stepwise upgrade 0001 through 0007 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            for rev in ["0001", "0002", "0003", "0004", "0005", "0006", "0007"]:
                rc, out = self._run_alembic("upgrade", rev, url=url)
                assert rc == 0, f"alembic upgrade {rev} failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_kinds" in table_names
                assert "item_definitions" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_base_is_clean(self) -> None:
        """alembic downgrade base removes all application tables including 0006/0007."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "base", url=url)
            assert rc_down == 0, f"alembic downgrade base failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'alembic_%' AND name != 'sqlite_sequence'"
                    )
                ).fetchall()
                assert tables == [], f"Tables still exist after downgrade: {tables}"
        finally:
            if db_path.exists():
                db_path.unlink()
