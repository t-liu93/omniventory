"""Tests for the repository-layer null-on-non-nullable PATCH guard.

Coverage
--------
Guard unit tests (reject_null_on_non_nullable):
    - Raises AppError (validation.invalid_input, 422) for None on a NOT NULL column.
    - No-op for None on a nullable column.
    - No-op for a non-None value on a NOT NULL column.
    - No-op for a key that is not a mapped column (e.g. relationship name).
    - No-op for an unknown key.

Maintenance-schedule HTTP integration (PATCH /maintenance-schedules/{id}):
    - Explicit null for NOT NULL column (name) → 422 validation.invalid_input,
      NOT 500, and the row is unchanged.
    - Explicit null for NOT NULL column (next_due_date) → 422.
    - Explicit null for NOT NULL column (is_active) → 422.
    - Explicit null for nullable column (lead_days) → 200 (clears the override).
    - Explicit null for nullable column (notes) → 200 (clears the note).
    - Normal valid PATCH is unaffected (guard is a no-op for non-None values).

Shopping-list HTTP integration (PATCH /shopping-list/{id}):
    - All three client-patchable fields (name, desired_quantity, note) are
      nullable in the DB — nulling them via PATCH → 200 (guard is a no-op).
    - The source NOT NULL column is not exposed through PATCH at all.
    - Normal valid PATCH is unaffected.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Guard unit tests — no DB required
# ---------------------------------------------------------------------------


class TestRejectNullOnNonNullable:
    """Direct unit tests for reject_null_on_non_nullable (no DB, no HTTP)."""

    def _guard(self, instance: object, fields: dict) -> None:  # type: ignore[type-arg]
        from app.repositories._update_guard import reject_null_on_non_nullable

        reject_null_on_non_nullable(instance, fields)

    def _make_schedule(self) -> object:
        """Return an unmapped MaintenanceSchedule instance (no DB session needed)."""
        from app.models.maintenance_schedule import MaintenanceSchedule

        return MaintenanceSchedule.__new__(MaintenanceSchedule)

    def _make_shopping_item(self) -> object:
        """Return an unmapped ShoppingListItem instance (no DB session needed)."""
        from app.models.shopping_list_item import ShoppingListItem

        return ShoppingListItem.__new__(ShoppingListItem)

    # ------------------------------------------------------------------
    # 1. Raises for None on a NOT NULL column
    # ------------------------------------------------------------------

    def test_raises_for_null_on_not_null_column(self) -> None:
        """None on MaintenanceSchedule.name (NOT NULL) → 422 AppError."""
        from app.core.errors import AppError, ErrorCode

        sched = self._make_schedule()
        with pytest.raises(AppError) as exc_info:
            self._guard(sched, {"name": None})
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.status_code == 422
        assert err.params == {"field": "name"}

    def test_raises_for_null_on_next_due_date(self) -> None:
        """None on MaintenanceSchedule.next_due_date (NOT NULL) → 422."""
        from app.core.errors import AppError, ErrorCode

        sched = self._make_schedule()
        with pytest.raises(AppError) as exc_info:
            self._guard(sched, {"next_due_date": None})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert exc_info.value.status_code == 422

    def test_raises_for_null_on_is_active(self) -> None:
        """None on MaintenanceSchedule.is_active (NOT NULL) → 422."""
        from app.core.errors import AppError, ErrorCode

        sched = self._make_schedule()
        with pytest.raises(AppError) as exc_info:
            self._guard(sched, {"is_active": None})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert exc_info.value.status_code == 422

    def test_raises_for_null_on_interval_unit(self) -> None:
        """None on MaintenanceSchedule.interval_unit (NOT NULL) → 422."""
        from app.core.errors import AppError

        sched = self._make_schedule()
        with pytest.raises(AppError):
            self._guard(sched, {"interval_unit": None})

    def test_raises_for_null_on_interval_count(self) -> None:
        """None on MaintenanceSchedule.interval_count (NOT NULL) → 422."""
        from app.core.errors import AppError

        sched = self._make_schedule()
        with pytest.raises(AppError):
            self._guard(sched, {"interval_count": None})

    # ------------------------------------------------------------------
    # 2. No-op for None on a nullable column
    # ------------------------------------------------------------------

    def test_noop_for_null_on_nullable_column(self) -> None:
        """None on MaintenanceSchedule.lead_days (nullable) → no error."""
        sched = self._make_schedule()
        self._guard(sched, {"lead_days": None})  # must not raise

    def test_noop_for_null_on_notes(self) -> None:
        """None on MaintenanceSchedule.notes (nullable) → no error."""
        sched = self._make_schedule()
        self._guard(sched, {"notes": None})  # must not raise

    def test_noop_for_null_on_shopping_list_name(self) -> None:
        """None on ShoppingListItem.name (nullable) → no error."""
        item = self._make_shopping_item()
        self._guard(item, {"name": None})  # must not raise

    def test_noop_for_null_on_shopping_list_desired_quantity(self) -> None:
        """None on ShoppingListItem.desired_quantity (nullable) → no error."""
        item = self._make_shopping_item()
        self._guard(item, {"desired_quantity": None})  # must not raise

    def test_noop_for_null_on_shopping_list_note(self) -> None:
        """None on ShoppingListItem.note (nullable) → no error."""
        item = self._make_shopping_item()
        self._guard(item, {"note": None})  # must not raise

    # ------------------------------------------------------------------
    # 3. No-op for a non-None value on a NOT NULL column
    # ------------------------------------------------------------------

    def test_noop_for_non_none_on_not_null_column(self) -> None:
        """Non-None value on MaintenanceSchedule.name (NOT NULL) → no error."""
        sched = self._make_schedule()
        self._guard(sched, {"name": "New task name"})  # must not raise

    def test_noop_for_non_none_is_active(self) -> None:
        """Non-None bool on is_active → no error."""
        sched = self._make_schedule()
        self._guard(sched, {"is_active": False})  # must not raise

    # ------------------------------------------------------------------
    # 4. No-op for unknown / relationship keys
    # ------------------------------------------------------------------

    def test_noop_for_relationship_key(self) -> None:
        """'instance' is a relationship, not a column — guard ignores it."""
        sched = self._make_schedule()
        self._guard(sched, {"instance": None})  # relationship name — must not raise

    def test_noop_for_unknown_key(self) -> None:
        """Completely unknown key → guard ignores it (defensive)."""
        sched = self._make_schedule()
        self._guard(sched, {"nonexistent_field": None})  # must not raise

    # ------------------------------------------------------------------
    # 5. Mixed fields: raises on first bad field
    # ------------------------------------------------------------------

    def test_raises_on_first_null_not_null_in_mixed_dict(self) -> None:
        """When dict mixes nullable-null and NOT-NULL-null, raises for the bad one."""
        from app.core.errors import AppError

        sched = self._make_schedule()
        # notes=None is fine (nullable); name=None is not
        with pytest.raises(AppError):
            self._guard(sched, {"notes": None, "name": None})


# ---------------------------------------------------------------------------
# Fixture infrastructure (mirrors test_m7_step4.py pattern)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_null_guard_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-null-guard")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models() -> None:
    """Reload model modules to pick up fresh DB engine after monkeypatch."""
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
    import app.models.maintenance_schedule as ms_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.shopping_list_item as sli_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod
    import app.repositories.maintenance_schedule as ms_repo_mod

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
    importlib.reload(user_token_mod)
    importlib.reload(audit_log_mod)
    importlib.reload(sli_mod)
    importlib.reload(ms_mod)
    # Reload the repository AFTER models so it picks up the fresh class objects.
    importlib.reload(ms_repo_mod)


def _seed_kinds(engine: object) -> None:
    """Seed item kinds (required by item definitions)."""
    from sqlalchemy.orm import sessionmaker as SM

    from app.models.item_kind import ItemKind

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        for code, name in [
            ("durable", "Durable"),
            ("consumable", "Consumable"),
            ("perishable", "Perishable"),
        ]:
            db.add(ItemKind(code=code, name=name, is_system=True))
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """TestClient + engine with schema created but no users."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_kinds(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


