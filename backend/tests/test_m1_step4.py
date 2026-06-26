"""M1 Step 4 tests: Stock Instance + container-as-item bridge.

Required coverage (easy-to-get-wrong logic, per M1.md §5 / §9 Step 4):

- serial ⇒ quantity = 1 rejected at the service layer (422).
- serial ⇒ quantity = 1 enforced at the DB layer (CHECK → IntegrityError).
- Partial-uniqueness: duplicate (definition_id, serial) rejected; same serial
  under a different definition is allowed; two NULL serials coexist.
- Default-location resolution: omitting location → definition's
  default_location_id; stays NULL when the definition has none.
- Container-as-item:
    - item_instance_id is unique (one instance ↔ one location).
    - Linking a non-existent instance fails (404).
    - Location delete-guard returns 409 when the location is linked as a
      container OR holds assigned instances.
- Migration 0008 up/down including the batch-alter on locations.
- Basic CRUD: create / read / update / delete instances; list + search.
"""

import os
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1step4_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


def _make_fresh_session() -> Session:
    """In-memory SQLite session with all models registered and FK enforcement on."""
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
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)

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


def _seed_minimal(session: Session) -> dict[str, int]:
    """Seed an in-memory DB with the minimum needed for instance tests.

    Returns a dict with keys: durable_kind_id, loc_a_id, loc_b_id,
    def_with_loc_id, def_no_loc_id.
    """
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.location import Location

    kind = ItemKind(code="durable", name="Durable", is_system=True)
    session.add(kind)
    session.flush()

    loc_a = Location(name="Garage")
    loc_b = Location(name="Kitchen")
    session.add(loc_a)
    session.add(loc_b)
    session.flush()

    def_with_loc = ItemDefinition(
        name="Cordless Drill", kind_id=kind.id, default_location_id=loc_a.id
    )
    def_no_loc = ItemDefinition(name="Mystery Box", kind_id=kind.id)
    session.add(def_with_loc)
    session.add(def_no_loc)
    session.flush()
    session.commit()

    return {
        "durable_kind_id": kind.id,
        "loc_a_id": loc_a.id,
        "loc_b_id": loc_b.id,
        "def_with_loc_id": def_with_loc.id,
        "def_no_loc_id": def_no_loc.id,
    }


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
    """Fresh in-memory SQLite session, seeded with kinds / locations / defs."""
    session = _make_fresh_session()
    _seed_minimal(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB; sets SECRET_KEY, ENVIRONMENT=test, DATABASE_URL."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """TestClient with a full schema and an authenticated admin session."""
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
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)

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

            # Seed the three system kinds so definition endpoints work.
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


def _create_location(client: TestClient, name: str, **kwargs: object) -> dict:  # type: ignore[type-arg]
    payload = {"name": name, **kwargs}
    resp = client.post("/api/locations", json=payload)
    assert resp.status_code == 201, f"create_location failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_definition(
    client: TestClient,
    name: str,
    *,
    default_location_id: int | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if default_location_id is not None:
        payload["default_location_id"] = default_location_id
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    location_id: int | None = None,
    quantity: str | None = None,
    serial: str | None = None,
    model_number: str | None = None,
    manufacturer: str | None = None,
    purchase_price: str | None = None,
    expect_status: int = 201,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"definition_id": definition_id}  # type: ignore[type-arg]
    if location_id is not None:
        payload["location_id"] = location_id
    if quantity is not None:
        payload["quantity"] = quantity
    if serial is not None:
        payload["serial"] = serial
    if model_number is not None:
        payload["model_number"] = model_number
    if manufacturer is not None:
        payload["manufacturer"] = manufacturer
    if purchase_price is not None:
        payload["purchase_price"] = purchase_price
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == expect_status, (
        f"create_instance failed: {resp.status_code} {resp.json()}"
    )
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Basic CRUD
# ---------------------------------------------------------------------------


class TestInstanceCRUD:
    """Basic CRUD for stock instances."""

    def test_create_minimal(self, test_client: TestClient) -> None:
        """POST /instances with definition_id only → created with quantity=1."""
        defn = _create_definition(test_client, "Hammer")
        data = _create_instance(test_client, defn["id"])
        assert data["definition_id"] == defn["id"]
        assert Decimal(data["quantity"]) == Decimal("1")
        assert data["serial"] is None
        assert data["location_id"] is None
        assert "id" in data
        assert "created_at" in data

    def test_create_with_all_fields(self, test_client: TestClient) -> None:
        """POST /instances with all fields stores them correctly."""
        defn = _create_definition(test_client, "Cordless Drill")
        loc = _create_location(test_client, "Garage")
        data = _create_instance(
            test_client,
            defn["id"],
            location_id=loc["id"],
            serial="SN-12345",
            model_number="DR-200X",
            manufacturer="Bosch",
            purchase_price="199.99",
        )
        assert data["serial"] == "SN-12345"
        assert data["model_number"] == "DR-200X"
        assert data["manufacturer"] == "Bosch"
        assert Decimal(data["purchase_price"]) == Decimal("199.99")
        assert data["location_id"] == loc["id"]
        assert Decimal(data["quantity"]) == Decimal("1")

    def test_get_instance_by_id(self, test_client: TestClient) -> None:
        """GET /instances/{id} returns the instance."""
        defn = _create_definition(test_client, "Widget")
        created = _create_instance(test_client, defn["id"])
        resp = test_client.get(f"/api/instances/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_instance_404(self, test_client: TestClient) -> None:
        """GET /instances/{id} returns 404 for a non-existent id."""
        resp = test_client.get("/api/instances/9999")
        assert resp.status_code == 404

    def test_list_instances_empty(self, test_client: TestClient) -> None:
        """GET /instances returns [] when no instances exist."""
        resp = test_client.get("/api/instances")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_quantity_ignored_via_patch(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} ignores 'quantity' in body (M2: quantity is ledger-derived).

        Quantity can no longer be changed via PATCH — it is derived from the
        movement ledger.  Sending 'quantity' in the PATCH body is silently
        ignored; the instance's quantity remains at the ledger-derived value.
        """
        defn = _create_definition(test_client, "Nails")
        created = _create_instance(test_client, defn["id"])
        original_qty = created["quantity"]
        resp = test_client.patch(f"/api/instances/{created['id']}", json={"quantity": "100.000000"})
        assert resp.status_code == 200
        # quantity stays at the original ledger-derived value (initial intake = 1 by default)
        assert resp.json()["quantity"] == original_qty

    def test_update_manufacturer(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} can update manufacturer."""
        defn = _create_definition(test_client, "Saw")
        created = _create_instance(test_client, defn["id"])
        resp = test_client.patch(f"/api/instances/{created['id']}", json={"manufacturer": "DeWalt"})
        assert resp.status_code == 200
        assert resp.json()["manufacturer"] == "DeWalt"

    def test_delete_instance(self, test_client: TestClient) -> None:
        """DELETE /instances/{id} returns 204 and the instance is gone."""
        defn = _create_definition(test_client, "Temp")
        created = _create_instance(test_client, defn["id"])
        resp = test_client.delete(f"/api/instances/{created['id']}")
        assert resp.status_code == 204
        assert test_client.get(f"/api/instances/{created['id']}").status_code == 404

    def test_create_nonexistent_definition_404(self, test_client: TestClient) -> None:
        """POST /instances with a non-existent definition_id returns 404."""
        resp = test_client.post("/api/instances", json={"definition_id": 9999})
        assert resp.status_code == 404

    def test_create_nonexistent_location_404(self, test_client: TestClient) -> None:
        """POST /instances with a non-existent location_id returns 404."""
        defn = _create_definition(test_client, "Widget")
        resp = test_client.post(
            "/api/instances", json={"definition_id": defn["id"], "location_id": 9999}
        )
        assert resp.status_code == 404

    def test_response_has_all_fields(self, test_client: TestClient) -> None:
        """InstanceResponse includes all expected fields (M2: includes stock_level, received_at)."""
        defn = _create_definition(test_client, "Widget")
        data = _create_instance(test_client, defn["id"])
        for field in [
            "id",
            "definition_id",
            "location_id",
            "quantity",
            "stock_level",
            "received_at",
            "serial",
            "model_number",
            "manufacturer",
            "warranty_expires",
            "warranty_details",
            "purchase_price",
            "purchase_date",
            "purchase_source",
            "created_at",
        ]:
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# 2. serial ⇒ quantity = 1 (easy-to-get-wrong — BOTH layers required)
# ---------------------------------------------------------------------------


class TestSerialQtyConstraint:
    """serial ⇒ quantity = 1 must be rejected at BOTH the service and DB layers."""

    # ------ Service layer (422) ------

    def test_create_serial_qty_gt_1_rejected_422(self, test_client: TestClient) -> None:
        """POST /instances with serial and quantity > 1 is rejected with 422."""
        defn = _create_definition(test_client, "Drill")
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn["id"], "serial": "SN-001", "quantity": "2"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for serial+qty>1, got {resp.status_code}: {resp.json()}"
        )

    def test_create_serial_qty_fractional_rejected_422(self, test_client: TestClient) -> None:
        """POST /instances with serial and quantity = 0.5 is rejected with 422."""
        defn = _create_definition(test_client, "Drill")
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn["id"], "serial": "SN-002", "quantity": "0.5"},
        )
        assert resp.status_code == 422

    def test_update_qty_via_patch_ignored_for_serial_instance(
        self, test_client: TestClient
    ) -> None:
        """PATCH /instances/{id} with 'quantity' is silently ignored (M2 contract).

        In M2, quantity is ledger-derived and cannot be changed via PATCH.
        Sending 'quantity' in the PATCH body is a no-op; the serialized lot's
        quantity remains at 1 (from the initial intake movement).
        """
        defn = _create_definition(test_client, "Saw")
        inst = _create_instance(test_client, defn["id"], serial="SN-SAW-001")
        # Sending quantity=5 in PATCH body is silently ignored
        resp = test_client.patch(f"/api/instances/{inst['id']}", json={"quantity": "5"})
        # Should succeed (body field is ignored)
        assert resp.status_code == 200
        # Quantity stays at 1 (the ledger-derived value)
        assert Decimal(resp.json()["quantity"]) == Decimal("1")

    def test_update_serial_on_qty_gt_1_instance_rejected_422(self, test_client: TestClient) -> None:
        """PATCH /instances/{id}: adding serial to an instance with qty > 1 → 422."""
        defn = _create_definition(test_client, "Screws")
        inst = _create_instance(test_client, defn["id"], quantity="50")
        resp = test_client.patch(f"/api/instances/{inst['id']}", json={"serial": "SN-SCREW-1"})
        assert resp.status_code == 422

    def test_create_serial_with_qty_1_accepted(self, test_client: TestClient) -> None:
        """POST /instances with serial and quantity = 1 is accepted."""
        defn = _create_definition(test_client, "Angle Grinder")
        data = _create_instance(
            test_client, defn["id"], serial="SN-AG-001", quantity="1", expect_status=201
        )
        assert data["serial"] == "SN-AG-001"
        assert Decimal(data["quantity"]) == Decimal("1")

    def test_create_serial_without_quantity_defaults_to_1(self, test_client: TestClient) -> None:
        """POST /instances with serial and no quantity → quantity defaults to 1."""
        defn = _create_definition(test_client, "Jigsaw")
        data = _create_instance(test_client, defn["id"], serial="SN-JIG-001", expect_status=201)
        assert Decimal(data["quantity"]) == Decimal("1")

    # ------ Service layer unit tests ------

    def test_service_rejects_serial_qty_gt_1(self, db_session: Session) -> None:
        """StockInstanceService raises AppError 422 for serial + quantity > 1 (unit test)."""
        from sqlalchemy import select

        from app.core.errors import AppError, ErrorCode
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        # Reuse the seeded durable kind (already in db_session via _seed_minimal).
        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="ServiceWidget", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(
                InstanceCreate(definition_id=defn.id, serial="SN-001", quantity=Decimal("3"))
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE

    # ------ DB layer (CHECK constraint) ------

    def test_db_check_constraint_rejects_serial_qty_gt_1(self, db_session: Session) -> None:
        """Direct bad INSERT into stock_instances raises IntegrityError (DB CHECK)."""
        import sqlalchemy.exc
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        # Reuse the seeded durable kind.
        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="DBCheckWidget", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        # Bypass the service and insert directly — the DB CHECK must fire.
        bad_row = StockInstance(
            definition_id=defn.id,
            serial="SN-BAD",
            quantity=Decimal("3"),
        )
        db_session.add(bad_row)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# 3. Partial-uniqueness on (definition_id, serial) (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestSerialPartialUniqueness:
    """Partial unique index on (definition_id, serial) WHERE serial IS NOT NULL."""

    def test_duplicate_serial_same_definition_rejected(self, test_client: TestClient) -> None:
        """Creating two instances with the same serial under the same definition returns 409."""
        defn = _create_definition(test_client, "Drill")
        _create_instance(test_client, defn["id"], serial="SN-DUPE", expect_status=201)
        # The second insert must be rejected with 409 (uniqueness conflict, §4.2).
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn["id"], "serial": "SN-DUPE"},
        )
        assert resp.status_code == 409, (
            f"Expected 409 for duplicate serial, got {resp.status_code}: {resp.json()}"
        )

    def test_update_to_existing_serial_returns_409(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} that would create a serial collision returns 409."""
        defn = _create_definition(test_client, "Saw")
        _create_instance(test_client, defn["id"], serial="SN-TAKEN", expect_status=201)
        inst = _create_instance(test_client, defn["id"], serial="SN-OTHER", expect_status=201)
        # Try to rename inst's serial to the already-taken one.
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"serial": "SN-TAKEN"},
        )
        assert resp.status_code == 409, (
            f"Expected 409 for serial collision on update, got {resp.status_code}: {resp.json()}"
        )

    def test_update_own_serial_unchanged_does_not_409(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} that re-sends the same serial does NOT return 409."""
        defn = _create_definition(test_client, "Wrench")
        inst = _create_instance(test_client, defn["id"], serial="SN-SELF", expect_status=201)
        # Patching with the same serial must succeed (self-collision must be excluded).
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"serial": "SN-SELF"},
        )
        assert resp.status_code == 200, (
            f"Expected 200 when updating to own serial, got {resp.status_code}: {resp.json()}"
        )

    def test_same_serial_different_definition_allowed(self, test_client: TestClient) -> None:
        """The same serial string can exist under two different definitions."""
        defn_a = _create_definition(test_client, "Drill A")
        defn_b = _create_definition(test_client, "Drill B")
        inst_a = _create_instance(test_client, defn_a["id"], serial="SN-SHARED", expect_status=201)
        inst_b = _create_instance(test_client, defn_b["id"], serial="SN-SHARED", expect_status=201)
        assert inst_a["id"] != inst_b["id"]
        assert inst_a["serial"] == inst_b["serial"] == "SN-SHARED"

    def test_two_null_serials_coexist(self, test_client: TestClient) -> None:
        """Two instances of the same definition with NULL serial both succeed."""
        defn = _create_definition(test_client, "Nails")
        inst_1 = _create_instance(test_client, defn["id"], quantity="100", expect_status=201)
        inst_2 = _create_instance(test_client, defn["id"], quantity="200", expect_status=201)
        assert inst_1["id"] != inst_2["id"]
        assert inst_1["serial"] is None
        assert inst_2["serial"] is None

    def test_partial_uniqueness_db_level(self, db_session: Session) -> None:
        """DB-level partial unique index rejects duplicate (definition_id, serial)."""
        import sqlalchemy.exc
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="UniqueWidget", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        row1 = StockInstance(definition_id=defn.id, serial="SN-SAME")
        db_session.add(row1)
        db_session.flush()
        db_session.commit()

        row2 = StockInstance(definition_id=defn.id, serial="SN-SAME")
        db_session.add(row2)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_session.flush()

    def test_two_nulls_db_level_allowed(self, db_session: Session) -> None:
        """DB-level partial unique index allows two NULL serials for same definition."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="BulkItem", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        row1 = StockInstance(definition_id=defn.id, quantity=Decimal("10"))
        row2 = StockInstance(definition_id=defn.id, quantity=Decimal("20"))
        db_session.add(row1)
        db_session.add(row2)
        db_session.flush()  # Must not raise
        db_session.commit()

        # Both rows persisted.
        from sqlalchemy import select

        from app.models.stock_instance import StockInstance as SI

        rows = db_session.scalars(select(SI).where(SI.definition_id == defn.id)).all()
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# 4. Default-location resolution (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestDefaultLocationResolution:
    """Creating an instance without location_id resolves from definition's default."""

    def test_omit_location_uses_definition_default(self, test_client: TestClient) -> None:
        """POST /instances without location_id → uses definition's default_location_id."""
        loc = _create_location(test_client, "Garage")
        defn = _create_definition(test_client, "Drill", default_location_id=loc["id"])
        # Do NOT provide location_id.
        data = _create_instance(test_client, defn["id"], expect_status=201)
        assert data["location_id"] == loc["id"], (
            f"Expected location_id={loc['id']}, got {data['location_id']}"
        )

    def test_omit_location_stays_null_when_definition_has_none(
        self, test_client: TestClient
    ) -> None:
        """POST /instances without location, definition has no default → location_id = NULL."""
        defn = _create_definition(test_client, "Mystery Box")  # no default_location_id
        data = _create_instance(test_client, defn["id"], expect_status=201)
        assert data["location_id"] is None

    def test_explicit_location_overrides_definition_default(self, test_client: TestClient) -> None:
        """POST /instances with explicit location_id → uses that, not definition's default."""
        default_loc = _create_location(test_client, "Garage")
        override_loc = _create_location(test_client, "Kitchen")
        defn = _create_definition(test_client, "Drill", default_location_id=default_loc["id"])
        data = _create_instance(
            test_client, defn["id"], location_id=override_loc["id"], expect_status=201
        )
        assert data["location_id"] == override_loc["id"]

    def test_service_default_location_resolution_unit(self, db_session: Session) -> None:
        """StockInstanceService.create resolves definition's default_location_id (unit test)."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.location import Location
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        loc = Location(name="ServiceGarage")
        db_session.add(loc)
        db_session.flush()
        defn = ItemDefinition(name="ServiceDrill", kind_id=kind.id, default_location_id=loc.id)
        db_session.add(defn)
        db_session.flush()

        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        assert inst.location_id == loc.id

    def test_service_null_stays_null_when_no_default(self, db_session: Session) -> None:
        """StockInstanceService.create leaves location_id = NULL when definition has none."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="ServiceNoLoc", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        assert inst.location_id is None


