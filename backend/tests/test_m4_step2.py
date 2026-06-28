"""M4 Step 2 tests: per-item and per-user reminder lead-time overrides.

Required coverage (per M4.md §5 + §9 Step 2 Tests):

Per-item (item_definition.reminder_lead_days):
- stored and echoed back in DefinitionResponse
- <0 rejected by Pydantic (validation.invalid_input via HTTP)
- not provided → NULL default
- PATCH set to null removes the override (consistent with default_best_before_days pattern)
- PATCH set to integer stores the value

Per-user (users.reminder_best_before_lead_days / reminder_warranty_lead_days):
- GET /auth/me echoes both new fields
- PATCH /auth/me can set, clear (null), and leave unchanged (omitted) each field independently
- <0 rejected by Pydantic (validation.invalid_input via HTTP 422)
- interactions: preferred_language is unaffected when only lead-day fields change

Migrations 0016 / 0017:
- upgrade creates the column(s); downgrade removes them
- existing rows are left NULL (no backfill) — the "inherit" default
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, object]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    for mod in (
        db_base_mod,
        hh_mod,
        user_mod,
        sess_mod,
        app_config_mod,
        cat_mod,
        ikind_mod,
        idef_mod,
        loc_mod,
        si_mod,
        sm_mod,
        setting_mod,
        audit_log_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base as _Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    return session, engine


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step2_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


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
    """Fresh in-memory SQLite session with all models registered."""
    session, engine = _make_in_memory_session()

    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step2")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session."""
    from fastapi.testclient import TestClient

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    for mod in (
        db_base_mod,
        hh_mod,
        user_mod,
        sess_mod,
        app_config_mod,
        cat_mod,
        ikind_mod,
        idef_mod,
        loc_mod,
        si_mod,
        sm_mod,
        setting_mod,
        audit_log_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(email="admin@example.com", password_hash=hash_password("adminpass"))
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
# 1. Per-item reminder_lead_days (unit-level via repository)
# ---------------------------------------------------------------------------


class TestDefinitionReminderLeadDays:
    """Unit tests for ItemDefinition.reminder_lead_days via repository."""

    def _make_kind(self, db: Session) -> int:
        """Insert a minimal durable item kind and return its id."""
        from app.models.item_kind import ItemKind

        kind = ItemKind(code="durable", name="Durable", is_system=True)
        db.add(kind)
        db.flush()
        return kind.id

    def test_default_is_null(self, db_session: Session) -> None:
        """When reminder_lead_days is not set on create, it is NULL."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Widget", kind_id=kind_id)
        db_session.commit()

        assert defn.reminder_lead_days is None

    def test_create_with_reminder_lead_days(self, db_session: Session) -> None:
        """Creating with reminder_lead_days stores and echoes the value."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Milk", kind_id=kind_id, reminder_lead_days=7)
        db_session.commit()

        assert defn.reminder_lead_days == 7

    def test_create_with_zero_lead_days(self, db_session: Session) -> None:
        """A lead of 0 is valid (fire on the target date itself)."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Bread", kind_id=kind_id, reminder_lead_days=0)
        db_session.commit()

        assert defn.reminder_lead_days == 0

    def test_update_sets_reminder_lead_days(self, db_session: Session) -> None:
        """PATCH (set_reminder_lead_days=True) stores the new value."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Passport", kind_id=kind_id)
        db_session.commit()

        repo.update(defn, set_reminder_lead_days=True, reminder_lead_days=90)
        db_session.commit()

        assert defn.reminder_lead_days == 90

    def test_update_clears_reminder_lead_days(self, db_session: Session) -> None:
        """PATCH with set_reminder_lead_days=True and None clears the override."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Passport", kind_id=kind_id, reminder_lead_days=90)
        db_session.commit()

        repo.update(defn, set_reminder_lead_days=True, reminder_lead_days=None)
        db_session.commit()

        assert defn.reminder_lead_days is None

    def test_update_without_flag_does_not_touch_field(self, db_session: Session) -> None:
        """Calling update() without set_reminder_lead_days leaves the field unchanged."""
        from app.repositories.item_definition import ItemDefinitionRepository

        kind_id = self._make_kind(db_session)
        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Widget", kind_id=kind_id, reminder_lead_days=14)
        db_session.commit()

        # Update name only — reminder_lead_days must be untouched
        repo.update(defn, name="Updated Widget")
        db_session.commit()

        assert defn.reminder_lead_days == 14


# ---------------------------------------------------------------------------
# 2. Per-item HTTP API tests
# ---------------------------------------------------------------------------


class TestDefinitionReminderLeadDaysHttp:
    """End-to-end HTTP tests for reminder_lead_days on item definitions."""

    def test_create_definition_with_reminder_lead_days(self, http_client: object) -> None:
        """POST /definitions with reminder_lead_days stores and echoes the value."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.post(
            "/api/definitions",
            json={"name": "Milk", "reminder_lead_days": 5},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["reminder_lead_days"] == 5

    def test_create_definition_no_lead_days_returns_null(self, http_client: object) -> None:
        """POST /definitions without reminder_lead_days returns null in response."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.post(
            "/api/definitions",
            json={"name": "Widget"},
        )
        assert resp.status_code == 201
        assert resp.json()["reminder_lead_days"] is None

    def test_create_definition_lead_zero_valid(self, http_client: object) -> None:
        """POST /definitions with reminder_lead_days=0 is accepted."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.post(
            "/api/definitions",
            json={"name": "Bread", "reminder_lead_days": 0},
        )
        assert resp.status_code == 201
        assert resp.json()["reminder_lead_days"] == 0

    def test_create_definition_negative_lead_returns_422(self, http_client: object) -> None:
        """POST /definitions with reminder_lead_days<0 returns 422 validation.invalid_input."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.post(
            "/api/definitions",
            json={"name": "Bad Item", "reminder_lead_days": -1},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_definition_sets_reminder_lead_days(self, http_client: object) -> None:
        """PATCH /definitions/{id} sets reminder_lead_days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        create_resp = http_client.post("/api/definitions", json={"name": "Passport"})
        assert create_resp.status_code == 201
        defn_id = create_resp.json()["id"]

        patch_resp = http_client.patch(
            f"/api/definitions/{defn_id}",
            json={"reminder_lead_days": 90},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reminder_lead_days"] == 90

    def test_patch_definition_null_clears_reminder_lead_days(self, http_client: object) -> None:
        """PATCH /definitions/{id} with null removes the reminder_lead_days override."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        create_resp = http_client.post(
            "/api/definitions", json={"name": "Passport", "reminder_lead_days": 90}
        )
        assert create_resp.status_code == 201
        defn_id = create_resp.json()["id"]

        patch_resp = http_client.patch(
            f"/api/definitions/{defn_id}",
            json={"reminder_lead_days": None},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reminder_lead_days"] is None

    def test_patch_definition_negative_lead_returns_422(self, http_client: object) -> None:
        """PATCH /definitions/{id} with reminder_lead_days<0 returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        create_resp = http_client.post("/api/definitions", json={"name": "Widget"})
        assert create_resp.status_code == 201
        defn_id = create_resp.json()["id"]

        patch_resp = http_client.patch(
            f"/api/definitions/{defn_id}",
            json={"reminder_lead_days": -5},
        )
        assert patch_resp.status_code == 422
        assert patch_resp.json()["code"] == "validation.invalid_input"

    def test_get_definition_echoes_reminder_lead_days(self, http_client: object) -> None:
        """GET /definitions/{id} includes reminder_lead_days in the response."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        create_resp = http_client.post(
            "/api/definitions", json={"name": "Milk", "reminder_lead_days": 3}
        )
        assert create_resp.status_code == 201
        defn_id = create_resp.json()["id"]

        get_resp = http_client.get(f"/api/definitions/{defn_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["reminder_lead_days"] == 3

    def test_patch_omit_lead_days_is_noop(self, http_client: object) -> None:
        """PATCH /definitions/{id} without reminder_lead_days leaves the value unchanged."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        create_resp = http_client.post(
            "/api/definitions", json={"name": "Milk", "reminder_lead_days": 7}
        )
        assert create_resp.status_code == 201
        defn_id = create_resp.json()["id"]

        # PATCH only the name — reminder_lead_days should remain 7
        patch_resp = http_client.patch(
            f"/api/definitions/{defn_id}",
            json={"name": "Whole Milk"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reminder_lead_days"] == 7


# ---------------------------------------------------------------------------
# 3. Per-user lead-time overrides (unit-level via repository)
# ---------------------------------------------------------------------------


class TestUserReminderLeadDays:
    """Unit tests for UserRepository.set_reminder_*_lead_days methods."""

    def _make_user(self, db: Session) -> object:
        from app.auth.passwords import hash_password
        from app.repositories.user import UserRepository

        repo = UserRepository(db)
        user = repo.create(email="tester@example.com", password_hash=hash_password("pass"))
        db.commit()
        return user

    def test_defaults_are_null(self, db_session: Session) -> None:
        """Newly created users have NULL reminder lead-day overrides."""
        user = self._make_user(db_session)

        assert user.reminder_best_before_lead_days is None  # type: ignore[union-attr]
        assert user.reminder_warranty_lead_days is None  # type: ignore[union-attr]

    def test_set_reminder_best_before_lead_days(self, db_session: Session) -> None:
        """set_reminder_best_before_lead_days stores the value."""
        from app.repositories.user import UserRepository

        user = self._make_user(db_session)
        repo = UserRepository(db_session)
        repo.set_reminder_best_before_lead_days(user, 5)
        db_session.commit()

        assert user.reminder_best_before_lead_days == 5  # type: ignore[union-attr]

    def test_set_reminder_warranty_lead_days(self, db_session: Session) -> None:
        """set_reminder_warranty_lead_days stores the value."""
        from app.repositories.user import UserRepository

        user = self._make_user(db_session)
        repo = UserRepository(db_session)
        repo.set_reminder_warranty_lead_days(user, 14)
        db_session.commit()

        assert user.reminder_warranty_lead_days == 14  # type: ignore[union-attr]

    def test_clear_reminder_best_before_lead_days(self, db_session: Session) -> None:
        """set_reminder_best_before_lead_days(None) clears the override to NULL."""
        from app.repositories.user import UserRepository

        user = self._make_user(db_session)
        repo = UserRepository(db_session)
        repo.set_reminder_best_before_lead_days(user, 5)
        db_session.commit()

        repo.set_reminder_best_before_lead_days(user, None)
        db_session.commit()

        assert user.reminder_best_before_lead_days is None  # type: ignore[union-attr]

    def test_clear_reminder_warranty_lead_days(self, db_session: Session) -> None:
        """set_reminder_warranty_lead_days(None) clears the override to NULL."""
        from app.repositories.user import UserRepository

        user = self._make_user(db_session)
        repo = UserRepository(db_session)
        repo.set_reminder_warranty_lead_days(user, 30)
        db_session.commit()

        repo.set_reminder_warranty_lead_days(user, None)
        db_session.commit()

        assert user.reminder_warranty_lead_days is None  # type: ignore[union-attr]

    def test_two_fields_are_independent(self, db_session: Session) -> None:
        """Setting best-before lead does not affect warranty lead and vice versa."""
        from app.repositories.user import UserRepository

        user = self._make_user(db_session)
        repo = UserRepository(db_session)
        repo.set_reminder_best_before_lead_days(user, 3)
        db_session.commit()

        # warranty should still be NULL
        assert user.reminder_warranty_lead_days is None  # type: ignore[union-attr]

        repo.set_reminder_warranty_lead_days(user, 21)
        db_session.commit()

        # best-before should still be 3
        assert user.reminder_best_before_lead_days == 3  # type: ignore[union-attr]
        assert user.reminder_warranty_lead_days == 21  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 4. Per-user HTTP API tests (/auth/me)
# ---------------------------------------------------------------------------


class TestUserReminderLeadDaysHttp:
    """End-to-end HTTP tests for per-user reminder lead-day overrides via /auth/me."""

    def test_get_me_returns_null_lead_days_by_default(self, http_client: object) -> None:
        """GET /auth/me returns null for both reminder lead fields by default."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/auth/me")
        assert resp.status_code == 200

        user = resp.json()["user"]
        assert user["reminder_best_before_lead_days"] is None
        assert user["reminder_warranty_lead_days"] is None

    def test_patch_me_sets_best_before_lead(self, http_client: object) -> None:
        """PATCH /auth/me sets reminder_best_before_lead_days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 5},
        )
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["reminder_best_before_lead_days"] == 5

    def test_patch_me_sets_warranty_lead(self, http_client: object) -> None:
        """PATCH /auth/me sets reminder_warranty_lead_days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_warranty_lead_days": 14},
        )
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["reminder_warranty_lead_days"] == 14

    def test_patch_me_null_clears_best_before_lead(self, http_client: object) -> None:
        """PATCH /auth/me with null explicitly clears reminder_best_before_lead_days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # First set
        http_client.patch("/api/auth/me", json={"reminder_best_before_lead_days": 7})
        # Then clear
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": None},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["reminder_best_before_lead_days"] is None

    def test_patch_me_null_clears_warranty_lead(self, http_client: object) -> None:
        """PATCH /auth/me with null explicitly clears reminder_warranty_lead_days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        http_client.patch("/api/auth/me", json={"reminder_warranty_lead_days": 21})
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_warranty_lead_days": None},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["reminder_warranty_lead_days"] is None

    def test_patch_me_omit_lead_is_noop(self, http_client: object) -> None:
        """PATCH /auth/me without reminder fields leaves them unchanged."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # Set both fields
        http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 3, "reminder_warranty_lead_days": 30},
        )

        # Update only preferred_language — lead-day fields must be untouched
        resp = http_client.patch(
            "/api/auth/me",
            json={"preferred_language": "en"},
        )
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["reminder_best_before_lead_days"] == 3
        assert user["reminder_warranty_lead_days"] == 30

    def test_patch_me_negative_best_before_lead_returns_422(self, http_client: object) -> None:
        """PATCH /auth/me with reminder_best_before_lead_days<0 returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": -1},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_me_negative_warranty_lead_returns_422(self, http_client: object) -> None:
        """PATCH /auth/me with reminder_warranty_lead_days<0 returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_warranty_lead_days": -3},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_me_zero_lead_is_valid(self, http_client: object) -> None:
        """PATCH /auth/me with 0 is accepted (fire on the target date itself)."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 0, "reminder_warranty_lead_days": 0},
        )
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["reminder_best_before_lead_days"] == 0
        assert user["reminder_warranty_lead_days"] == 0

    def test_patch_me_lead_days_do_not_affect_preferred_language(self, http_client: object) -> None:
        """Setting lead-day fields does not clobber preferred_language."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # First set preferred_language
        http_client.patch("/api/auth/me", json={"preferred_language": "zh"})

        # Now update lead days only — preferred_language must remain 'zh'
        resp = http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 5},
        )
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["preferred_language"] == "zh"
        assert user["reminder_best_before_lead_days"] == 5

    def test_patch_me_preferred_language_does_not_affect_lead_days(
        self, http_client: object
    ) -> None:
        """Updating preferred_language does not clobber reminder lead-day fields."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # First set lead days
        http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 7, "reminder_warranty_lead_days": 14},
        )

        # Now update language only — lead days must remain
        resp = http_client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["reminder_best_before_lead_days"] == 7
        assert user["reminder_warranty_lead_days"] == 14

    def test_get_me_after_patch_reflects_persisted_values(self, http_client: object) -> None:
        """GET /auth/me after PATCH returns the persisted values (not just PATCH response)."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        http_client.patch(
            "/api/auth/me",
            json={"reminder_best_before_lead_days": 10, "reminder_warranty_lead_days": 60},
        )

        get_resp = http_client.get("/api/auth/me")
        assert get_resp.status_code == 200
        user = get_resp.json()["user"]
        assert user["reminder_best_before_lead_days"] == 10
        assert user["reminder_warranty_lead_days"] == 60


