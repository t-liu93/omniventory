"""Tests for M6 Step 2: user administration + last-admin guard.

Coverage
--------
Repository layer:
- ``count_active_admins`` counts correctly; ignores inactive admins and
  non-admin active users.
- ``list_all`` returns all users including inactive, ordered by id.
- ``set_role``, ``set_active``, ``delete`` apply changes and flush.

Last-admin guard (service layer):
- Demote the only active admin → 409 ``user.last_admin``.
- Deactivate the only active admin → 409.
- Delete the only active admin → 409.
- With a second active admin present, each of those succeeds.
- Demoting/deactivating/deleting a non-admin user always succeeds.
- A combined PATCH (role + is_active) is evaluated on the resulting state.
- Demoting an inactive admin (not the last active admin) succeeds.

Route-level integration:
- ``GET /users`` is accessible to admin, member, and viewer (any authed user).
- ``GET /users`` returns the expected summary shape.
- ``GET /users/{id}``, ``PATCH /users/{id}``, ``DELETE /users/{id}`` require
  MANAGE_USERS: member and viewer get 403 ``auth.forbidden``.
- Admin can GET/PATCH/DELETE a user.
- 404 ``user.not_found`` on all routes for a missing id.
- Invalid role in PATCH → 422 ``validation.invalid_input``.
- Role change and active-flag change round-trip via the API.
- Last-admin guard surfaces as 409 ``user.last_admin`` via the API.
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
# Fixtures (mirrors test_m6_step1.py)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step2_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step2")
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


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """Returns (unauthenticated TestClient, engine) with schema created."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(
    engine: object,
    email: str,
    role: str = "admin",
    is_active: bool = True,
) -> int:
    """Insert a user directly into the DB; return the new user id."""
    from sqlalchemy.orm import sessionmaker as SM

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        repo = UserRepository(db)
        user = repo.create(
            email=email,
            password_hash=hash_password("testpassword"),
            role=role,
            is_active=is_active,
        )
        db.commit()
        return user.id
    finally:
        db.close()


def _login(client: TestClient, email: str, password: str = "testpassword") -> None:
    """Log in the given email and attach the session cookie to *client*."""
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.json()}"


def _create_user_and_login(
    engine: object,
    client: TestClient,
    email: str,
    role: str,
    password: str = "testpassword",
) -> int:
    """Insert user and log in; returns the new user id."""
    uid = _make_user(engine, email, role=role)
    _login(client, email, password)
    return uid


def _fresh_client_for_role(
    base_client: tuple[TestClient, object],
    role: str,
    email: str | None = None,
) -> tuple[TestClient, int]:
    """Create a fresh TestClient (same app) logged in with *role*.

    Returns (client, user_id).
    """
    client, engine = base_client
    from fastapi.testclient import TestClient as FTC

    new_client = FTC(client.app, raise_server_exceptions=True)
    new_client.__enter__()
    addr = email or f"{role}_extra@example.com"
    uid = _make_user(engine, addr, role=role)
    _login(new_client, addr)
    return new_client, uid


# ---------------------------------------------------------------------------
# Class 1: Repository layer
# ---------------------------------------------------------------------------


