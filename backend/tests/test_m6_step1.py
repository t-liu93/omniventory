"""Tests for M6 Step 1: permission matrix + require_permission + enforcement sweep.

Coverage
--------
- ``has_permission`` truth table for every (role, permission) pair (§2 matrix).
- Unknown role → all False; unknown permission → all False.
- ``VALID_ROLES`` contains exactly admin / member / viewer.
- Enforcement by role using the FastAPI TestClient:
    * viewer  → 403 ``auth.forbidden`` on representative data-mutating routes
               from each gated router class; 200 on corresponding read routes.
    * member  → 200 on data mutations; 403 on settings PATCH and POST /reminders/run.
    * admin   → 200 on data mutations AND settings.
    * self routes are ungated: viewer can PATCH /auth/me without 403.
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
# Unit tests — permission matrix
# ---------------------------------------------------------------------------


class TestPermissionMatrix:
    """has_permission truth table — must match M6 §2 exactly."""

    def test_viewer_has_view(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("viewer", Permission.VIEW) is True

    def test_viewer_no_edit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("viewer", Permission.EDIT) is False

    def test_viewer_no_manage_users(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("viewer", Permission.MANAGE_USERS) is False

    def test_viewer_no_manage_settings(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("viewer", Permission.MANAGE_SETTINGS) is False

    def test_viewer_no_view_audit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("viewer", Permission.VIEW_AUDIT) is False

    def test_member_has_view(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("member", Permission.VIEW) is True

    def test_member_has_edit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("member", Permission.EDIT) is True

    def test_member_no_manage_users(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("member", Permission.MANAGE_USERS) is False

    def test_member_no_manage_settings(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("member", Permission.MANAGE_SETTINGS) is False

    def test_member_no_view_audit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("member", Permission.VIEW_AUDIT) is False

    def test_admin_has_view(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("admin", Permission.VIEW) is True

    def test_admin_has_edit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("admin", Permission.EDIT) is True

    def test_admin_has_manage_users(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("admin", Permission.MANAGE_USERS) is True

    def test_admin_has_manage_settings(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("admin", Permission.MANAGE_SETTINGS) is True

    def test_admin_has_view_audit(self) -> None:
        from app.auth.permissions import Permission, has_permission

        assert has_permission("admin", Permission.VIEW_AUDIT) is True

    def test_unknown_role_all_perms_false(self) -> None:
        from app.auth.permissions import Permission, has_permission

        for perm in (
            Permission.VIEW,
            Permission.EDIT,
            Permission.MANAGE_USERS,
            Permission.MANAGE_SETTINGS,
            Permission.VIEW_AUDIT,
        ):
            assert has_permission("superadmin", perm) is False
            assert has_permission("", perm) is False
            assert has_permission("   ", perm) is False

    def test_unknown_permission_returns_false(self) -> None:
        from app.auth.permissions import has_permission

        assert has_permission("admin", "fly") is False

    def test_valid_roles_set(self) -> None:
        from app.auth.permissions import VALID_ROLES

        assert frozenset({"admin", "member", "viewer"}) == VALID_ROLES

    def test_permissions_dict_keys_match_valid_roles(self) -> None:
        from app.auth.permissions import PERMISSIONS, VALID_ROLES

        assert set(PERMISSIONS.keys()) == set(VALID_ROLES)


# ---------------------------------------------------------------------------
# Integration fixtures (mirrors the test_m5_* pattern)
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
    """Temp-file SQLite DB patched into DATABASE_URL."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step1_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step1")
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
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod

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


def _seed_db(engine: object) -> None:
    """Seed item kinds (required by definitions / instances)."""
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