def _create_user_and_login(
    engine: object,
    client: TestClient,
    email: str,
    password: str,
    role: str = "admin",
) -> None:
    """Insert a user with the given role and log in."""
    from sqlalchemy.orm import sessionmaker as SM

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        repo = UserRepository(db)
        repo.create(email=email, password_hash=hash_password(password), role=role)
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.json()}"


@pytest.fixture()
def admin_client(base_client: tuple[TestClient, object]) -> TestClient:
    """TestClient authenticated as an admin user."""
    client, engine = base_client
    _create_user_and_login(engine, client, "admin@test.com", "adminpass", "admin")
    return client


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str = "Test Item",
    unit: str = "pcs",
    tracking_mode: str = "exact",
) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/definitions",
        json={"name": name, "unit": unit, "stock_tracking_mode": tracking_mode},
    )
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(client: TestClient, definition_id: int) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/instances", json={"definition_id": definition_id})
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_schedule(
    client: TestClient,
    instance_id: int,
    name: str = "Replace filter",
    interval_unit: str = "month",
    interval_count: int = 3,
    next_due_date: str = "2027-01-01",
) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/maintenance-schedules",
        json={
            "instance_id": instance_id,
            "name": name,
            "interval_unit": interval_unit,
            "interval_count": interval_count,
            "next_due_date": next_due_date,
        },
    )
    assert resp.status_code == 201, f"create_schedule failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _add_shopping_item(
    client: TestClient,
    name: str = "Milk",
    desired_quantity: str | None = "2",
    note: str | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if desired_quantity is not None:
        payload["desired_quantity"] = desired_quantity
    if note is not None:
        payload["note"] = note
    resp = client.post("/api/shopping-list", json=payload)
    assert resp.status_code == 201, f"add_item failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Maintenance schedule PATCH — null-guard integration tests
# ---------------------------------------------------------------------------


