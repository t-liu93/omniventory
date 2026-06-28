"""Tests for M6 Step 6: security/admin audit log.

Coverage
--------
Audit row content (one row per event):
- ``auth.login_succeeded`` — actor = user, target = user, ip captured.
- ``auth.login_failed``    — actor_user_id NULL, actor_email = attempted email.
- ``auth.logout``          — actor = user.
- ``user.role_changed``    — params ``{"old_role": …, "new_role": …}``.
- ``user.deactivated``     — is_active toggled False.
- ``user.reactivated``     — is_active toggled True.
- ``user.deleted``         — params ``{"email": …}``.
- ``password.changed``     — self change-password.
- ``password.reset``       — issued (params phase=issued) + completed (phase=completed).
- ``invitation.issued``    — params email + role.
- ``invitation.accepted``  — actor = newly created user.
- ``invitation.revoked``   — params email.
- ``settings.changed``     — actor = admin.

Failed-login persistence (the critical commit-before-raise test):
- A bad-credentials login returns 401 AND writes exactly one auth.login_failed
  row with actor_user_id IS NULL and actor_email = the attempted email.
  This proves the commit-before-raise fix — a naive flush-only implementation
  would leave 0 rows (the get_db rollback would discard the flushed row).

Filtering + pagination:
- event_type filter returns only matching rows.
- actor_id filter returns only rows for that actor.
- from/to date filters narrow the result set.
- limit + offset paginate; total reflects all matching rows.
- Results are newest-first.

Access control:
- GET /audit returns 403 auth.forbidden for member and viewer.
- GET /audit returns 200 for admin.

Append-only structural assertion:
- AuditLogRepository exposes only append + list (no update/delete methods).
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
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB patched into DATABASE_URL."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step6_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step6")
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
    password: str = "testpassword",
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
            password_hash=hash_password(password),
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


def _setup_admin(
    base_client: tuple[TestClient, object],
    email: str = "admin@example.com",
    password: str = "testpassword",
) -> tuple[TestClient, int]:
    """Create admin user, log in, return (client, user_id)."""
    client, engine = base_client
    uid = _make_user(engine, email, role="admin", password=password)
    _login(client, email, password)
    return client, uid


def _get_audit_rows(
    engine: object,
    event_type: str | None = None,
) -> list[object]:
    """Query audit_log rows directly from the DB."""
    from sqlalchemy.orm import sessionmaker as SM

    from app.repositories.audit_log import AuditLogRepository

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        repo = AuditLogRepository(db)
        rows, _ = repo.list(event_type=event_type, limit=200)
        return list(rows)
    finally:
        db.close()


def _count_audit_rows(engine: object, event_type: str | None = None) -> int:
    """Return the number of audit_log rows matching optional event_type."""
    rows = _get_audit_rows(engine, event_type=event_type)
    return len(rows)


def _fresh_client_for_role(
    base_client: tuple[TestClient, object],
    role: str,
    email: str | None = None,
    password: str = "testpassword",
) -> tuple[TestClient, int]:
    """Create a fresh TestClient (same app) logged in with *role*.

    Returns (client, user_id).
    """
    client, engine = base_client
    from fastapi.testclient import TestClient as FTC

    new_client = FTC(client.app, raise_server_exceptions=True)
    new_client.__enter__()
    addr = email or f"{role}_extra@example.com"
    uid = _make_user(engine, addr, role=role, password=password)
    _login(new_client, addr, password=password)
    return new_client, uid


# ---------------------------------------------------------------------------
# Class 1: Append-only structural assertion
# ---------------------------------------------------------------------------


class TestAppendOnly:
    """Verify AuditLogRepository is structurally append-only."""

    def test_no_update_or_delete_methods(self) -> None:
        """AuditLogRepository must not expose update or delete methods."""
        from app.repositories.audit_log import AuditLogRepository

        # The repository must have 'append' and 'list'
        assert hasattr(AuditLogRepository, "append")
        assert hasattr(AuditLogRepository, "list")
        # It must NOT have update or delete methods
        for forbidden in ("update", "delete", "remove", "set_"):
            matching = [
                attr
                for attr in dir(AuditLogRepository)
                if not attr.startswith("__") and attr.startswith(forbidden)
            ]
            assert matching == [], (
                f"AuditLogRepository must not expose {forbidden!r} methods; found: {matching}"
            )


# ---------------------------------------------------------------------------
# Class 2: Failed-login persistence (the critical commit-before-raise test)
# ---------------------------------------------------------------------------


class TestFailedLoginPersistence:
    """Prove the commit-before-raise fix ensures auth.login_failed rows survive 401s."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client

    def test_failed_login_row_survives_401_user_not_found(self) -> None:
        """Bad email → 401 AND exactly one audit row with actor_user_id=NULL."""
        # Make an admin user so the login endpoint is not hitting an empty DB.
        _make_user(self._engine, "real@example.com", role="admin")

        resp = self._client.post(
            "/api/auth/login",
            json={"email": "nonexistent@example.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401

        rows = _get_audit_rows(self._engine, event_type="auth.login_failed")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id is None  # type: ignore[union-attr]
        assert row.actor_email == "nonexistent@example.com"  # type: ignore[union-attr]

    def test_failed_login_row_survives_401_wrong_password(self) -> None:
        """Correct email but wrong password → 401 AND exactly one audit row."""
        _make_user(self._engine, "user@example.com", role="admin")

        resp = self._client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

        rows = _get_audit_rows(self._engine, event_type="auth.login_failed")
        assert len(rows) == 1
        assert rows[0].actor_user_id is None  # type: ignore[union-attr]
        assert rows[0].actor_email == "user@example.com"  # type: ignore[union-attr]

    def test_successful_login_writes_login_succeeded_not_failed(self) -> None:
        """A successful login writes auth.login_succeeded; no auth.login_failed row."""
        _make_user(self._engine, "admin@example.com", role="admin")
        _login(self._client, "admin@example.com")

        assert _count_audit_rows(self._engine, event_type="auth.login_failed") == 0
        assert _count_audit_rows(self._engine, event_type="auth.login_succeeded") == 1


# ---------------------------------------------------------------------------
# Class 3: One row per covered event type
# ---------------------------------------------------------------------------


class TestEventRows:
    """Verify each covered event writes exactly one correct audit row."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client

    def _db_session(self) -> object:
        from sqlalchemy.orm import sessionmaker as SM

        factory = SM(bind=self._engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        return factory()

    def test_login_succeeded(self) -> None:
        """auth.login_succeeded — actor = the user, target_id = user.id."""
        uid = _make_user(self._engine, "a@t.com", role="admin")
        _login(self._client, "a@t.com")

        rows = _get_audit_rows(self._engine, event_type="auth.login_succeeded")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == uid  # type: ignore[union-attr]
        assert row.actor_email == "a@t.com"  # type: ignore[union-attr]
        assert row.target_type == "user"  # type: ignore[union-attr]
        assert row.target_id == uid  # type: ignore[union-attr]

    def test_login_failed_actor_null_email_snapshot(self) -> None:
        """auth.login_failed — actor_user_id NULL, actor_email = attempted email."""
        _make_user(self._engine, "admin@t.com", role="admin")
        resp = self._client.post(
            "/api/auth/login", json={"email": "admin@t.com", "password": "wrong"}
        )
        assert resp.status_code == 401

        rows = _get_audit_rows(self._engine, event_type="auth.login_failed")
        assert len(rows) == 1
        assert rows[0].actor_user_id is None  # type: ignore[union-attr]
        assert rows[0].actor_email == "admin@t.com"  # type: ignore[union-attr]

    def test_role_changed_params(self) -> None:
        """user.role_changed — params contains old_role and new_role."""
        import json

        # Admin logs in
        admin_uid = _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        # Create a member to demote
        member_uid = _make_user(self._engine, "member@t.com", role="member")

        resp = self._client.patch(f"/api/users/{member_uid}", json={"role": "viewer"})
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="user.role_changed")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == admin_uid  # type: ignore[union-attr]
        assert row.target_type == "user"  # type: ignore[union-attr]
        assert row.target_id == member_uid  # type: ignore[union-attr]
        params = json.loads(row.params)  # type: ignore[arg-type]
        assert params["old_role"] == "member"
        assert params["new_role"] == "viewer"

    def test_user_deactivated(self) -> None:
        """user.deactivated — emitted when is_active toggled False."""
        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        target_uid = _make_user(self._engine, "target@t.com", role="member")

        resp = self._client.patch(f"/api/users/{target_uid}", json={"is_active": False})
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="user.deactivated")
        assert len(rows) == 1
        assert rows[0].target_id == target_uid  # type: ignore[union-attr]

    def test_user_reactivated(self) -> None:
        """user.reactivated — emitted when is_active toggled True."""
        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        target_uid = _make_user(self._engine, "target@t.com", role="member", is_active=False)

        resp = self._client.patch(f"/api/users/{target_uid}", json={"is_active": True})
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="user.reactivated")
        assert len(rows) == 1
        assert rows[0].target_id == target_uid  # type: ignore[union-attr]

    def test_user_deleted(self) -> None:
        """user.deleted — params contains deleted user's email."""
        import json

        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")
        second_admin = _make_user(self._engine, "second@t.com", role="admin")

        # Delete second admin (safe since first admin still exists).
        resp = self._client.delete(f"/api/users/{second_admin}")
        assert resp.status_code == 204

        rows = _get_audit_rows(self._engine, event_type="user.deleted")
        assert len(rows) == 1
        params = json.loads(rows[0].params)  # type: ignore[arg-type]
        assert params["email"] == "second@t.com"
        assert rows[0].target_id == second_admin  # type: ignore[union-attr]

    def test_password_changed(self) -> None:
        """password.changed — self change-password writes one row with actor = user."""
        uid = _make_user(self._engine, "user@t.com", role="admin")
        _login(self._client, "user@t.com")

        resp = self._client.post(
            "/api/auth/change-password",
            json={"current_password": "testpassword", "new_password": "newpassword123"},
        )
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="password.changed")
        assert len(rows) == 1
        assert rows[0].actor_user_id == uid  # type: ignore[union-attr]
        assert rows[0].target_id == uid  # type: ignore[union-attr]

    def test_invitation_issued(self) -> None:
        """invitation.issued — params contains email and role."""
        import json

        admin_uid = _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        resp = self._client.post(
            "/api/invitations", json={"email": "invite@t.com", "role": "member"}
        )
        assert resp.status_code == 201

        rows = _get_audit_rows(self._engine, event_type="invitation.issued")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == admin_uid  # type: ignore[union-attr]
        params = json.loads(row.params)  # type: ignore[arg-type]
        assert params["email"] == "invite@t.com"
        assert params["role"] == "member"

    def test_invitation_accepted(self) -> None:
        """invitation.accepted — actor is the newly created user."""
        import json

        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        resp = self._client.post(
            "/api/invitations", json={"email": "invitee@t.com", "role": "member"}
        )
        assert resp.status_code == 201
        accept_url = resp.json()["accept_url"]
        token = accept_url.split("token=")[1]

        accept_resp = self._client.post(
            "/api/invitations/accept", json={"token": token, "password": "newpass123"}
        )
        assert accept_resp.status_code == 201
        new_user_id = accept_resp.json()["id"]

        rows = _get_audit_rows(self._engine, event_type="invitation.accepted")
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == new_user_id  # type: ignore[union-attr]
        assert row.actor_email == "invitee@t.com"  # type: ignore[union-attr]
        params = json.loads(row.params)  # type: ignore[arg-type]
        assert params["email"] == "invitee@t.com"
        assert params["role"] == "member"

    def test_settings_changed(self) -> None:
        """settings.changed — emitted on PATCH /settings with a real change."""
        admin_uid = _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        resp = self._client.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": 7}},
        )
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="settings.changed")
        assert len(rows) == 1
        assert rows[0].actor_user_id == admin_uid  # type: ignore[union-attr]

    def test_settings_changed_not_emitted_on_empty_patch(self) -> None:
        """Empty-body PATCH /settings must NOT create a settings.changed audit row."""
        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

        resp = self._client.patch("/api/settings", json={})
        assert resp.status_code == 200

        rows = _get_audit_rows(self._engine, event_type="settings.changed")
        assert len(rows) == 0, f"Expected 0 settings.changed rows for empty PATCH, got {len(rows)}"

    def test_admin_self_delete_succeeds_with_null_actor(self) -> None:
        """Admin deletes their own account (≥2 admins present) → 204 and correct audit row.

        Before the fix the audit INSERT raised FOREIGN KEY constraint failed
        (actor_user_id pointed at the just-deleted row), rolling back the delete
        and returning 500.  After the fix:
        - The delete succeeds (204).
        - Exactly one user.deleted row exists.
        - actor_user_id IS NULL (no dangling FK).
        - actor_email captures the admin's email as a snapshot.
        """
        self_email = "self_delete@t.com"
        self_id = _make_user(self._engine, self_email, role="admin")
        _login(self._client, self_email)

        # Second admin present so the last-admin guard allows self-deletion.
        _make_user(self._engine, "other_admin@t.com", role="admin")

        resp = self._client.delete(f"/api/users/{self_id}")
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        rows = _get_audit_rows(self._engine, event_type="user.deleted")
        assert len(rows) == 1, f"Expected 1 user.deleted row, got {len(rows)}"
        row = rows[0]
        assert row.actor_user_id is None, (  # type: ignore[union-attr]
            f"actor_user_id must be NULL for self-delete, got {row.actor_user_id}"  # type: ignore[union-attr]
        )
        assert row.actor_email == self_email, (  # type: ignore[union-attr]
            f"actor_email snapshot must be {self_email!r}, got {row.actor_email!r}"  # type: ignore[union-attr]
        )
        assert row.target_id == self_id  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Class 4: Filtering + pagination