# ---------------------------------------------------------------------------
# 5. Pydantic schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Pydantic validation: reminder_lead_days rejects negatives, allows 0."""

    def test_definition_create_negative_lead_rejected(self) -> None:
        """DefinitionCreate with reminder_lead_days<0 raises ValidationError."""
        from pydantic import ValidationError

        from app.schemas.item_definition import DefinitionCreate

        with pytest.raises(ValidationError):
            DefinitionCreate(name="Bad", reminder_lead_days=-1)

    def test_definition_update_negative_lead_rejected(self) -> None:
        """DefinitionUpdate with reminder_lead_days<0 raises ValidationError."""
        from pydantic import ValidationError

        from app.schemas.item_definition import DefinitionUpdate

        with pytest.raises(ValidationError):
            DefinitionUpdate(reminder_lead_days=-10)

    def test_definition_create_zero_lead_valid(self) -> None:
        """DefinitionCreate with reminder_lead_days=0 is accepted."""
        from app.schemas.item_definition import DefinitionCreate

        create = DefinitionCreate(name="Item", reminder_lead_days=0)
        assert create.reminder_lead_days == 0

    def test_definition_create_null_lead_valid(self) -> None:
        """DefinitionCreate with reminder_lead_days=None is accepted (inherit)."""
        from app.schemas.item_definition import DefinitionCreate

        create = DefinitionCreate(name="Item", reminder_lead_days=None)
        assert create.reminder_lead_days is None

    def test_user_preferences_update_negative_best_before_rejected(self) -> None:
        """UserPreferencesUpdate with reminder_best_before_lead_days<0 raises ValidationError."""
        from pydantic import ValidationError

        from app.schemas.auth import UserPreferencesUpdate

        with pytest.raises(ValidationError):
            UserPreferencesUpdate(reminder_best_before_lead_days=-1)

    def test_user_preferences_update_negative_warranty_rejected(self) -> None:
        """UserPreferencesUpdate with reminder_warranty_lead_days<0 raises ValidationError."""
        from pydantic import ValidationError

        from app.schemas.auth import UserPreferencesUpdate

        with pytest.raises(ValidationError):
            UserPreferencesUpdate(reminder_warranty_lead_days=-5)

    def test_user_preferences_update_zero_leads_valid(self) -> None:
        """UserPreferencesUpdate with 0 for both lead fields is accepted."""
        from app.schemas.auth import UserPreferencesUpdate

        upd = UserPreferencesUpdate(reminder_best_before_lead_days=0, reminder_warranty_lead_days=0)
        assert upd.reminder_best_before_lead_days == 0
        assert upd.reminder_warranty_lead_days == 0

    def test_user_preferences_update_null_tracked_in_model_fields_set(self) -> None:
        """Explicit null in UserPreferencesUpdate appears in model_fields_set."""
        from app.schemas.auth import UserPreferencesUpdate

        upd = UserPreferencesUpdate(
            **{"reminder_best_before_lead_days": None}  # type: ignore[arg-type]
        )
        assert "reminder_best_before_lead_days" in upd.model_fields_set

    def test_user_preferences_update_omitted_not_in_model_fields_set(self) -> None:
        """Omitted reminder fields are absent from model_fields_set (no-op semantics)."""
        from app.schemas.auth import UserPreferencesUpdate

        upd = UserPreferencesUpdate(preferred_language="en")
        assert "reminder_best_before_lead_days" not in upd.model_fields_set
        assert "reminder_warranty_lead_days" not in upd.model_fields_set