class TestRepository:
    """Unit tests for the new UserRepository methods."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client

    def _get_repo(self) -> tuple[object, object]:
        """Return (db_session, UserRepository)."""
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(  # type: ignore[arg-type]
            bind=self._engine, autocommit=False, autoflush=False
        )
        db = factory()
        return db, UserRepository(db)

    def test_count_active_admins_zero_when_no_users(self) -> None:
        db, repo = self._get_repo()
        try:
            assert repo.count_active_admins() == 0
        finally:
            db.close()  # type: ignore[union-attr]

    def test_count_active_admins_counts_only_active_admins(self) -> None:
        # Create: 1 active admin, 1 inactive admin, 1 active member
        _make_user(self._engine, "admin_active@t.com", role="admin", is_active=True)
        _make_user(self._engine, "admin_inactive@t.com", role="admin", is_active=False)
        _make_user(self._engine, "member@t.com", role="member", is_active=True)

        db, repo = self._get_repo()
        try:
            assert repo.count_active_admins() == 1
        finally:
            db.close()  # type: ignore[union-attr]

    def test_count_active_admins_two(self) -> None:
        _make_user(self._engine, "a1@t.com", role="admin", is_active=True)
        _make_user(self._engine, "a2@t.com", role="admin", is_active=True)
        db, repo = self._get_repo()
        try:
            assert repo.count_active_admins() == 2
        finally:
            db.close()  # type: ignore[union-attr]

    def test_list_all_includes_inactive(self) -> None:
        uid1 = _make_user(self._engine, "active@t.com", role="admin", is_active=True)
        uid2 = _make_user(self._engine, "inactive@t.com", role="member", is_active=False)

        db, repo = self._get_repo()
        try:
            users = repo.list_all()
            ids = [u.id for u in users]
            assert uid1 in ids
            assert uid2 in ids
        finally:
            db.close()  # type: ignore[union-attr]

    def test_list_all_ordered_by_id(self) -> None:
        _make_user(self._engine, "z@t.com", role="viewer")
        _make_user(self._engine, "a@t.com", role="member")
        db, repo = self._get_repo()
        try:
            users = repo.list_all()
            ids = [u.id for u in users]
            assert ids == sorted(ids)
        finally:
            db.close()  # type: ignore[union-attr]

    def test_set_role_persists(self) -> None:
        uid = _make_user(self._engine, "u@t.com", role="admin")
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(bind=self._engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            repo = UserRepository(db)
            user = repo.get_by_id(uid)
            assert user is not None
            repo.set_role(user, "member")
            db.commit()
        finally:
            db.close()

        db2, repo2 = self._get_repo()
        try:
            user2 = repo2.get_by_id(uid)
            assert user2 is not None
            assert user2.role == "member"
        finally:
            db2.close()  # type: ignore[union-attr]

    def test_set_active_persists(self) -> None:
        uid = _make_user(self._engine, "u2@t.com", is_active=True)
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(bind=self._engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            repo = UserRepository(db)
            user = repo.get_by_id(uid)
            assert user is not None
            repo.set_active(user, False)
            db.commit()
        finally:
            db.close()

        db2, repo2 = self._get_repo()
        try:
            user2 = repo2.get_by_id(uid)
            assert user2 is not None
            assert user2.is_active is False
        finally:
            db2.close()  # type: ignore[union-attr]

    def test_delete_removes_row(self) -> None:
        uid = _make_user(self._engine, "del@t.com", role="member")
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(bind=self._engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            repo = UserRepository(db)
            user = repo.get_by_id(uid)
            assert user is not None
            repo.delete(user)
            db.commit()
        finally:
            db.close()

        db2, repo2 = self._get_repo()
        try:
            assert repo2.get_by_id(uid) is None
        finally:
            db2.close()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Class 2: Last-admin guard (service layer)
# ---------------------------------------------------------------------------


class TestLastAdminGuard:
    """Service-level tests for the last-admin guard logic (§4.1 / §5).

    Each test creates its own session, calls the service, commits (so side
    effects are visible to subsequent sessions), and closes cleanly to avoid
    SQLite locking during teardown.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client

    def _run_service(self, engine: object, fn: object) -> object:
        """Execute *fn(svc)* in a fresh, committed, closed session.

        ``fn`` receives the ``UserAdminService`` and should return a value.
        The session is committed on success and always closed.
        """
        from sqlalchemy.orm import sessionmaker as SM

        from app.services.user_admin import UserAdminService

        factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        svc = UserAdminService(db)
        try:
            result = fn(svc)  # type: ignore[operator]
            db.commit()  # type: ignore[union-attr]
            return result
        except Exception:
            db.rollback()  # type: ignore[union-attr]
            raise
        finally:
            db.close()  # type: ignore[union-attr]

    def _get_user(self, engine: object, uid: int) -> object:
        """Fetch user by id in a fresh session; returns None if deleted."""
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            return UserRepository(db).get_by_id(uid)
        finally:
            db.close()

    def test_demote_only_admin_raises_last_admin(self) -> None:
        from app.core.errors import AppError, ErrorCode

        uid = _make_user(self._engine, "sole_admin@t.com", role="admin")
        with pytest.raises(AppError) as exc_info:
            self._run_service(
                self._engine,
                lambda svc: svc.update_user(
                    uid, role="member", is_active=None, fields_set={"role"}
                ),
            )
        assert exc_info.value.code == ErrorCode.USER_LAST_ADMIN
        assert exc_info.value.status_code == 409

    def test_deactivate_only_admin_raises_last_admin(self) -> None:
        from app.core.errors import AppError, ErrorCode

        uid = _make_user(self._engine, "sole_admin2@t.com", role="admin")
        with pytest.raises(AppError) as exc_info:
            self._run_service(
                self._engine,
                lambda svc: svc.update_user(
                    uid, role=None, is_active=False, fields_set={"is_active"}
                ),
            )
        assert exc_info.value.code == ErrorCode.USER_LAST_ADMIN
        assert exc_info.value.status_code == 409

    def test_delete_only_admin_raises_last_admin(self) -> None:
        from app.core.errors import AppError, ErrorCode

        uid = _make_user(self._engine, "sole_admin3@t.com", role="admin")
        with pytest.raises(AppError) as exc_info:
            self._run_service(self._engine, lambda svc: svc.delete_user(uid))
        assert exc_info.value.code == ErrorCode.USER_LAST_ADMIN
        assert exc_info.value.status_code == 409

    def test_demote_with_second_admin_succeeds(self) -> None:
        uid1 = _make_user(self._engine, "admin1@t.com", role="admin")
        _make_user(self._engine, "admin2@t.com", role="admin")
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(uid1, role="member", is_active=None, fields_set={"role"}),
        )
        user = self._get_user(self._engine, uid1)
        assert user is not None
        assert user.role == "member"  # type: ignore[union-attr]

    def test_deactivate_with_second_admin_succeeds(self) -> None:
        uid1 = _make_user(self._engine, "a1_deact@t.com", role="admin")
        _make_user(self._engine, "a2_deact@t.com", role="admin")
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(uid1, role=None, is_active=False, fields_set={"is_active"}),
        )
        user = self._get_user(self._engine, uid1)
        assert user is not None
        assert user.is_active is False  # type: ignore[union-attr]

    def test_delete_with_second_admin_succeeds(self) -> None:
        uid1 = _make_user(self._engine, "a1_del@t.com", role="admin")
        _make_user(self._engine, "a2_del@t.com", role="admin")
        self._run_service(self._engine, lambda svc: svc.delete_user(uid1))
        assert self._get_user(self._engine, uid1) is None

    def test_demote_non_admin_succeeds(self) -> None:
        uid = _make_user(self._engine, "member@t.com", role="member")
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(uid, role="viewer", is_active=None, fields_set={"role"}),
        )
        user = self._get_user(self._engine, uid)
        assert user is not None
        assert user.role == "viewer"  # type: ignore[union-attr]

    def test_deactivate_non_admin_succeeds(self) -> None:
        uid = _make_user(self._engine, "member2@t.com", role="member")
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(uid, role=None, is_active=False, fields_set={"is_active"}),
        )
        user = self._get_user(self._engine, uid)
        assert user is not None
        assert user.is_active is False  # type: ignore[union-attr]

    def test_delete_non_admin_succeeds(self) -> None:
        uid = _make_user(self._engine, "viewer@t.com", role="viewer")
        self._run_service(self._engine, lambda svc: svc.delete_user(uid))
        assert self._get_user(self._engine, uid) is None

    def test_deactivate_inactive_admin_no_guard(self) -> None:
        """Deactivating an already-inactive admin never triggers the guard."""
        _make_user(self._engine, "active_admin@t.com", role="admin", is_active=True)
        uid_inactive = _make_user(
            self._engine, "inactive_admin@t.com", role="admin", is_active=False
        )
        # Setting an already-inactive admin to is_active=False should be a no-op / succeed
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(
                uid_inactive, role=None, is_active=False, fields_set={"is_active"}
            ),
        )
        user = self._get_user(self._engine, uid_inactive)
        assert user is not None
        assert user.is_active is False  # type: ignore[union-attr]

    def test_combined_patch_both_role_and_is_active_blocked(self) -> None:
        """A combined PATCH that demotes AND deactivates the only admin is blocked."""
        from app.core.errors import AppError, ErrorCode

        uid = _make_user(self._engine, "combo@t.com", role="admin")
        with pytest.raises(AppError) as exc_info:
            self._run_service(
                self._engine,
                lambda svc: svc.update_user(
                    uid,
                    role="member",
                    is_active=False,
                    fields_set={"role", "is_active"},
                ),
            )
        assert exc_info.value.code == ErrorCode.USER_LAST_ADMIN

    def test_promote_to_admin_not_blocked(self) -> None:
        """Promoting a member to admin when an admin already exists is fine."""
        _make_user(self._engine, "existing_admin@t.com", role="admin")
        uid = _make_user(self._engine, "promote_me@t.com", role="member")
        self._run_service(
            self._engine,
            lambda svc: svc.update_user(uid, role="admin", is_active=None, fields_set={"role"}),
        )
        user = self._get_user(self._engine, uid)
        assert user is not None
        assert user.role == "admin"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Class 3: Route-level integration tests