def _create_user_and_login(
    engine: object,
    client: TestClient,
    email: str,
    password: str,
    role: str,
) -> None:
    """Insert a user with the given role into the DB and log in via the API.

    After this call the *client* carries the session cookie.
    """
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

    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed for {email}: {resp.json()}"


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """Returns (unauthenticated TestClient, engine) with schema created.

    Tests that need a specific role should call ``_create_user_and_login``
    to create the user and get a session-carrying client.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_db(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# Role-specific authenticated clients
# ---------------------------------------------------------------------------


def _make_client_for_role(
    base_client: tuple[TestClient, object],
    role: str,
    suffix: str = "",
) -> TestClient:
    """Log in (or register) a user with *role* and return the same TestClient."""
    client, engine = base_client
    email = f"{role}{suffix}@example.com"
    _create_user_and_login(engine, client, email, f"pass{role}", role)
    return client


# ---------------------------------------------------------------------------
# Helpers to create prerequisite data (as admin, on an admin client)
# ---------------------------------------------------------------------------


def _admin_client(base_client: tuple[TestClient, object]) -> TestClient:
    return _make_client_for_role(base_client, "admin")


def _create_location(client: TestClient, name: str = "Test Room") -> int:
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def _create_category(client: TestClient, name: str = "Test Cat") -> int:
    resp = client.post("/api/categories", json={"name": name})
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def _create_definition(
    client: TestClient,
    name: str = "Test Widget",
    category_id: int | None = None,
) -> int:
    payload: dict[str, object] = {"name": name, "stock_tracking_mode": "none"}
    if category_id is not None:
        payload["category_id"] = category_id
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def _create_instance(client: TestClient, definition_id: int) -> int:
    resp = client.post("/api/instances", json={"definition_id": definition_id})
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def _create_tag(client: TestClient, name: str = "testag") -> int:
    resp = client.post("/api/tags", json={"name": name})
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def _create_note(client: TestClient, definition_id: int) -> int:
    resp = client.post(
        "/api/notes",
        json={"model_type": "item_definition", "model_id": definition_id, "body": "hello"},
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Helper: new client for a role that shares the same app/db
# ---------------------------------------------------------------------------


def _fresh_client_for_role(
    base_client: tuple[TestClient, object],
    role: str,
    suffix: str = "2",
) -> TestClient:
    """Create a second client (same app) logged in as the given role."""
    client, engine = base_client
    # Return a fresh TestClient wrapping the same app (shares db engine).
    from fastapi.testclient import TestClient as FTC

    new_client = FTC(client.app, raise_server_exceptions=True)
    new_client.__enter__()
    email = f"{role}{suffix}@example.com"
    _create_user_and_login(engine, new_client, email, f"pass{role}2", role)
    return new_client


# ---------------------------------------------------------------------------
# Class 1: Viewer is denied on data mutations
# ---------------------------------------------------------------------------


class TestViewerDeniedOnMutations:
    """A viewer gets 403 auth.forbidden on every gated mutation."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        """Bootstrap prerequisite data as admin, then build the viewer client."""
        admin = _admin_client(base_client)

        # Create prerequisites as admin.
        self.loc_id = _create_location(admin, "Hallway")
        self.cat_id = _create_category(admin, "Electronics")
        self.def_id = _create_definition(admin, "Gadget", category_id=self.cat_id)
        self.inst_id = _create_instance(admin, self.def_id)
        self.tag_id = _create_tag(admin, "mytag")
        self.note_id = _create_note(admin, self.def_id)

        # Build viewer client.
        self.viewer = _fresh_client_for_role(base_client, "viewer")
        yield
        self.viewer.__exit__(None, None, None)

    def _assert_forbidden(self, resp: object) -> None:
        import httpx

        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.json()}"
        body = resp.json()
        assert body["code"] == "auth.forbidden", f"Wrong code: {body}"

    # Locations
    def test_viewer_cannot_create_location(self) -> None:
        self._assert_forbidden(self.viewer.post("/api/locations", json={"name": "X"}))

    def test_viewer_cannot_update_location(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/locations/{self.loc_id}", json={"name": "Y"})
        )

    def test_viewer_cannot_delete_location(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/locations/{self.loc_id}"))

    # Categories
    def test_viewer_cannot_create_category(self) -> None:
        self._assert_forbidden(self.viewer.post("/api/categories", json={"name": "Toys"}))

    def test_viewer_cannot_update_category(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/categories/{self.cat_id}", json={"name": "Y"})
        )

    def test_viewer_cannot_delete_category(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/categories/{self.cat_id}"))

    # Definitions
    def test_viewer_cannot_create_definition(self) -> None:
        self._assert_forbidden(
            self.viewer.post("/api/definitions", json={"name": "Z", "stock_tracking_mode": "none"})
        )

    def test_viewer_cannot_update_definition(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/definitions/{self.def_id}", json={"name": "Z2"})
        )

    def test_viewer_cannot_delete_definition(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/definitions/{self.def_id}"))

    # Instances
    def test_viewer_cannot_create_instance(self) -> None:
        self._assert_forbidden(
            self.viewer.post("/api/instances", json={"definition_id": self.def_id})
        )

    def test_viewer_cannot_update_instance(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/instances/{self.inst_id}", json={"notes": "x"})
        )

    def test_viewer_cannot_delete_instance(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/instances/{self.inst_id}"))

    # Tags
    def test_viewer_cannot_create_tag(self) -> None:
        self._assert_forbidden(self.viewer.post("/api/tags", json={"name": "newtag"}))

    def test_viewer_cannot_update_tag(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/tags/{self.tag_id}", json={"name": "updated"})
        )

    def test_viewer_cannot_delete_tag(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/tags/{self.tag_id}"))

    def test_viewer_cannot_set_tag_links(self) -> None:
        self._assert_forbidden(
            self.viewer.put(
                "/api/tags/links",
                json={
                    "model_type": "item_definition",
                    "model_id": self.def_id,
                    "tag_ids": [self.tag_id],
                },
            )
        )

    # Notes
    def test_viewer_cannot_create_note(self) -> None:
        self._assert_forbidden(
            self.viewer.post(
                "/api/notes",
                json={
                    "model_type": "item_definition",
                    "model_id": self.def_id,
                    "body": "hi",
                },
            )
        )

    def test_viewer_cannot_update_note(self) -> None:
        self._assert_forbidden(
            self.viewer.patch(f"/api/notes/{self.note_id}", json={"body": "changed"})
        )

    def test_viewer_cannot_delete_note(self) -> None:
        self._assert_forbidden(self.viewer.delete(f"/api/notes/{self.note_id}"))

    # Barcodes
    def test_viewer_cannot_bind_barcode(self) -> None:
        self._assert_forbidden(
            self.viewer.post(
                f"/api/definitions/{self.def_id}/barcodes",
                json={"code": "123456789"},
            )
        )


# ---------------------------------------------------------------------------
# Class 2: Viewer is allowed on read routes
# ---------------------------------------------------------------------------


class TestViewerAllowedOnReads:
    """A viewer gets 200 on read (GET) routes — they must NOT be over-gated."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        admin = _admin_client(base_client)
        self.loc_id = _create_location(admin)
        self.cat_id = _create_category(admin)
        self.def_id = _create_definition(admin)
        self.inst_id = _create_instance(admin, self.def_id)
        self.viewer = _fresh_client_for_role(base_client, "viewer", suffix="3")
        yield
        self.viewer.__exit__(None, None, None)

    def test_viewer_can_list_locations(self) -> None:
        assert self.viewer.get("/api/locations").status_code == 200

    def test_viewer_can_get_location(self) -> None:
        assert self.viewer.get(f"/api/locations/{self.loc_id}").status_code == 200

    def test_viewer_can_list_categories(self) -> None:
        assert self.viewer.get("/api/categories").status_code == 200

    def test_viewer_can_list_definitions(self) -> None:
        assert self.viewer.get("/api/definitions").status_code == 200

    def test_viewer_can_get_definition(self) -> None:
        assert self.viewer.get(f"/api/definitions/{self.def_id}").status_code == 200

    def test_viewer_can_list_instances(self) -> None:
        assert self.viewer.get("/api/instances").status_code == 200

    def test_viewer_can_get_instance(self) -> None:
        assert self.viewer.get(f"/api/instances/{self.inst_id}").status_code == 200

    def test_viewer_can_list_tags(self) -> None:
        assert self.viewer.get("/api/tags").status_code == 200

    def test_viewer_can_list_notes(self) -> None:
        resp = self.viewer.get(
            "/api/notes",
            params={"model_type": "item_definition", "model_id": self.def_id},
        )
        assert resp.status_code == 200

    def test_viewer_can_get_settings(self) -> None:
        # GET /settings is a read; viewer should be allowed.
        assert self.viewer.get("/api/settings").status_code == 200


