"""M1.5 Step 1 tests: uniform error-code contract.

Required coverage (M1.5.md §5 / §10 Step 1):
- AppError → {code, message, params} at the right status.
- Pydantic 422 → validation.invalid_input + fields list.
- Stray HTTPException (SPA 404) → http.404 envelope.
- Auth codes exact (auth.invalid_credentials, auth.setup_already_complete,
  auth.not_authenticated, auth.session_invalid, auth.account_inactive,
  auth.account_disabled).
- User-enumeration safety: both login failures → auth.invalid_credentials.
- No legacy bare-string detail field on any error response.
- ErrorResponse is flat (not nested under detail/error).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1_5_step1_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    """Clear lru_cache on get_settings and get_engine before/after every test."""
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def test_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """Return a TestClient backed by a temp-file SQLite with the full schema.

    Follows the same pattern as test_m1_step3.py: reload all model modules so
    that their tables are correctly registered to Base.metadata, then create
    the schema and seed the item_kinds (normally done by Alembic migrations).
    Uses raise_server_exceptions=False so we can assert on error envelopes.
    """
    import importlib

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-5-step1")
    monkeypatch.setenv("ENVIRONMENT", "test")
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("DATABASE_URL", url)

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
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

    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        # Seed the three system item_kinds (normally done by Alembic migration 0006).
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.models.item_kind import ItemKind

            for code, name in [
                ("durable", "Durable"),
                ("consumable", "Consumable"),
                ("perishable", "Perishable"),
            ]:
                db.add(ItemKind(code=code, name=name, is_system=True))
            db.commit()
        finally:
            db.close()

        yield client

    drop_all_sqlite(Base, engine)
    if db_path.exists():
        db_path.unlink()


def _setup_admin(client: TestClient) -> dict[str, object]:
    """Create the admin user and return the response JSON."""
    resp = client.post(
        "/api/auth/setup",
        json={"email": "admin@example.com", "password": "Password123!"},
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()  # type: ignore[return-value]


def _login(client: TestClient) -> None:
    """Log in as the admin user."""
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Password123!"},
    )
    assert resp.status_code == 200, resp.json()


# ---------------------------------------------------------------------------
# 1. Envelope shape helpers
# ---------------------------------------------------------------------------


def _assert_error_envelope(body: dict[str, object]) -> None:
    """Assert the response is a flat ErrorResponse envelope (no bare detail)."""
    assert "code" in body, f"Missing 'code' in error body: {body}"
    assert "message" in body, f"Missing 'message' in error body: {body}"
    # Envelope is FLAT — must NOT have a 'detail' key at the root.
    assert "detail" not in body, f"Legacy 'detail' key must not appear: {body}"
    # code and message must be non-empty strings.
    assert isinstance(body["code"], str) and body["code"], f"code must be non-empty string: {body}"
    assert isinstance(body["message"], str) and body["message"], (
        f"message must be non-empty string: {body}"
    )


# ---------------------------------------------------------------------------
# 2. AppError → correct envelope
# ---------------------------------------------------------------------------


class TestAppErrorEnvelope:
    """AppError raises produce the flat {code, message, params} envelope."""

    def test_location_not_found_returns_error_envelope(self, test_client: TestClient) -> None:
        """GET /locations/999 → 404 with code=location.not_found."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.get("/api/locations/999")
        assert resp.status_code == 404
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "location.not_found"
        assert body["params"] == {"id": 999}

    def test_category_not_found_returns_error_envelope(self, test_client: TestClient) -> None:
        """GET /categories/999 → 404 with code=category.not_found."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.get("/api/categories/999")
        assert resp.status_code == 404
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "category.not_found"

    def test_stock_instance_not_found(self, test_client: TestClient) -> None:
        """GET /instances/999 → 404 with code=stock_instance.not_found."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.get("/api/instances/999")
        assert resp.status_code == 404
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "stock_instance.not_found"

    def test_tree_cycle_returns_409(self, test_client: TestClient) -> None:
        """Reparenting under self → 409 with code=tree.cycle."""
        _setup_admin(test_client)
        _login(test_client)

        resp_loc = test_client.post("/api/locations", json={"name": "A"})
        assert resp_loc.status_code == 201
        loc_id = resp_loc.json()["id"]

        resp = test_client.patch(f"/api/locations/{loc_id}", json={"parent_id": loc_id})
        assert resp.status_code == 409
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "tree.cycle"
        assert body["params"] is not None
        assert "kind" in body["params"]  # type: ignore[operator]

    def test_tree_delete_has_children_returns_409(self, test_client: TestClient) -> None:
        """Deleting a non-empty location → 409 with code=tree.delete_has_children."""
        _setup_admin(test_client)
        _login(test_client)

        resp_parent = test_client.post("/api/locations", json={"name": "Parent"})
        assert resp_parent.status_code == 201
        parent_id = resp_parent.json()["id"]
        resp_child = test_client.post(
            "/api/locations", json={"name": "Child", "parent_id": parent_id}
        )
        assert resp_child.status_code == 201

        resp = test_client.delete(f"/api/locations/{parent_id}")
        assert resp.status_code == 409
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "tree.delete_has_children"

    def test_serial_requires_qty_one(self, test_client: TestClient) -> None:
        """serial + qty>1 → 422 with code=stock_instance.serial_requires_qty_one."""
        _setup_admin(test_client)
        _login(test_client)

        # Create a definition first.
        resp_defn = test_client.post("/api/definitions", json={"name": "Widget"})
        assert resp_defn.status_code == 201, resp_defn.json()
        defn_id = resp_defn.json()["id"]

        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn_id, "serial": "SN-001", "quantity": "3"},
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "stock_instance.serial_requires_qty_one"

    def test_serial_duplicate_returns_409(self, test_client: TestClient) -> None:
        """Duplicate (definition_id, serial) → 409 with code=stock_instance.serial_duplicate."""
        _setup_admin(test_client)
        _login(test_client)

        resp_defn = test_client.post("/api/definitions", json={"name": "UniqueWidget"})
        assert resp_defn.status_code == 201
        defn_id = resp_defn.json()["id"]

        # First instance.
        r1 = test_client.post("/api/instances", json={"definition_id": defn_id, "serial": "SN-DUP"})
        assert r1.status_code == 201

        # Second instance with the same serial — should conflict.
        r2 = test_client.post("/api/instances", json={"definition_id": defn_id, "serial": "SN-DUP"})
        assert r2.status_code == 409
        body = r2.json()
        _assert_error_envelope(body)
        assert body["code"] == "stock_instance.serial_duplicate"
        assert body["params"] is not None
        assert body["params"]["serial"] == "SN-DUP"  # type: ignore[index]