# ---------------------------------------------------------------------------
# 6. Migration round-trip tests (0016 and 0017)
# ---------------------------------------------------------------------------


class _MigrationHelper:
    """Shared migration test helpers."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic as a subprocess; return (returncode, combined output)."""
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

    def _make_temp_db(self, suffix: str = "migtest") -> tuple[str, Path]:
        """Return (url, path) for a disposable temp-file SQLite DB."""
        fd, path_str = tempfile.mkstemp(suffix=".db", prefix=f"omniventory_{suffix}_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        return f"sqlite:///{path_str}", db_path


class TestMigration0016(_MigrationHelper):
    """Migration 0016: add/drop reminder_lead_days on item_definitions."""

    def test_upgrade_adds_column(self) -> None:
        """After upgrade to 0016, item_definitions has reminder_lead_days column."""
        url, db_path = self._make_temp_db("0016up")
        try:
            rc, out = self._run_alembic("upgrade", "0016", url=url)
            assert rc == 0, f"alembic upgrade 0016 failed:\n{out}"

            engine = create_engine(url)
            inspector = inspect(engine)
            columns = {col["name"] for col in inspector.get_columns("item_definitions")}
            assert "reminder_lead_days" in columns, (
                f"reminder_lead_days not in item_definitions columns: {columns}"
            )
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_removes_column(self) -> None:
        """After downgrade from 0016 to 0015, reminder_lead_days is gone."""
        url, db_path = self._make_temp_db("0016down")
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0016", url=url)
            assert rc_up == 0, f"upgrade 0016 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0015", url=url)
            assert rc_down == 0, f"downgrade 0015 failed:\n{out_down}"

            engine = create_engine(url)
            inspector = inspect(engine)
            columns = {col["name"] for col in inspector.get_columns("item_definitions")}
            assert "reminder_lead_days" not in columns, (
                f"reminder_lead_days still present after downgrade: {columns}"
            )
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_existing_rows_are_null_after_upgrade(self) -> None:
        """Pre-existing item_definitions rows are NULL for reminder_lead_days (no backfill)."""
        import sqlite3

        url, db_path = self._make_temp_db("0016null")
        try:
            # Upgrade to 0015 (before this column)
            rc, out = self._run_alembic("upgrade", "0015", url=url)
            assert rc == 0, f"upgrade 0015 failed:\n{out}"

            # Manually insert a row into item_definitions
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode)"
                    " VALUES ('Test', 1, 'pcs', 'exact')"
                )
                conn.commit()
            finally:
                conn.close()

            # Now upgrade to 0016
            rc, out = self._run_alembic("upgrade", "0016", url=url)
            assert rc == 0, f"upgrade 0016 failed:\n{out}"

            # Check the row: reminder_lead_days should be NULL
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT reminder_lead_days FROM item_definitions WHERE name = 'Test'"
                ).fetchone()
                assert row is not None
                assert row[0] is None, f"Expected NULL but got {row[0]}"
            finally:
                conn.close()
        finally:
            if db_path.exists():
                db_path.unlink()