# ---------------------------------------------------------------------------


class TestListUsersEndpoint:
    """GET /users is open to any authenticated user (VIEW)."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        self._client, self._engine = base_client
        # Create an admin (id=1) and a member (id=2) for the list
        self._admin_id = _make_user(self._engine, "admin@t.com", role="admin")
        self._member_id = _make_user(self._engine, "member@t.com", role="member")
        self._viewer_id = _make_user(self._engine, "viewer@t.com", role="viewer")
        # inactive user should appear too
        self._inactive_id = _make_user(
            self._engine, "inactive@t.com", role="member", is_active=False
        )
        yield

    def _get_list_as(self, role: str, email: str) -> object:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        with c:
            _login(c, email)
            return c.get("/api/users")

    def test_admin_can_list(self) -> None:
        resp = self._get_list_as("admin", "admin@t.com")
        import httpx

        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 200

    def test_member_can_list(self) -> None:
        resp = self._get_list_as("member", "member@t.com")
        import httpx

        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 200

    def test_viewer_can_list(self) -> None:
        resp = self._get_list_as("viewer", "viewer@t.com")
        import httpx

        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 200

    def test_unauthenticated_cannot_list(self) -> None:
        resp = self._client.get("/api/users")
        assert resp.status_code == 401

    def test_list_returns_summary_shape(self) -> None:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        with c:
            _login(c, "admin@t.com")
            resp = c.get("/api/users")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # admin + member + viewer + inactive
        # Check required fields on first item
        for item in data:
            assert "id" in item
            assert "email" in item
            assert "role" in item
            assert "is_active" in item
            # UserSummary does NOT include created_at, preferred_language etc.
            assert "created_at" not in item

    def test_list_includes_inactive_users(self) -> None:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        with c:
            _login(c, "admin@t.com")
            resp = c.get("/api/users")
        data = resp.json()
        ids = [u["id"] for u in data]
        assert self._inactive_id in ids


class TestManageUsersEndpoints:
    """GET/PATCH/DELETE /users/{id} require MANAGE_USERS (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> Generator[None]:
        self._client, self._engine = base_client
        self._admin_id = _make_user(self._engine, "admin@t.com", role="admin")
        self._member_id = _make_user(self._engine, "member@t.com", role="member")
        self._viewer_id = _make_user(self._engine, "viewer@t.com", role="viewer")
        yield

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _as_admin(self) -> TestClient:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        c.__enter__()
        _login(c, "admin@t.com")
        return c

    def _as_member(self) -> TestClient:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        c.__enter__()
        _login(c, "member@t.com")
        return c

    def _as_viewer(self) -> TestClient:
        from fastapi.testclient import TestClient as FTC

        c = FTC(self._client.app, raise_server_exceptions=True)
        c.__enter__()
        _login(c, "viewer@t.com")
        return c

    # ------------------------------------------------------------------
    # GET /users/{id} authorization
    # ------------------------------------------------------------------

    def test_admin_can_get_user(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.get(f"/api/users/{self._member_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == self._member_id
        assert data["email"] == "member@t.com"
        assert data["role"] == "member"
        assert "created_at" in data  # full UserResponse

    def test_member_cannot_get_user(self) -> None:
        c = self._as_member()
        with c:
            resp = c.get(f"/api/users/{self._viewer_id}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_viewer_cannot_get_user(self) -> None:
        c = self._as_viewer()
        with c:
            resp = c.get(f"/api/users/{self._member_id}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_get_user_404_for_missing_id(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.get("/api/users/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "user.not_found"

    # ------------------------------------------------------------------
    # PATCH /users/{id} authorization
    # ------------------------------------------------------------------

    def test_admin_can_patch_role(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{self._member_id}", json={"role": "viewer"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "viewer"

    def test_admin_can_patch_is_active(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{self._member_id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_member_cannot_patch_user(self) -> None:
        c = self._as_member()
        with c:
            resp = c.patch(f"/api/users/{self._viewer_id}", json={"role": "admin"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_viewer_cannot_patch_user(self) -> None:
        c = self._as_viewer()
        with c:
            resp = c.patch(f"/api/users/{self._member_id}", json={"is_active": False})
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_patch_404_for_missing_id(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch("/api/users/99999", json={"role": "viewer"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "user.not_found"

    def test_patch_invalid_role_rejected(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{self._member_id}", json={"role": "superadmin"})
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # DELETE /users/{id} authorization
    # ------------------------------------------------------------------

    def test_admin_can_delete_non_admin_user(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.delete(f"/api/users/{self._viewer_id}")
        assert resp.status_code == 204

    def test_member_cannot_delete_user(self) -> None:
        c = self._as_member()
        with c:
            resp = c.delete(f"/api/users/{self._viewer_id}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_viewer_cannot_delete_user(self) -> None:
        c = self._as_viewer()
        with c:
            resp = c.delete(f"/api/users/{self._member_id}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_delete_404_for_missing_id(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.delete("/api/users/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "user.not_found"

    # ------------------------------------------------------------------
    # Last-admin guard via API
    # ------------------------------------------------------------------

    def test_api_demote_only_admin_is_409(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{self._admin_id}", json={"role": "member"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "user.last_admin"

    def test_api_deactivate_only_admin_is_409(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{self._admin_id}", json={"is_active": False})
        assert resp.status_code == 409
        assert resp.json()["code"] == "user.last_admin"

    def test_api_delete_only_admin_is_409(self) -> None:
        c = self._as_admin()
        with c:
            resp = c.delete(f"/api/users/{self._admin_id}")
        assert resp.status_code == 409
        assert resp.json()["code"] == "user.last_admin"

    def test_api_demote_with_second_admin_succeeds(self) -> None:
        # Create a second admin
        second_admin_id = _make_user(self._engine, "admin2@t.com", role="admin")
        c = self._as_admin()
        with c:
            resp = c.patch(f"/api/users/{second_admin_id}", json={"role": "member"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "member"

    def test_api_delete_with_second_admin_succeeds(self) -> None:
        second_admin_id = _make_user(self._engine, "admin3@t.com", role="admin")
        c = self._as_admin()
        with c:
            resp = c.delete(f"/api/users/{second_admin_id}")
        assert resp.status_code == 204

    # ------------------------------------------------------------------
    # Persistence verification
    # ------------------------------------------------------------------

    def test_role_change_persists(self) -> None:
        """Patching role and re-fetching confirms the DB was updated."""
        c = self._as_admin()
        with c:
            patch_resp = c.patch(f"/api/users/{self._member_id}", json={"role": "viewer"})
            assert patch_resp.status_code == 200
            get_resp = c.get(f"/api/users/{self._member_id}")
        assert get_resp.json()["role"] == "viewer"

    def test_deactivate_persists(self) -> None:
        c = self._as_admin()
        with c:
            patch_resp = c.patch(f"/api/users/{self._member_id}", json={"is_active": False})
            assert patch_resp.status_code == 200
            get_resp = c.get(f"/api/users/{self._member_id}")
        assert get_resp.json()["is_active"] is False

    def test_reactivate_persists(self) -> None:
        inactive_id = _make_user(self._engine, "was_inactive@t.com", role="viewer", is_active=False)
        c = self._as_admin()
        with c:
            patch_resp = c.patch(f"/api/users/{inactive_id}", json={"is_active": True})
            assert patch_resp.status_code == 200
            get_resp = c.get(f"/api/users/{inactive_id}")
        assert get_resp.json()["is_active"] is True

    def test_delete_removes_user_from_list(self) -> None:
        extra_id = _make_user(self._engine, "todelete@t.com", role="viewer")
        c = self._as_admin()
        with c:
            del_resp = c.delete(f"/api/users/{extra_id}")
            assert del_resp.status_code == 204
            list_resp = c.get("/api/users")
        ids = [u["id"] for u in list_resp.json()]
        assert extra_id not in ids