# ---------------------------------------------------------------------------
# 3. Pydantic 422 → validation.invalid_input + fields
# ---------------------------------------------------------------------------


class TestValidationErrorEnvelope:
    """Pydantic RequestValidationError → validation.invalid_input."""

    def test_pydantic_422_returns_validation_invalid_input(self, test_client: TestClient) -> None:
        """POST /auth/login with missing body → 422 with validation.invalid_input."""
        resp = test_client.post("/api/auth/login", json={})
        assert resp.status_code == 422
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "validation.invalid_input"

    def test_pydantic_422_has_fields_list(self, test_client: TestClient) -> None:
        """validation.invalid_input params must contain a non-empty 'fields' list."""
        resp = test_client.post("/api/auth/login", json={})
        assert resp.status_code == 422
        body = resp.json()
        params = body.get("params")
        assert params is not None, "params must be present for validation errors"
        fields = params.get("fields")  # type: ignore[union-attr]
        assert isinstance(fields, list), f"params.fields must be a list: {params}"
        assert len(fields) > 0, "fields list must not be empty"
        # Each field must have loc and type.
        for f in fields:
            assert "loc" in f, f"field entry must have 'loc': {f}"
            assert "type" in f, f"field entry must have 'type': {f}"

    def test_pydantic_422_no_detail_key(self, test_client: TestClient) -> None:
        """422 body must not have a legacy 'detail' key."""
        resp = test_client.post("/api/auth/login", json={})
        body = resp.json()
        assert "detail" not in body, f"Legacy 'detail' key must not appear: {body}"


# ---------------------------------------------------------------------------
# 4. Stray HTTPException (SPA 404) → http.404
# ---------------------------------------------------------------------------