# ---------------------------------------------------------------------------
# Class 3: Member is allowed on data but denied on settings
# ---------------------------------------------------------------------------


class TestMemberPermissions:
    """Member can CRUD data but cannot reach settings or reminders/run."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        admin = _admin_client(base_client)
        self.cat_id = _create_category(admin, "M Cat")
        self.loc_id = _create_location(admin, "M Room")
        self.def_id = _create_definition(admin, "M Widget", category_id=self.cat_id)
        self.inst_id = _create_instance(admin, self.def_id)
        self.member = _fresh_client_for_role(base_client, "member", suffix="4")
        yield
        self.member.__exit__(None, None, None)

    def test_member_can_create_location(self) -> None:
        resp = self.member.post("/api/locations", json={"name": "Member Room"})
        assert resp.status_code == 201

    def test_member_can_create_category(self) -> None:
        resp = self.member.post("/api/categories", json={"name": "Member Cat"})
        assert resp.status_code == 201

    def test_member_can_create_definition(self) -> None:
        resp = self.member.post(
            "/api/definitions", json={"name": "Member Widget", "stock_tracking_mode": "none"}
        )
        assert resp.status_code == 201

    def test_member_can_create_instance(self) -> None:
        resp = self.member.post("/api/instances", json={"definition_id": self.def_id})
        assert resp.status_code == 201

    def test_member_cannot_patch_settings(self) -> None:
        resp = self.member.patch("/api/settings", json={})
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_member_cannot_post_email_test(self) -> None:
        resp = self.member.post("/api/settings/email/test")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_member_cannot_post_mqtt_test(self) -> None:
        resp = self.member.post("/api/settings/mqtt/test")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_member_cannot_run_reminders(self) -> None:
        resp = self.member.post("/api/reminders/run")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_member_can_create_tag(self) -> None:
        resp = self.member.post("/api/tags", json={"name": "membertag"})
        assert resp.status_code == 201

    def test_member_can_create_note(self) -> None:
        resp = self.member.post(
            "/api/notes",
            json={
                "model_type": "item_definition",
                "model_id": self.def_id,
                "body": "Member note",
            },
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Class 4: Admin is allowed on data mutations AND settings
# ---------------------------------------------------------------------------


class TestAdminPermissions:
    """Admin can do everything: data mutations and settings."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        self.admin = _admin_client(base_client)
        self.def_id = _create_definition(self.admin, "Admin Widget")

    def test_admin_can_create_location(self) -> None:
        resp = self.admin.post("/api/locations", json={"name": "Admin Room"})
        assert resp.status_code == 201

    def test_admin_can_patch_settings(self) -> None:
        resp = self.admin.patch("/api/settings", json={})
        assert resp.status_code == 200

    def test_admin_can_run_reminders(self) -> None:
        resp = self.admin.post("/api/reminders/run")
        assert resp.status_code == 200

    def test_admin_can_create_tag(self) -> None:
        resp = self.admin.post("/api/tags", json={"name": "admintag"})
        assert resp.status_code == 201

    def test_admin_can_create_note(self) -> None:
        resp = self.admin.post(
            "/api/notes",
            json={
                "model_type": "item_definition",
                "model_id": self.def_id,
                "body": "Admin note",
            },
        )
        assert resp.status_code == 201

    def test_admin_can_post_email_test(self) -> None:
        # SMTP not configured → ok=False, but HTTP 200 (diagnostic endpoint).
        resp = self.admin.post("/api/settings/email/test")
        assert resp.status_code == 200

    def test_admin_can_post_mqtt_test(self) -> None:
        resp = self.admin.post("/api/settings/mqtt/test")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Class 5: Self routes are ungated (viewer can PATCH /auth/me)
