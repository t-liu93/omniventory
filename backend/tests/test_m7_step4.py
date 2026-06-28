"""Tests for M7 Step 4: maintenance_schedules table, add_interval helper,
CRUD service, and endpoints.

Coverage
--------
add_interval matrix (§4.4):
    - Day/week additions (simple timedelta).
    - Month additions: standard + end-of-month clamp.
      * Jan-31 + 1 month → Feb-28 (non-leap) or Feb-29 (leap).
      * Aug-31 + 6 months → Feb-28/29.
      * Oct-31 + 1 month → Nov-30.
      * Dec-31 + 1 month → Jan-31.
    - Year additions: +1 year = +12 months (same clamp rules).
    - count ≥ 2 for month and year.
    - Year rollover (Dec → Jan+1).
    - Unknown unit raises ValueError.

CRUD via HTTP:
    - Create schedule: 201 with correct fields.
    - Create: bad instance_id → 404 stock_instance.not_found.
    - Create: bad interval_unit → 422 validation.unsupported_interval_unit.
    - Create: interval_count < 1 → 422 validation.invalid_input.
    - Create: lead_days < 0 → 422 validation.invalid_input.
    - Edit (PATCH): only supplied fields updated.
    - Edit: bad interval_unit → 422 validation.unsupported_interval_unit.
    - Delete: 204; subsequent GET → 404.
    - 404 maintenance.not_found on PATCH/DELETE/GET for missing id.

Complete (mark done → advance):
    - complete() sets last_completed_date and advances next_due_date.
    - Back-dated completed_on advances from that date.
    - complete: 404 maintenance.not_found for missing id.

Instance-scoped list:
    - GET /instances/{id}/maintenance-schedules returns schedules for that instance.

Status computation:
    - overdue: today > next_due_date.
    - due_soon: next_due_date - lead <= today <= next_due_date.
    - ok: today < next_due_date - lead.
    - effective_lead_days = lead_days ?? global default (7).

Permission gating:
    - Viewer blocked from mutations (403 auth.forbidden).
    - Viewer allowed to read (200).
    - Member (admin) allowed to mutate.

Migration round-trip:
    - Migration 0034 upgrade + downgrade on a DB at 0033.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# add_interval unit tests (§4.4 — required matrix, no DB needed)
# ---------------------------------------------------------------------------


class TestAddInterval:
    """Calendar-correct add_interval helper (app/core/dates.py)."""

    def _ai(self, d: date, unit: str, count: int) -> date:
        from app.core.dates import add_interval

        return add_interval(d, unit, count)

    # --- Day and week (simple timedelta) ---

    def test_day_single(self) -> None:
        assert self._ai(date(2024, 3, 15), "day", 1) == date(2024, 3, 16)

    def test_day_multiple(self) -> None:
        assert self._ai(date(2024, 1, 28), "day", 7) == date(2024, 2, 4)

    def test_week_single(self) -> None:
        assert self._ai(date(2024, 3, 1), "week", 1) == date(2024, 3, 8)

    def test_week_multiple(self) -> None:
        assert self._ai(date(2024, 1, 1), "week", 4) == date(2024, 1, 29)

    # --- Month: standard cases (no clamp) ---

    def test_month_no_clamp_simple(self) -> None:
        assert self._ai(date(2024, 1, 15), "month", 1) == date(2024, 2, 15)

    def test_month_year_rollover(self) -> None:
        """December + 1 month → January of next year."""
        assert self._ai(date(2024, 12, 15), "month", 1) == date(2025, 1, 15)

    def test_month_multiple(self) -> None:
        """Adding 3 months."""
        assert self._ai(date(2024, 3, 10), "month", 3) == date(2024, 6, 10)

    def test_month_count_gte_2(self) -> None:
        """count=2 adds two calendar months."""
        assert self._ai(date(2024, 1, 5), "month", 2) == date(2024, 3, 5)

    def test_month_large_count_year_boundary(self) -> None:
        """count=13 crosses a year boundary."""
        assert self._ai(date(2024, 1, 1), "month", 13) == date(2025, 2, 1)

    # --- Month: end-of-month clamping (the trap) ---

    def test_month_jan31_to_feb_non_leap(self) -> None:
        """Jan-31 + 1 month → Feb-28 in a non-leap year (2023)."""
        assert self._ai(date(2023, 1, 31), "month", 1) == date(2023, 2, 28)

    def test_month_jan31_to_feb_leap(self) -> None:
        """Jan-31 + 1 month → Feb-29 in a leap year (2024)."""
        assert self._ai(date(2024, 1, 31), "month", 1) == date(2024, 2, 29)

    def test_month_aug31_plus6_non_leap(self) -> None:
        """Aug-31 + 6 months → Feb-28 in a non-leap year (2023)."""
        assert self._ai(date(2022, 8, 31), "month", 6) == date(2023, 2, 28)

    def test_month_aug31_plus6_leap(self) -> None:
        """Aug-31 + 6 months → Feb-29 in a leap year (2024)."""
        assert self._ai(date(2023, 8, 31), "month", 6) == date(2024, 2, 29)

    def test_month_oct31_plus1(self) -> None:
        """Oct-31 + 1 month → Nov-30 (November has 30 days)."""
        assert self._ai(date(2024, 10, 31), "month", 1) == date(2024, 11, 30)

    def test_month_dec31_plus1(self) -> None:
        """Dec-31 + 1 month → Jan-31 (no clamp needed)."""
        assert self._ai(date(2024, 12, 31), "month", 1) == date(2025, 1, 31)

    def test_month_mar31_plus1(self) -> None:
        """Mar-31 + 1 month → Apr-30."""
        assert self._ai(date(2024, 3, 31), "month", 1) == date(2024, 4, 30)

    # --- Year ---

    def test_year_single(self) -> None:
        """+1 year = +12 months."""
        assert self._ai(date(2024, 3, 15), "year", 1) == date(2025, 3, 15)

    def test_year_feb29_to_non_leap(self) -> None:
        """Feb-29 (leap) + 1 year → Feb-28 (non-leap next year)."""
        assert self._ai(date(2024, 2, 29), "year", 1) == date(2025, 2, 28)

    def test_year_feb29_to_next_leap(self) -> None:
        """Feb-29 (leap) + 4 years → Feb-29 (next leap year)."""
        assert self._ai(date(2024, 2, 29), "year", 4) == date(2028, 2, 29)

    def test_year_multiple(self) -> None:
        """count=3 for year."""
        assert self._ai(date(2022, 6, 15), "year", 3) == date(2025, 6, 15)

    def test_year_equals_12_months(self) -> None:
        """+1 year ≡ +12 months (same result)."""
        d = date(2024, 5, 31)
        assert self._ai(d, "year", 1) == self._ai(d, "month", 12)

    def test_year_jan31_clamp(self) -> None:
        """Jan-31 + 1 year still lands on Jan-31 (no clamp needed)."""
        assert self._ai(date(2024, 1, 31), "year", 1) == date(2025, 1, 31)

    # --- Unknown unit raises ValueError ---

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported interval unit"):
            self._ai(date(2024, 1, 1), "quarter", 1)

    def test_empty_unit_raises(self) -> None:
        with pytest.raises(ValueError):
            self._ai(date(2024, 1, 1), "", 1)


# ---------------------------------------------------------------------------
# Fixture infrastructure (mirrors test_m7_step1.py pattern)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m7_step4_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m7-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models() -> None:
    """Reload model modules (and the maintenance_schedule repository) to pick up
    fresh DB engine after monkeypatch.

    The maintenance_schedule repository is reloaded *after* all model modules so
    that its module-level ``from app.models.stock_instance import StockInstance``
    and ``from app.models.maintenance_schedule import MaintenanceSchedule`` pick
    up the freshly-reloaded classes.  Without this, the chained
    ``joinedload(MaintenanceSchedule.instance).joinedload(StockInstance.definition)``
    fails with an "ORM entity does not link" error when another test file has
    previously reloaded stock_instance_mod (causing class-identity mismatches).
    """
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


@pytest.fixture()
def viewer_client(base_client: tuple[TestClient, object]) -> tuple[TestClient, object]:
    """TestClient with admin session + a viewer user (for permission tests)."""
    client, engine = base_client
    _create_user_and_login(engine, client, "admin@test.com", "adminpass", "admin")
    return client, engine


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str = "Air Conditioner",
    unit: str = "unit",
    tracking_mode: str = "exact",
) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/definitions",
        json={"name": name, "unit": unit, "stock_tracking_mode": tracking_mode},
    )
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    serial: str | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"definition_id": definition_id}  # type: ignore[type-arg]
    if serial is not None:
        payload["serial"] = serial
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_schedule(
    client: TestClient,
    instance_id: int,
    name: str = "Replace AC filter",
    interval_unit: str = "month",
    interval_count: int = 3,
    next_due_date: str = "2026-07-01",
    lead_days: int | None = None,
    notes: str | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {  # type: ignore[type-arg]
        "instance_id": instance_id,
        "name": name,
        "interval_unit": interval_unit,
        "interval_count": interval_count,
        "next_due_date": next_due_date,
    }
    if lead_days is not None:
        payload["lead_days"] = lead_days
    if notes is not None:
        payload["notes"] = notes
    resp = client.post("/api/maintenance-schedules", json=payload)
    assert resp.status_code == 201, f"create_schedule failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Create (happy path + validation)
# ---------------------------------------------------------------------------


class TestCreateSchedule:
    """POST /maintenance-schedules."""

    def test_create_basic(self, admin_client: TestClient) -> None:
        """Create a schedule with minimal fields."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])

        payload = {
            "instance_id": inst["id"],
            "name": "Replace AC filter",
            "interval_unit": "month",
            "interval_count": 3,
            "next_due_date": "2026-09-01",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["instance_id"] == inst["id"]
        assert data["name"] == "Replace AC filter"
        assert data["interval_unit"] == "month"
        assert data["interval_count"] == 3
        assert data["next_due_date"] == "2026-09-01"
        assert data["lead_days"] is None
        assert data["last_completed_date"] is None
        assert data["is_active"] is True
        assert "id" in data
        assert "instance_name" in data
        assert "status" in data
        assert "effective_lead_days" in data

    def test_create_with_optional_fields(self, admin_client: TestClient) -> None:
        """Create a schedule with all optional fields."""
        defn = _create_definition(admin_client, name="Water Heater")
        inst = _create_instance(admin_client, defn["id"])

        payload = {
            "instance_id": inst["id"],
            "name": "Annual flush",
            "interval_unit": "year",
            "interval_count": 1,
            "next_due_date": "2027-01-15",
            "lead_days": 14,
            "notes": "Use vinegar flush kit",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["lead_days"] == 14
        assert data["effective_lead_days"] == 14
        assert data["notes"] == "Use vinegar flush kit"

    def test_create_instance_name_resolved(self, admin_client: TestClient) -> None:
        """instance_name in response is the definition's name."""
        defn = _create_definition(admin_client, name="My Appliance")
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        assert sched["instance_name"] == "My Appliance"

    def test_create_bad_instance_id(self, admin_client: TestClient) -> None:
        """Nonexistent instance_id → 404 stock_instance.not_found."""
        payload = {
            "instance_id": 999999,
            "name": "Bogus",
            "interval_unit": "month",
            "interval_count": 1,
            "next_due_date": "2026-09-01",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 404
        assert resp.json()["code"] == "stock_instance.not_found"

    def test_create_bad_interval_unit(self, admin_client: TestClient) -> None:
        """Bad interval_unit → 422 validation.unsupported_interval_unit."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        payload = {
            "instance_id": inst["id"],
            "name": "Test",
            "interval_unit": "quarter",  # not supported
            "interval_count": 1,
            "next_due_date": "2026-09-01",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.unsupported_interval_unit"

    def test_create_interval_count_zero_rejected(self, admin_client: TestClient) -> None:
        """interval_count < 1 → 422 (Pydantic ge=1 → validation.invalid_input)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        payload = {
            "instance_id": inst["id"],
            "name": "Bad",
            "interval_unit": "month",
            "interval_count": 0,
            "next_due_date": "2026-09-01",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_create_negative_interval_count_rejected(self, admin_client: TestClient) -> None:
        """interval_count < 0 → 422."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        payload = {
            "instance_id": inst["id"],
            "name": "Bad",
            "interval_unit": "month",
            "interval_count": -1,
            "next_due_date": "2026-09-01",
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 422

    def test_create_negative_lead_days_rejected(self, admin_client: TestClient) -> None:
        """lead_days < 0 → 422 (Pydantic ge=0)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        payload = {
            "instance_id": inst["id"],
            "name": "Bad",
            "interval_unit": "month",
            "interval_count": 1,
            "next_due_date": "2026-09-01",
            "lead_days": -1,
        }
        resp = admin_client.post("/api/maintenance-schedules", json=payload)
        assert resp.status_code == 422

    def test_create_lead_days_zero_allowed(self, admin_client: TestClient) -> None:
        """lead_days=0 is allowed (fires on the due date itself)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], lead_days=0)
        assert sched["lead_days"] == 0
        assert sched["effective_lead_days"] == 0

    def test_create_all_valid_interval_units(self, admin_client: TestClient) -> None:
        """All four supported interval units are accepted."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        for unit in ("day", "week", "month", "year"):
            payload = {
                "instance_id": inst["id"],
                "name": f"Test {unit}",
                "interval_unit": unit,
                "interval_count": 2,
                "next_due_date": "2026-09-01",
            }
            resp = admin_client.post("/api/maintenance-schedules", json=payload)
            assert resp.status_code == 201, f"Failed for unit={unit}: {resp.json()}"


# ---------------------------------------------------------------------------
# 2. Get one
# ---------------------------------------------------------------------------


class TestGetSchedule:
    """GET /maintenance-schedules/{id}."""

    def test_get_existing(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        created = _create_schedule(admin_client, inst["id"])
        resp = admin_client.get(f"/api/maintenance-schedules/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_404(self, admin_client: TestClient) -> None:
        """404 maintenance.not_found for unknown id."""
        resp = admin_client.get("/api/maintenance-schedules/999999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "maintenance.not_found"


# ---------------------------------------------------------------------------
# 3. Edit (PATCH)
# ---------------------------------------------------------------------------


class TestEditSchedule:
    """PATCH /maintenance-schedules/{id}."""

    def test_edit_name(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], name="Old name")
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"name": "New name"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New name"
        assert resp.json()["interval_unit"] == sched["interval_unit"]  # unchanged

    def test_edit_interval(self, admin_client: TestClient) -> None:
        """Editing interval_unit and interval_count."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], interval_unit="month", interval_count=3)
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}",
            json={"interval_unit": "year", "interval_count": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interval_unit"] == "year"
        assert data["interval_count"] == 1

    def test_edit_pause(self, admin_client: TestClient) -> None:
        """Setting is_active=false pauses the schedule."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"is_active": False}
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_edit_lead_days(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"lead_days": 14}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lead_days"] == 14
        assert data["effective_lead_days"] == 14

    def test_edit_bad_interval_unit(self, admin_client: TestClient) -> None:
        """Bad interval_unit in PATCH → 422 validation.unsupported_interval_unit."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"interval_unit": "biweekly"}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.unsupported_interval_unit"

    def test_edit_missing_404(self, admin_client: TestClient) -> None:
        resp = admin_client.patch("/api/maintenance-schedules/999999", json={"name": "X"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "maintenance.not_found"

    def test_edit_partial_semantics(self, admin_client: TestClient) -> None:
        """Only supplied fields are modified (PATCH semantics)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(
            admin_client, inst["id"], name="Original", interval_unit="month", interval_count=3
        )
        # Only update notes
        resp = admin_client.patch(
            f"/api/maintenance-schedules/{sched['id']}", json={"notes": "updated"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Original"  # unchanged
        assert data["interval_unit"] == "month"  # unchanged
        assert data["interval_count"] == 3  # unchanged
        assert data["notes"] == "updated"


# ---------------------------------------------------------------------------
# 4. Delete
# ---------------------------------------------------------------------------


class TestDeleteSchedule:
    """DELETE /maintenance-schedules/{id}."""

    def test_delete(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        resp = admin_client.delete(f"/api/maintenance-schedules/{sched['id']}")
        assert resp.status_code == 204
        # Subsequent GET → 404.
        resp2 = admin_client.get(f"/api/maintenance-schedules/{sched['id']}")
        assert resp2.status_code == 404
        assert resp2.json()["code"] == "maintenance.not_found"

    def test_delete_missing_404(self, admin_client: TestClient) -> None:
        resp = admin_client.delete("/api/maintenance-schedules/999999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "maintenance.not_found"


# ---------------------------------------------------------------------------
# 5. Complete (mark done → advance next_due_date)
# ---------------------------------------------------------------------------


class TestCompleteSchedule:
    """POST /maintenance-schedules/{id}/complete."""

    def test_complete_default_today(self, admin_client: TestClient) -> None:
        """complete() with no body sets last_completed_date=today and advances next_due."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        # Set next_due to a past date so we can verify advance.
        past_due = (date.today() - timedelta(days=30)).isoformat()
        sched = _create_schedule(
            admin_client,
            inst["id"],
            interval_unit="month",
            interval_count=3,
            next_due_date=past_due,
        )
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete")
        assert resp.status_code == 200
        data = resp.json()

        today_str = date.today().isoformat()
        assert data["last_completed_date"] == today_str

        from app.core.dates import add_interval

        expected_next = add_interval(date.today(), "month", 3).isoformat()
        assert data["next_due_date"] == expected_next

    def test_complete_explicit_completed_on(self, admin_client: TestClient) -> None:
        """complete() with explicit completed_on advances from that date."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(
            admin_client,
            inst["id"],
            interval_unit="month",
            interval_count=6,
            next_due_date="2026-06-01",
        )
        # Back-dated completion on 2026-01-15
        body = {"completed_on": "2026-01-15"}
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_completed_date"] == "2026-01-15"
        # +6 months from Jan-15 = Jul-15
        assert data["next_due_date"] == "2026-07-15"

    def test_complete_backdated_clamps_end_of_month(self, admin_client: TestClient) -> None:
        """Back-dated completion on Jan-31 with +1mo advances to Feb-28 (non-leap)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(
            admin_client,
            inst["id"],
            interval_unit="month",
            interval_count=1,
            next_due_date="2023-03-31",
        )
        body = {"completed_on": "2023-01-31"}
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_completed_date"] == "2023-01-31"
        assert data["next_due_date"] == "2023-02-28"

    def test_complete_note_accepted_not_persisted(self, admin_client: TestClient) -> None:
        """note field is accepted in body but not persisted (M7 §13 deferred)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])
        body = {"completed_on": "2026-06-01", "note": "Changed it today"}
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete", json=body)
        assert resp.status_code == 200
        # 'note' from the completion is not reflected in the schedule's 'notes' field
        # (the completion note is distinct from the schedule's own annotation).
        # notes field is unchanged from creation.
        data = resp.json()
        assert data["last_completed_date"] == "2026-06-01"

    def test_complete_missing_404(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/maintenance-schedules/999999/complete")
        assert resp.status_code == 404
        assert resp.json()["code"] == "maintenance.not_found"

    def test_complete_day_interval(self, admin_client: TestClient) -> None:
        """Complete with day interval advances correctly."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(
            admin_client,
            inst["id"],
            interval_unit="day",
            interval_count=30,
            next_due_date="2026-06-01",
        )
        body = {"completed_on": "2026-06-01"}
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["next_due_date"] == "2026-07-01"

    def test_complete_week_interval(self, admin_client: TestClient) -> None:
        """Complete with week interval advances correctly."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(
            admin_client,
            inst["id"],
            interval_unit="week",
            interval_count=2,
            next_due_date="2026-06-01",
        )
        body = {"completed_on": "2026-06-01"}
        resp = admin_client.post(f"/api/maintenance-schedules/{sched['id']}/complete", json=body)
        assert resp.status_code == 200
        assert resp.json()["next_due_date"] == "2026-06-15"


# ---------------------------------------------------------------------------
# 6. List (instance-scoped and global)
# ---------------------------------------------------------------------------


class TestListSchedules:
    """GET /maintenance-schedules and GET /instances/{id}/maintenance-schedules."""

    def test_list_all(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        _create_schedule(admin_client, inst["id"], name="Task 1")
        _create_schedule(admin_client, inst["id"], name="Task 2")
        resp = admin_client.get("/api/maintenance-schedules")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_list_filtered_by_instance(self, admin_client: TestClient) -> None:
        defn = _create_definition(admin_client)
        inst1 = _create_instance(admin_client, defn["id"])
        inst2 = _create_instance(admin_client, defn["id"])
        _create_schedule(admin_client, inst1["id"], name="Inst1 Task")
        _create_schedule(admin_client, inst2["id"], name="Inst2 Task")
        resp = admin_client.get(f"/api/maintenance-schedules?instance_id={inst1['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Inst1 Task"

    def test_list_instance_scoped_route(self, admin_client: TestClient) -> None:
        """GET /instances/{id}/maintenance-schedules returns schedules for that instance."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        _create_schedule(admin_client, inst["id"], name="Filter task")
        resp = admin_client.get(f"/api/instances/{inst['id']}/maintenance-schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all(s["instance_id"] == inst["id"] for s in data)

    def test_list_instance_scoped_empty_for_other_instance(self, admin_client: TestClient) -> None:
        """Instance with no schedules returns empty list (not 404)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        resp = admin_client.get(f"/api/instances/{inst['id']}/maintenance-schedules")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_active_filter(self, admin_client: TestClient) -> None:
        """?active=true returns only active schedules."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        _create_schedule(admin_client, inst["id"], name="Active task")
        sched2 = _create_schedule(admin_client, inst["id"], name="Paused task")
        # Pause sched2
        admin_client.patch(f"/api/maintenance-schedules/{sched2['id']}", json={"is_active": False})
        resp = admin_client.get(f"/api/maintenance-schedules?instance_id={inst['id']}&active=true")
        assert resp.status_code == 200
        data = resp.json()
        assert all(s["is_active"] is True for s in data)
        names = {s["name"] for s in data}
        assert "Active task" in names
        assert "Paused task" not in names


# ---------------------------------------------------------------------------
# 7. Status computation
# ---------------------------------------------------------------------------


class TestStatusComputation:
    """Server-computed status field (overdue / due_soon / ok)."""

    def test_status_overdue(self, admin_client: TestClient) -> None:
        """today > next_due_date → overdue."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        past = (date.today() - timedelta(days=5)).isoformat()
        sched = _create_schedule(admin_client, inst["id"], next_due_date=past, lead_days=7)
        assert sched["status"] == "overdue"

    def test_status_due_soon_boundary_equal_window_start(self, admin_client: TestClient) -> None:
        """today == next_due_date - lead → due_soon (boundary fires)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        lead = 7
        due = date.today() + timedelta(days=lead)
        sched = _create_schedule(
            admin_client, inst["id"], next_due_date=due.isoformat(), lead_days=lead
        )
        assert sched["status"] == "due_soon"

    def test_status_due_soon_within_window(self, admin_client: TestClient) -> None:
        """today is inside the window → due_soon."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        lead = 7
        due = date.today() + timedelta(days=3)
        sched = _create_schedule(
            admin_client, inst["id"], next_due_date=due.isoformat(), lead_days=lead
        )
        assert sched["status"] == "due_soon"

    def test_status_ok_before_window(self, admin_client: TestClient) -> None:
        """today < next_due_date - lead → ok."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        lead = 7
        due = date.today() + timedelta(days=30)
        sched = _create_schedule(
            admin_client, inst["id"], next_due_date=due.isoformat(), lead_days=lead
        )
        assert sched["status"] == "ok"

    def test_status_not_fired_day_before_window(self, admin_client: TestClient) -> None:
        """today == window_start - 1 day → ok (window not yet reached)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        lead = 7
        # Window starts in 1 day, so today is 1 day before the window.
        due = date.today() + timedelta(days=lead + 1)
        sched = _create_schedule(
            admin_client, inst["id"], next_due_date=due.isoformat(), lead_days=lead
        )
        assert sched["status"] == "ok"

    def test_effective_lead_days_no_override_uses_global(self, admin_client: TestClient) -> None:
        """When lead_days is None, effective_lead_days = global default (7)."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"])  # no lead_days
        assert sched["lead_days"] is None
        assert sched["effective_lead_days"] == 7  # default

    def test_effective_lead_days_override(self, admin_client: TestClient) -> None:
        """When lead_days is set, effective_lead_days = lead_days."""
        defn = _create_definition(admin_client)
        inst = _create_instance(admin_client, defn["id"])
        sched = _create_schedule(admin_client, inst["id"], lead_days=14)
        assert sched["lead_days"] == 14
        assert sched["effective_lead_days"] == 14


# ---------------------------------------------------------------------------
# 8. Permission gating
# ---------------------------------------------------------------------------


class TestPermissionGating:
    """M6 EDIT/VIEW gating on maintenance endpoints."""

    def _create_viewer(self, engine: object, admin_client: TestClient) -> TestClient:
        """Create a viewer user and return a TestClient logged in as viewer."""
        from sqlalchemy.orm import sessionmaker as SM

        from app.auth.passwords import hash_password
        from app.repositories.user import UserRepository

        factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            repo = UserRepository(db)
            repo.create(
                email="viewer@test.com",
                password_hash=hash_password("viewpass"),
                role="viewer",
            )
            db.commit()
        finally:
            db.close()

        from fastapi.testclient import TestClient as TC

        # Create a separate client for viewer, same app
        # We can piggyback on a new session by making a fresh TestClient
        # (the admin_client's app is already wired up)
        from app.config import get_settings
        from app.main import create_app

        get_settings.cache_clear()
        viewer_app = create_app()
        viewer_tc = TC(viewer_app, raise_server_exceptions=True)
        viewer_tc.__enter__()
        resp = viewer_tc.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewpass"}
        )
        assert resp.status_code == 200, f"Viewer login failed: {resp.json()}"
        return viewer_tc

    def test_viewer_can_read(self, viewer_client: tuple[TestClient, object]) -> None:
        """Viewer can GET /maintenance-schedules (VIEW permission)."""
        client, engine = viewer_client
        defn = _create_definition(client)
        inst = _create_instance(client, defn["id"])
        _create_schedule(client, inst["id"])

        viewer_tc = self._create_viewer(engine, client)
        try:
            resp = viewer_tc.get("/api/maintenance-schedules")
            assert resp.status_code == 200
        finally:
            viewer_tc.__exit__(None, None, None)

    def test_viewer_blocked_from_create(self, viewer_client: tuple[TestClient, object]) -> None:
        """Viewer is blocked from POST /maintenance-schedules (EDIT required)."""
        client, engine = viewer_client
        defn = _create_definition(client)
        inst = _create_instance(client, defn["id"])

        viewer_tc = self._create_viewer(engine, client)
        try:
            payload = {
                "instance_id": inst["id"],
                "name": "Viewer task",
                "interval_unit": "month",
                "interval_count": 1,
                "next_due_date": "2026-09-01",
            }
            resp = viewer_tc.post("/api/maintenance-schedules", json=payload)
            assert resp.status_code == 403
            assert resp.json()["code"] == "auth.forbidden"
        finally:
            viewer_tc.__exit__(None, None, None)

    def test_viewer_blocked_from_edit(self, viewer_client: tuple[TestClient, object]) -> None:
        """Viewer is blocked from PATCH /maintenance-schedules/{id}."""
        client, engine = viewer_client
        defn = _create_definition(client)
        inst = _create_instance(client, defn["id"])
        sched = _create_schedule(client, inst["id"])

        viewer_tc = self._create_viewer(engine, client)
        try:
            resp = viewer_tc.patch(
                f"/api/maintenance-schedules/{sched['id']}", json={"name": "Hijacked"}
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "auth.forbidden"
        finally:
            viewer_tc.__exit__(None, None, None)

    def test_viewer_blocked_from_delete(self, viewer_client: tuple[TestClient, object]) -> None:
        """Viewer is blocked from DELETE /maintenance-schedules/{id}."""
        client, engine = viewer_client
        defn = _create_definition(client)
        inst = _create_instance(client, defn["id"])
        sched = _create_schedule(client, inst["id"])

        viewer_tc = self._create_viewer(engine, client)
        try:
            resp = viewer_tc.delete(f"/api/maintenance-schedules/{sched['id']}")
            assert resp.status_code == 403
            assert resp.json()["code"] == "auth.forbidden"
        finally:
            viewer_tc.__exit__(None, None, None)

    def test_viewer_blocked_from_complete(self, viewer_client: tuple[TestClient, object]) -> None:
        """Viewer is blocked from POST /maintenance-schedules/{id}/complete."""
        client, engine = viewer_client
        defn = _create_definition(client)
        inst = _create_instance(client, defn["id"])
        sched = _create_schedule(client, inst["id"])

        viewer_tc = self._create_viewer(engine, client)
        try:
            resp = viewer_tc.post(f"/api/maintenance-schedules/{sched['id']}/complete")
            assert resp.status_code == 403
            assert resp.json()["code"] == "auth.forbidden"
        finally:
            viewer_tc.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 9. Migration round-trip (0034 upgrade + downgrade)
# ---------------------------------------------------------------------------


class TestMigration0034:
    """Migration 0034 upgrade + downgrade round-trip."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic via the venv binary (mirrors test_m7_step1 pattern)."""
        import subprocess

        backend_root = Path(__file__).parent.parent
        env = {
            **os.environ,
            "SECRET_KEY": "test-secret-migration-0034",
            "DATABASE_URL": url,
            "ENVIRONMENT": "test",
        }
        result = subprocess.run(
            [".venv/bin/alembic", *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def test_migration_round_trip(self) -> None:
        """0034 upgrade then downgrade leaves DB in 0033 state (table dropped)."""
        import tempfile as _tempfile

        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = _tempfile.mkstemp(suffix=".db", prefix="omniventory_mig_0034_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to head (applies 0033 + 0034).
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            engine = sa_create_engine(url)
            tables = set(sa_inspect(engine).get_table_names())
            columns = {c["name"] for c in sa_inspect(engine).get_columns("maintenance_schedules")}
            indexes = {
                idx["name"]
                for table in sa_inspect(engine).get_table_names()
                for idx in sa_inspect(engine).get_indexes(table)
            }
            engine.dispose()

            assert "maintenance_schedules" in tables, (
                f"maintenance_schedules table missing. Tables: {tables}"
            )
            assert columns >= {
                "id",
                "instance_id",
                "name",
                "interval_unit",
                "interval_count",
                "next_due_date",
                "lead_days",
                "last_completed_date",
                "notes",
                "is_active",
                "created_by",
                "created_at",
                "updated_at",
            }, f"Missing columns: {columns}"
            assert "ix_maintenance_schedules_instance_id" in indexes, (
                f"instance_id index missing. Indexes: {indexes}"
            )
            assert "ix_maintenance_schedules_next_due_date" in indexes, (
                f"next_due_date index missing. Indexes: {indexes}"
            )
            assert "ix_maintenance_schedules_is_active" in indexes, (
                f"is_active index missing. Indexes: {indexes}"
            )

            # Downgrade to 0033 — removes maintenance_schedules.
            rc, output = self._run_alembic("downgrade", "0033", url=url)
            assert rc == 0, f"alembic downgrade to 0033 failed:\n{output}"

            engine2 = sa_create_engine(url)
            tables2 = set(sa_inspect(engine2).get_table_names())
            engine2.dispose()

            assert "maintenance_schedules" not in tables2, (
                "maintenance_schedules table must be gone after downgrade to 0033"
            )
            # shopping_list_items (0033) should still be present.
            assert "shopping_list_items" in tables2, (
                "shopping_list_items must survive downgrade to 0033"
            )

        finally:
            if db_path.exists():
                db_path.unlink()