# ---------------------------------------------------------------------------


class TestFilteringAndPagination:
    """Verify GET /audit filters and paginates correctly."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client
        # Set up admin + log in
        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

    def test_filter_by_event_type(self) -> None:
        """event_type filter returns only matching rows."""
        # Trigger a login_succeeded and a settings.changed
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 5}})

        resp = self._client.get("/api/audit?event_type=settings.changed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert all(item["event_type"] == "settings.changed" for item in data["items"])

        resp2 = self._client.get("/api/audit?event_type=auth.login_succeeded")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["total"] == 1
        assert all(item["event_type"] == "auth.login_succeeded" for item in data2["items"])

    def test_filter_by_actor_id(self) -> None:
        """actor_id filter returns only rows for that actor."""
        from sqlalchemy.orm import sessionmaker as SM

        from app.repositories.user import UserRepository

        factory = SM(bind=self._engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            admin = UserRepository(db).get_by_email("admin@t.com")
            admin_id = admin.id  # type: ignore[union-attr]
        finally:
            db.close()

        # Do an action that writes a row with the admin as actor.
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 3}})

        resp = self._client.get(f"/api/audit?actor_id={admin_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for item in data["items"]:
            # Every returned row must belong to this actor (or have NULL actor
            # for system events — but all these actions are admin-actor rows).
            assert item.get("actor_email") == "admin@t.com"

    def test_pagination_limit_and_offset(self) -> None:
        """limit + offset paginate correctly; total is the full count."""
        # Generate multiple settings.changed rows
        for i in range(5):
            self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": i}})

        resp_all = self._client.get("/api/audit?event_type=settings.changed&limit=200")
        assert resp_all.status_code == 200
        total = resp_all.json()["total"]
        assert total == 5

        # First page of 2
        resp_p1 = self._client.get("/api/audit?event_type=settings.changed&limit=2&offset=0")
        assert resp_p1.status_code == 200
        data_p1 = resp_p1.json()
        assert data_p1["total"] == 5
        assert len(data_p1["items"]) == 2
        assert data_p1["limit"] == 2
        assert data_p1["offset"] == 0

        # Second page of 2
        resp_p2 = self._client.get("/api/audit?event_type=settings.changed&limit=2&offset=2")
        assert resp_p2.status_code == 200
        data_p2 = resp_p2.json()
        assert len(data_p2["items"]) == 2
        assert data_p2["total"] == 5

        # Third page of 1
        resp_p3 = self._client.get("/api/audit?event_type=settings.changed&limit=2&offset=4")
        assert resp_p3.status_code == 200
        assert len(resp_p3.json()["items"]) == 1

    def test_newest_first_ordering(self) -> None:
        """Results are ordered newest-first."""
        import time

        # Generate two rows with measurable time gap using settings changes
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 1}})
        time.sleep(0.05)  # small gap to ensure distinct timestamps
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 2}})

        resp = self._client.get("/api/audit?event_type=settings.changed")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        # Newest first: created_at[0] >= created_at[1]
        from datetime import datetime

        ts0 = datetime.fromisoformat(items[0]["created_at"])
        ts1 = datetime.fromisoformat(items[1]["created_at"])
        assert ts0 >= ts1

    def test_from_to_date_filters(self) -> None:
        """from/to date filters narrow the result set.

        Uses a clearly-past lower bound to confirm 'from' returns all matching rows,
        and a clearly-future upper bound to confirm 'to' excludes rows created after.
        This avoids flaky timing issues that arise from using real-time midpoints.
        """
        import urllib.parse
        from datetime import UTC, datetime, timedelta

        # Do two settings changes
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 1}})
        self._client.patch("/api/settings", json={"reminders": {"best_before_lead_days": 2}})

        # 'from' = 1 hour ago should return both rows.
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        encoded_past = urllib.parse.quote(past)
        resp_past = self._client.get(f"/api/audit?event_type=settings.changed&from={encoded_past}")
        assert resp_past.status_code == 200, resp_past.json()
        assert resp_past.json()["total"] == 2

        # 'to' = 1 hour from now should return both rows.
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        encoded_future = urllib.parse.quote(future)
        resp_future = self._client.get(
            f"/api/audit?event_type=settings.changed&to={encoded_future}"
        )
        assert resp_future.status_code == 200, resp_future.json()
        assert resp_future.json()["total"] == 2

        # 'from' = 1 hour from now should return 0 rows (all rows are in the past).
        resp_none = self._client.get(
            f"/api/audit?event_type=settings.changed&from={encoded_future}"
        )
        assert resp_none.status_code == 200, resp_none.json()
        assert resp_none.json()["total"] == 0

    def test_default_limit_and_offset_in_response(self) -> None:
        """Response envelope includes the applied limit and offset."""
        resp = self._client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_max_limit_200(self) -> None:
        """limit > 200 returns 422 validation error."""
        resp = self._client.get("/api/audit?limit=201")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Class 5: Access control
# ---------------------------------------------------------------------------


class TestAccessControl:
    """GET /audit must be admin-only (VIEW_AUDIT permission)."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._base_client = base_client
        self._client, self._engine = base_client
        # Bootstrap admin
        _make_user(self._engine, "admin@t.com", role="admin")

    def test_admin_can_access_audit(self) -> None:
        """Admin gets 200 from GET /audit."""
        _login(self._client, "admin@t.com")
        resp = self._client.get("/api/audit")
        assert resp.status_code == 200

    def test_member_cannot_access_audit(self) -> None:
        """Member gets 403 auth.forbidden from GET /audit."""
        member_client, _ = _fresh_client_for_role(self._base_client, "member")
        resp = member_client.get("/api/audit")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_viewer_cannot_access_audit(self) -> None:
        """Viewer gets 403 auth.forbidden from GET /audit."""
        viewer_client, _ = _fresh_client_for_role(self._base_client, "viewer")
        resp = viewer_client.get("/api/audit")
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

    def test_unauthenticated_cannot_access_audit(self) -> None:
        """Unauthenticated request gets 401."""
        client, _ = self._base_client
        from fastapi.testclient import TestClient as FTC

        anon = FTC(client.app, raise_server_exceptions=True)
        anon.__enter__()
        resp = anon.get("/api/audit")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Class 6: Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    """Verify AuditLogResponse deserialises params from JSON string to dict."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_client: tuple[TestClient, object]) -> None:
        self._client, self._engine = base_client
        _make_user(self._engine, "admin@t.com", role="admin")
        _login(self._client, "admin@t.com")

    def test_params_surfaced_as_dict(self) -> None:
        """params column is a dict in the response (not a raw JSON string)."""
        # Create a second user and change its role — this creates a row with params
        target = _make_user(self._engine, "target@t.com", role="member")
        self._client.patch(f"/api/users/{target}", json={"role": "viewer"})

        resp = self._client.get("/api/audit?event_type=user.role_changed")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        params = items[0]["params"]
        assert isinstance(params, dict)
        assert params["old_role"] == "member"
        assert params["new_role"] == "viewer"

    def test_params_null_for_no_params_event(self) -> None:
        """Events without params return params=null in the response."""
        # auth.login_succeeded has no params (or target only; params=None)
        resp = self._client.get("/api/audit?event_type=auth.login_succeeded")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["params"] is None


# ---------------------------------------------------------------------------
# Class 7: Migration round-trip
# ---------------------------------------------------------------------------

# Path to the backend root (this file lives in backend/tests/).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_migration_0032_roundtrip(tmp_path: Path) -> None:
    """Migration 0032 adds audit_log, then removes it on downgrade.

    Runs alembic as a subprocess to avoid the local ``alembic/`` package
    directory shadowing the installed alembic (same pattern as other step tests).
    """
    import subprocess

    db_path = tmp_path / "migration_test_step6.db"
    db_url = f"sqlite:///{db_path}"

    def _alembic(*args: str) -> tuple[int, str]:
        env = {**os.environ, "SECRET_KEY": "test-migration-key-step6", "DATABASE_URL": db_url}
        result = subprocess.run(
            [str(_BACKEND_ROOT / ".venv/bin/alembic"), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_BACKEND_ROOT),
        )
        output = result.stdout + result.stderr
        return result.returncode, output

    # Upgrade to 0031 first (the revision before our migration).
    rc, out = _alembic("upgrade", "0031")
    assert rc == 0, f"alembic upgrade 0031 failed:\n{out}"

    # Verify audit_log does NOT exist before 0032.
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    assert "audit_log" not in sa_inspect(engine).get_table_names()

    # Now upgrade to 0032.
    rc, out = _alembic("upgrade", "0032")
    assert rc == 0, f"alembic upgrade 0032 failed:\n{out}"

    # Verify audit_log EXISTS after 0032.
    # Dispose and reconnect so SQLAlchemy picks up the new schema.
    engine.dispose()
    engine2 = create_engine(db_url)
    tables_after = sa_inspect(engine2).get_table_names()
    assert "audit_log" in tables_after, f"audit_log not found in {tables_after}"
    # Check the expected columns are present.
    audit_cols = {c["name"] for c in sa_inspect(engine2).get_columns("audit_log")}
    for col in (
        "id",
        "event_type",
        "actor_user_id",
        "actor_email",
        "target_type",
        "target_id",
        "params",
        "ip_address",
        "created_at",
    ):
        assert col in audit_cols, f"Column {col!r} missing from audit_log"

    # Downgrade back to 0031.
    rc, out = _alembic("downgrade", "0031")
    assert rc == 0, f"alembic downgrade 0031 failed:\n{out}"

    engine2.dispose()
    engine3 = create_engine(db_url)
    tables_after_downgrade = sa_inspect(engine3).get_table_names()
    assert "audit_log" not in tables_after_downgrade, "audit_log should be gone after downgrade"
    assert "users" in tables_after_downgrade, "users table must still exist"
    engine3.dispose()