class TestMigration0017(_MigrationHelper):
    """Migration 0017: add/drop reminder lead-day columns on users."""

    def test_upgrade_adds_columns(self) -> None:
        """After upgrade to 0017, users has both new reminder lead-day columns."""
        url, db_path = self._make_temp_db("0017up")
        try:
            rc, out = self._run_alembic("upgrade", "0017", url=url)
            assert rc == 0, f"alembic upgrade 0017 failed:\n{out}"

            engine = create_engine(url)
            inspector = inspect(engine)
            columns = {col["name"] for col in inspector.get_columns("users")}
            assert "reminder_best_before_lead_days" in columns, (
                f"reminder_best_before_lead_days not in users columns: {columns}"
            )
            assert "reminder_warranty_lead_days" in columns, (
                f"reminder_warranty_lead_days not in users columns: {columns}"
            )
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_removes_columns(self) -> None:
        """After downgrade from 0017 to 0016, both new columns are gone from users."""
        url, db_path = self._make_temp_db("0017down")
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0017", url=url)
            assert rc_up == 0, f"upgrade 0017 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0016", url=url)
            assert rc_down == 0, f"downgrade 0016 failed:\n{out_down}"

            engine = create_engine(url)
            inspector = inspect(engine)
            columns = {col["name"] for col in inspector.get_columns("users")}
            assert "reminder_best_before_lead_days" not in columns, (
                f"reminder_best_before_lead_days still present: {columns}"
            )
            assert "reminder_warranty_lead_days" not in columns, (
                f"reminder_warranty_lead_days still present: {columns}"
            )
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_existing_user_rows_are_null_after_upgrade(self) -> None:
        """Pre-existing user rows are NULL for both new columns (no backfill)."""
        import sqlite3

        url, db_path = self._make_temp_db("0017null")
        try:
            # Upgrade to 0016 (before users columns)
            rc, out = self._run_alembic("upgrade", "0016", url=url)
            assert rc == 0, f"upgrade 0016 failed:\n{out}"

            # Manually insert a user row
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "INSERT INTO users (email, password_hash, role, is_active)"
                    " VALUES ('test@example.com', 'hash', 'admin', 1)"
                )
                conn.commit()
            finally:
                conn.close()

            # Now upgrade to 0017
            rc, out = self._run_alembic("upgrade", "0017", url=url)
            assert rc == 0, f"upgrade 0017 failed:\n{out}"

            # Both new columns should be NULL for the existing row
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT reminder_best_before_lead_days, reminder_warranty_lead_days"
                    " FROM users WHERE email = 'test@example.com'"
                ).fetchone()
                assert row is not None
                assert row[0] is None, (
                    f"reminder_best_before_lead_days: expected NULL, got {row[0]}"
                )
                assert row[1] is None, f"reminder_warranty_lead_days: expected NULL, got {row[1]}"
            finally:
                conn.close()
        finally:
            if db_path.exists():
                db_path.unlink()