class TestMaintenancePatchNullGuard:
    """PATCH /maintenance-schedules/{id}: null on NOT NULL → 422, not 500."""

    def test_null_name_returns_422(self, admin_client: TestClient) -> None:
        """PATCH with name=null on a NOT NULL column → 422 validation.invalid_input."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], name="AC filter replacement")

        resp = admin_client.patch(f"/api/maintenance-schedules/{sched['id']}", json={"name": None})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert data["code"] == "validation.invalid_input"

    def test_null_name_row_unchanged(self, admin_client: TestClient) -> None:
        """After a rejected name=null PATCH, the row should be unchanged."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], name="Original")

        # Send bad PATCH
        admin_client.patch(f"/api/maintenance-schedules/{sched['id']}", json={"name": None})

        # Row should be unchanged
        get_resp = admin_client.get(f"/api/maintenance-schedules/{sched['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Original"

    def test_null_next_due_date_returns_422(self, admin_client: TestClient) -> None:
        """PATCH with next_due_date=null → 422 validation.invalid_input."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])

        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"next_due_date": None}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_null_is_active_returns_422(self, admin_client: TestClient) -> None:
        """PATCH with is_active=null → 422 validation.invalid_input."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])

        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"is_active": None}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_null_interval_count_returns_422(self, admin_client: TestClient) -> None:
        """PATCH with interval_count=null → 422 validation.invalid_input."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])

        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"interval_count": None}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_null_lead_days_succeeds(self, admin_client: TestClient) -> None:
        """PATCH with lead_days=null on a nullable column → 200 (clears the override)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        # Create with an explicit lead_days override
        resp_create = admin_client.post(
            "/api/maintenance-schedules",
            json={
                "instance_id": inst["id"],
                "name": "Filter",
                "interval_unit": "month",
                "interval_count": 3,
                "next_due_date": "2027-01-01",
                "lead_days": 14,
            },
        )
        assert resp_create.status_code == 201
        sched = resp_create.json()
        assert sched["lead_days"] == 14

        # Clear it via PATCH null
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"lead_days": None}
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        assert resp.json()["lead_days"] is None

    def test_null_notes_succeeds(self, admin_client: TestClient) -> None:
        """PATCH with notes=null on a nullable column → 200 (clears the note)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        resp_create = admin_client.post(
            "/api/maintenance-schedules",
            json={
                "instance_id": inst["id"],
                "name": "Filter",
                "interval_unit": "month",
                "interval_count": 3,
                "next_due_date": "2027-01-01",
                "notes": "Some note",
            },
        )
        assert resp_create.status_code == 201
        sched = resp_create.json()
        assert sched["notes"] == "Some note"

        resp = admin_client.patch(f"/api/maintenance-schedules/{sched['id']}", json={"notes": None})
        assert resp.status_code == 200
        assert resp.json()["notes"] is None

    def test_valid_patch_unaffected(self, admin_client: TestClient) -> None:
        """Normal valid PATCH (non-null fields) is unaffected by the guard."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], name="Old name", interval_count=3)

        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}",
            json={"name": "New name", "interval_count": 6},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New name"
        assert data["interval_count"] == 6


# ---------------------------------------------------------------------------
# Shopping list PATCH — all client-patchable fields are nullable
# ---------------------------------------------------------------------------


class TestShoppingListPatchNullableFields:
    """PATCH /shopping-list/{id}: all patchable fields (name, desired_quantity, note)
    are nullable in the DB.  Nulling them via PATCH should always succeed (200).

    This confirms the guard is a no-op for ShoppingListItem's PATCH surface.
    """

    def test_null_name_succeeds(self, admin_client: TestClient) -> None:
        """name is nullable → PATCH name=null → 200."""
        item = _add_shopping_item(admin_client, name="Paper towels", desired_quantity="2")
        resp = admin_client.patch(f"/api/shopping-list/{item['id']}", json={"name": None})
        # name is nullable in the DB, so null is valid
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"

    def test_null_desired_quantity_succeeds(self, admin_client: TestClient) -> None:
        """desired_quantity is nullable → PATCH desired_quantity=null → 200."""
        item = _add_shopping_item(admin_client, name="Eggs", desired_quantity="12")
        resp = admin_client.patch(
            f"/api/shopping-list/{item['id']}", json={"desired_quantity": None}
        )
        assert resp.status_code == 200
        assert resp.json()["desired_quantity"] is None

    def test_null_note_succeeds(self, admin_client: TestClient) -> None:
        """note is nullable → PATCH note=null → 200 (clears the note)."""
        item = _add_shopping_item(admin_client, name="Butter", note="Unsalted")
        resp = admin_client.patch(f"/api/shopping-list/{item['id']}", json={"note": None})
        assert resp.status_code == 200
        assert resp.json()["note"] is None

    def test_valid_patch_unaffected(self, admin_client: TestClient) -> None:
        """Normal valid PATCH is unaffected."""
        item = _add_shopping_item(admin_client, name="Milk")
        resp = admin_client.patch(
            f"/api/shopping-list/{item['id']}",
            json={"name": "Oat milk", "desired_quantity": "3"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Oat milk"