# ---------------------------------------------------------------------------
# 5. Container-as-item (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestContainerAsItem:
    """Location.item_instance_id uniqueness and delete-guard when container or occupied."""

    def test_link_location_to_instance(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} with item_instance_id links the container."""
        loc = _create_location(test_client, "Toolbox")
        defn = _create_definition(test_client, "Toolbox Asset")
        inst = _create_instance(test_client, defn["id"])

        resp = test_client.patch(
            f"/api/locations/{loc['id']}", json={"item_instance_id": inst["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["item_instance_id"] == inst["id"]

    def test_unlink_location_from_instance(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} with item_instance_id=null unlinks the container."""
        loc = _create_location(test_client, "Toolbox")
        defn = _create_definition(test_client, "Toolbox Asset")
        inst = _create_instance(test_client, defn["id"])

        # Link first.
        test_client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": inst["id"]})

        # Now unlink.
        resp = test_client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": None})
        assert resp.status_code == 200
        assert resp.json()["item_instance_id"] is None

    def test_item_instance_id_unique_two_locations_same_instance(
        self, test_client: TestClient
    ) -> None:
        """Linking a second location to the same instance fails with 409."""
        loc_a = _create_location(test_client, "Toolbox")
        loc_b = _create_location(test_client, "Shelf")
        defn = _create_definition(test_client, "Toolbox Asset")
        inst = _create_instance(test_client, defn["id"])

        # Link loc_a → inst.
        resp_a = test_client.patch(
            f"/api/locations/{loc_a['id']}", json={"item_instance_id": inst["id"]}
        )
        assert resp_a.status_code == 200

        # Attempt to link loc_b → same inst → must fail 409.
        resp_b = test_client.patch(
            f"/api/locations/{loc_b['id']}", json={"item_instance_id": inst["id"]}
        )
        assert resp_b.status_code == 409, (
            f"Expected 409 for duplicate item_instance_id link, got {resp_b.status_code}"
        )

    def test_link_nonexistent_instance_fails_404(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} with a non-existent instance_id returns 404."""
        loc = _create_location(test_client, "Garage")
        resp = test_client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": 9999})
        assert resp.status_code == 404

    def test_delete_location_linked_as_container_fails_409(self, test_client: TestClient) -> None:
        """DELETE /locations/{id} returns 409 when the location is linked as a container."""
        loc = _create_location(test_client, "Toolbox")
        defn = _create_definition(test_client, "Toolbox Asset")
        inst = _create_instance(test_client, defn["id"])

        # Link the location as a container.
        test_client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": inst["id"]})

        # Attempting to delete the location must fail with 409.
        resp = test_client.delete(f"/api/locations/{loc['id']}")
        assert resp.status_code == 409, (
            f"Expected 409 when deleting a location linked as container, got {resp.status_code}"
        )

    def test_delete_location_with_assigned_instances_fails_409(
        self, test_client: TestClient
    ) -> None:
        """DELETE /locations/{id} returns 409 when instances are assigned to it."""
        loc = _create_location(test_client, "Garage")
        defn = _create_definition(test_client, "Drill")
        # Assign an instance to the location.
        _create_instance(test_client, defn["id"], location_id=loc["id"])

        resp = test_client.delete(f"/api/locations/{loc['id']}")
        assert resp.status_code == 409, (
            f"Expected 409 when deleting a location with assigned instances, got {resp.status_code}"
        )

    def test_delete_empty_unlinked_location_succeeds(self, test_client: TestClient) -> None:
        """DELETE /locations/{id} on a location with no children/instances/link succeeds."""
        loc = _create_location(test_client, "Empty Room")
        resp = test_client.delete(f"/api/locations/{loc['id']}")
        assert resp.status_code == 204

    def test_service_container_uniqueness_unit(self, db_session: Session) -> None:
        """LocationService raises AppError 409 when trying to link two locations to the same instance."""
        from sqlalchemy import select

        from app.core.errors import AppError, ErrorCode
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.location import Location
        from app.models.stock_instance import StockInstance
        from app.schemas.location import LocationUpdate
        from app.services.location import LocationService

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="ContainerAsset", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()
        inst = StockInstance(definition_id=defn.id)
        db_session.add(inst)
        db_session.flush()

        loc_a = Location(name="LocA")
        loc_b = Location(name="LocB")
        db_session.add(loc_a)
        db_session.add(loc_b)
        db_session.flush()

        svc = LocationService(db_session)
        # Link loc_a.
        svc.update(loc_a.id, LocationUpdate(item_instance_id=inst.id))
        db_session.commit()

        # Attempt to link loc_b to the same instance.
        with pytest.raises(AppError) as exc_info:
            svc.update(loc_b.id, LocationUpdate(item_instance_id=inst.id))
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.LOCATION_CONTAINER_LINK_CONFLICT

    def test_location_response_includes_item_instance_id(self, test_client: TestClient) -> None:
        """GET /locations/{id} response includes item_instance_id field."""
        loc = _create_location(test_client, "Garage")
        resp = test_client.get(f"/api/locations/{loc['id']}")
        assert resp.status_code == 200
        assert "item_instance_id" in resp.json()

    def test_tree_nodes_include_item_instance_id(self, test_client: TestClient) -> None:
        """GET /locations/tree response nodes include item_instance_id field."""
        _create_location(test_client, "Root")
        resp = test_client.get("/api/locations/tree")
        assert resp.status_code == 200
        for node in resp.json():
            assert "item_instance_id" in node


# ---------------------------------------------------------------------------
# 6. Instance search and filters
# ---------------------------------------------------------------------------


class TestInstanceSearch:
    """GET /instances with q / definition_id / location_id filters."""

    def test_list_returns_all(self, test_client: TestClient) -> None:
        """GET /instances returns all instances when no filters applied."""
        defn = _create_definition(test_client, "Widget")
        _create_instance(test_client, defn["id"])
        _create_instance(test_client, defn["id"])
        resp = test_client.get("/api/instances")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_filter_by_definition_id(self, test_client: TestClient) -> None:
        """GET /instances?definition_id=X returns only instances of that definition."""
        defn_a = _create_definition(test_client, "Drill")
        defn_b = _create_definition(test_client, "Saw")
        _create_instance(test_client, defn_a["id"])
        _create_instance(test_client, defn_b["id"])

        resp = test_client.get(f"/api/instances?definition_id={defn_a['id']}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["definition_id"] == defn_a["id"]

    def test_filter_by_location_id(self, test_client: TestClient) -> None:
        """GET /instances?location_id=X returns only instances at that location."""
        loc = _create_location(test_client, "Garage")
        defn = _create_definition(test_client, "Drill")
        _create_instance(test_client, defn["id"], location_id=loc["id"])
        _create_instance(test_client, defn["id"])  # no location

        resp = test_client.get(f"/api/instances?location_id={loc['id']}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["location_id"] == loc["id"]

    def test_search_by_serial(self, test_client: TestClient) -> None:
        """GET /instances?q=<serial_fragment> matches by serial (case-insensitive)."""
        defn = _create_definition(test_client, "Drill")
        _create_instance(test_client, defn["id"], serial="SN-12345")
        _create_instance(test_client, defn["id"], serial="SN-99999")

        resp = test_client.get("/api/instances?q=12345")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["serial"] == "SN-12345"

    def test_search_by_manufacturer_case_insensitive(self, test_client: TestClient) -> None:
        """GET /instances?q=<manufacturer> is case-insensitive."""
        defn = _create_definition(test_client, "Drill")
        _create_instance(test_client, defn["id"], serial="SN-A", manufacturer="Bosch")
        _create_instance(test_client, defn["id"], serial="SN-B", manufacturer="DeWalt")

        for q in ["bosch", "BOSCH", "Bosch"]:
            resp = test_client.get(f"/api/instances?q={q}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) == 1, f"q={q!r} got {results}"
            assert results[0]["manufacturer"] == "Bosch"

    def test_search_no_match_returns_empty(self, test_client: TestClient) -> None:
        """GET /instances?q=no_match returns []."""
        defn = _create_definition(test_client, "Widget")
        _create_instance(test_client, defn["id"])
        resp = test_client.get("/api/instances?q=zzz_no_match")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. Authentication guard
# ---------------------------------------------------------------------------


class TestInstancesRequireAuth:
    """All /instances endpoints require a valid session."""

    def test_list_requires_auth(self, temp_db: Path) -> None:  # noqa: ARG002
        """GET /instances without a session returns 401."""
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
        importlib.reload(media_file_mod)
        importlib.reload(attachment_mod)
        importlib.reload(tag_mod)

        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/instances")
            assert resp.status_code == 401
        drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# 8. Migration 0008 up/down (incl. batch-alter on locations)
# ---------------------------------------------------------------------------


class TestAlembicMigration0008:
    """Migration 0008 creates stock_instances and batch-alters locations."""

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

    def test_upgrade_0008_creates_stock_instances(self) -> None:
        """alembic upgrade 0008 creates the stock_instances table."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0008", url=url)
            assert rc == 0, f"alembic upgrade 0008 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_instances" in table_names, (
                    f"stock_instances missing; found: {table_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0008_adds_item_instance_id_to_locations(self) -> None:
        """alembic upgrade 0008 adds item_instance_id column to locations."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0008", url=url)
            assert rc == 0, f"alembic upgrade 0008 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(locations)")).fetchall()
                col_names = {row[1] for row in cols}
                assert "item_instance_id" in col_names, (
                    f"item_instance_id missing from locations; found: {col_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0008_check_constraint_enforced(self) -> None:
        """After upgrade 0008, the CHECK constraint rejects serial + qty > 1."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{out}"

            engine = create_engine(url, connect_args={"check_same_thread": False})

            @event.listens_for(engine, "connect")
            def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
                import sqlite3

                if isinstance(dbapi_conn, sqlite3.Connection):
                    dbapi_conn.execute("PRAGMA foreign_keys=ON")

            # Seed minimum data: a definition (migration 0006 already seeds 'durable' kind).
            with engine.begin() as conn:
                # Get the durable kind's id.
                durable_id = conn.execute(
                    text("SELECT id FROM item_kinds WHERE code='durable'")
                ).scalar()
                conn.execute(
                    text(
                        f"INSERT INTO item_definitions (name, kind_id, unit) "
                        f"VALUES ('Widget', {durable_id}, 'pcs')"
                    )
                )

            # Get the definition's id (just inserted above).
            with engine.begin() as conn:
                defn_id = conn.execute(
                    text("SELECT id FROM item_definitions WHERE name='Widget'")
                ).scalar()

            # Try to insert a row that violates the CHECK (serial set, qty != 1).
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(str(db_path))
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                with pytest.raises(_sqlite3.IntegrityError):
                    conn.execute(
                        f"INSERT INTO stock_instances (definition_id, serial, quantity) "
                        f"VALUES ({defn_id}, 'SN-BAD', 5)"
                    )
            finally:
                conn.close()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0008_drops_stock_instances_and_column(self) -> None:
        """Downgrade from 0008 to 0007 drops stock_instances and item_instance_id."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0008", url=url)
            assert rc_up == 0, f"alembic upgrade 0008 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0007", url=url)
            assert rc_down == 0, f"alembic downgrade to 0007 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_instances" not in table_names, (
                    "stock_instances must be dropped after downgrade to 0007"
                )
                assert "item_definitions" in table_names, (
                    "item_definitions must still exist at 0007"
                )

                # item_instance_id column must be gone from locations.
                cols = conn.execute(text("PRAGMA table_info(locations)")).fetchall()
                col_names = {row[1] for row in cols}
                assert "item_instance_id" not in col_names, (
                    "item_instance_id must be dropped from locations after downgrade"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_stepwise_upgrade_0001_to_0008(self) -> None:
        """Stepwise upgrade 0001 through 0008 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            for rev in ["0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008"]:
                rc, out = self._run_alembic("upgrade", rev, url=url)
                assert rc == 0, f"alembic upgrade {rev} failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_instances" in table_names
                assert "item_definitions" in table_names
                assert "locations" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_base_removes_all_tables(self) -> None:
        """alembic downgrade base removes all application tables."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade head failed:\n{out_up}"

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