class TestStrayHTTPException:
    """Stray HTTPException (e.g. the SPA fallback 404) → http.<status> envelope."""

    def test_unregistered_api_path_returns_http_404_envelope(self, test_client: TestClient) -> None:
        """GET /api/nonexistent → 404 with code=http.404 (not a bare detail)."""
        resp = test_client.get("/api/nonexistent-endpoint-that-does-not-exist")
        assert resp.status_code == 404
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "http.404"


# ---------------------------------------------------------------------------
# 5. Auth error codes — exact codes + user-enumeration safety
# ---------------------------------------------------------------------------


class TestAuthErrorCodes:
    """Auth errors must use exact stable codes; user enumeration must be prevented."""

    def test_not_authenticated_on_me_without_session(self, test_client: TestClient) -> None:
        """GET /auth/me without cookie → 401 auth.not_authenticated."""
        resp = test_client.get("/api/auth/me")
        assert resp.status_code == 401
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "auth.not_authenticated"

    def test_invalid_credentials_wrong_password(self, test_client: TestClient) -> None:
        """Wrong password → 401 auth.invalid_credentials."""
        _setup_admin(test_client)

        resp = test_client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 401
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "auth.invalid_credentials"

    def test_invalid_credentials_unknown_email(self, test_client: TestClient) -> None:
        """Unknown email → 401 auth.invalid_credentials (same code, user-enumeration safe)."""
        _setup_admin(test_client)

        resp = test_client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 401
        body = resp.json()
        _assert_error_envelope(body)
        # CRITICAL: must be the same code as wrong password — no enumeration.
        assert body["code"] == "auth.invalid_credentials"

    def test_user_enumeration_safety_same_code_for_both_failures(
        self, test_client: TestClient
    ) -> None:
        """Both login failures must return EXACTLY the same error code."""
        _setup_admin(test_client)

        resp_no_user = test_client.post(
            "/api/auth/login",
            json={"email": "ghost@example.com", "password": "anything"},
        )
        resp_bad_pw = test_client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "wrongpassword"},
        )
        assert (
            resp_no_user.json()["code"] == resp_bad_pw.json()["code"] == "auth.invalid_credentials"
        )

    def test_setup_already_complete_returns_409(self, test_client: TestClient) -> None:
        """Second POST /auth/setup → 409 auth.setup_already_complete."""
        _setup_admin(test_client)

        resp = test_client.post(
            "/api/auth/setup",
            json={"email": "other@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 409
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "auth.setup_already_complete"

    def test_session_invalid_with_bad_cookie(self, test_client: TestClient) -> None:
        """GET /auth/me with a bogus session cookie → 401 auth.session_invalid."""
        _setup_admin(test_client)
        test_client.cookies.set("omniventory_session", "totally-fake-session-id")

        resp = test_client.get("/api/auth/me")
        assert resp.status_code == 401
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "auth.session_invalid"

    def test_account_disabled_returns_401(self, test_client: TestClient) -> None:
        """Login with a disabled account → 401 auth.account_disabled."""

        from sqlalchemy.orm import Session, sessionmaker

        from app.db.base import get_engine

        _setup_admin(test_client)

        # Disable the user directly in the DB.
        engine = get_engine()
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()
        try:
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            user = repo.get_by_email("admin@example.com")
            assert user is not None
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp = test_client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 401
        body = resp.json()
        _assert_error_envelope(body)
        assert body["code"] == "auth.account_disabled"


# ---------------------------------------------------------------------------
# 6. No legacy detail on any status
# ---------------------------------------------------------------------------


class TestNoLegacyDetail:
    """Assert no endpoint returns the legacy bare-string detail field."""

    def test_404_no_detail(self, test_client: TestClient) -> None:
        """A 404 response must not have a bare 'detail' field."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.get("/api/locations/99999")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" not in body

    def test_409_no_detail(self, test_client: TestClient) -> None:
        """A 409 response must not have a bare 'detail' field."""
        _setup_admin(test_client)

        # Second setup → 409.
        resp = test_client.post(
            "/api/auth/setup",
            json={"email": "other@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "detail" not in body

    def test_422_no_detail(self, test_client: TestClient) -> None:
        """A 422 response must not have a bare 'detail' field."""
        resp = test_client.post("/api/auth/login", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" not in body

    def test_401_no_detail(self, test_client: TestClient) -> None:
        """A 401 response must not have a bare 'detail' field."""
        resp = test_client.get("/api/auth/me")
        assert resp.status_code == 401
        body = resp.json()
        assert "detail" not in body