# ---------------------------------------------------------------------------


class TestSelfRoutesUngated:
    """Self routes must be accessible to all roles without a permission check."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        self.viewer = _fresh_client_for_role(base_client, "viewer", suffix="5")
        yield
        self.viewer.__exit__(None, None, None)

    def test_viewer_can_get_own_profile(self) -> None:
        resp = self.viewer.get("/api/auth/me")
        assert resp.status_code == 200

    def test_viewer_can_patch_own_profile(self) -> None:
        # PATCH /auth/me (language preference etc.) is a self route — no permission gate.
        resp = self.viewer.patch("/api/auth/me", json={"preferred_language": "en"})
        assert resp.status_code == 200

    def test_viewer_can_access_notifications_inbox(self) -> None:
        # GET /notifications is self — viewer should see it without 403.
        resp = self.viewer.get("/api/notifications")
        assert resp.status_code == 200

    def test_viewer_can_mark_notifications_read_all(self) -> None:
        # POST /notifications/read-all is self.
        resp = self.viewer.post("/api/notifications/read-all")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Class 6: Unauthenticated → 401 (not 403)
# ---------------------------------------------------------------------------


class TestUnauthenticatedStillGets401:
    """An unauthenticated request must get 401, not 403."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        client, _ = base_client
        from fastapi.testclient import TestClient as FTC

        # A fresh client with no cookies = unauthenticated.
        self.anon = FTC(client.app, raise_server_exceptions=True)
        self.anon.__enter__()

    def teardown_method(self) -> None:
        self.anon.__exit__(None, None, None)

    def test_anon_post_location_gets_401(self) -> None:
        resp = self.anon.post("/api/locations", json={"name": "X"})
        assert resp.status_code == 401

    def test_anon_patch_settings_gets_401(self) -> None:
        resp = self.anon.patch("/api/settings", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Class 7: error code integrity
# ---------------------------------------------------------------------------


class TestErrorCodeIntegrity:
    """auth.forbidden is registered in ErrorCode and produces the correct body."""

    def test_forbidden_error_code_exists(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.FORBIDDEN == "auth.forbidden"

    def test_forbidden_has_default_message(self) -> None:
        from app.core.errors import AppError, ErrorCode

        err = AppError(ErrorCode.FORBIDDEN, status_code=403)
        assert err.status_code == 403
        assert "permission" in err.message.lower()

    def test_require_permission_raises_forbidden(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """A viewer trying a mutation gets exactly auth.forbidden (not some other code)."""
        admin = _admin_client(base_client)
        viewer = _fresh_client_for_role(base_client, "viewer", suffix="6")
        try:
            _create_definition(admin, "FC Widget")
            resp = viewer.post("/api/locations", json={"name": "Forbidden"})
            assert resp.status_code == 403
            body = resp.json()
            assert body["code"] == "auth.forbidden"
            assert body["message"]  # non-empty message
        finally:
            viewer.__exit__(None, None, None)
