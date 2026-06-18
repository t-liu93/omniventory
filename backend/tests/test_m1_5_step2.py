"""M1.5 Step 2 tests: per-user preferred_language field + PATCH /auth/me.

Required coverage (M1.5.md §5 / §10 Step 2):
- New user's preferred_language is NULL by default.
- GET /auth/me returns preferred_language field.
- PATCH /auth/me persists 'en', 'zh', and explicit null; each round-trips via
  a follow-up GET /auth/me.
- Unsupported codes ('fr', 'zh-CN', '') → 422 validation.unsupported_language
  AND no write (stored value unchanged).
- Omitted field (empty body {}) → no-op (existing value not clobbered).
- PATCH /auth/me without a session → 401 auth.not_authenticated.
- Migration 0009: upgrades clean on a DB at 0008 and downgrades cleanly.
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
# Fixtures (mirror test_m1_5_step1.py pattern)
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1_5_step2_")
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
    """Return a TestClient backed by a temp-file SQLite with the full schema."""
    import importlib

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-5-step2")
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
        # Seed system item_kinds (normally done by Alembic migration 0006).
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


def _me(client: TestClient) -> dict[str, object]:
    """Call GET /api/auth/me and return the body."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200, resp.json()
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Default value is NULL
# ---------------------------------------------------------------------------


class TestDefaultNull:
    """New users get preferred_language = NULL."""

    def test_new_user_preferred_language_is_null(self, test_client: TestClient) -> None:
        """preferred_language is null right after account creation."""
        _setup_admin(test_client)
        _login(test_client)
        body = _me(test_client)
        user = body["user"]
        assert isinstance(user, dict)
        assert "preferred_language" in user, f"preferred_language missing from UserResponse: {user}"
        assert user["preferred_language"] is None, (
            f"Expected None for a fresh user, got {user['preferred_language']!r}"
        )


# ---------------------------------------------------------------------------
# 2. GET /auth/me includes the field
# ---------------------------------------------------------------------------


class TestGetMeIncludesField:
    """GET /auth/me returns preferred_language in the user object."""

    def test_me_returns_preferred_language_field(self, test_client: TestClient) -> None:
        """GET /auth/me response includes preferred_language key."""
        _setup_admin(test_client)
        _login(test_client)
        resp = test_client.get("/api/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        user = body.get("user", {})
        assert "preferred_language" in user, (
            f"preferred_language key missing from /auth/me response: {body}"
        )


# ---------------------------------------------------------------------------
# 3. PATCH /auth/me persists values and round-trips
# ---------------------------------------------------------------------------


class TestPatchMePersists:
    """PATCH /auth/me correctly persists each value and is confirmed by GET."""

    def test_patch_persists_en(self, test_client: TestClient) -> None:
        """PATCH {preferred_language: 'en'} is persisted and confirmed by GET."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["user"]["preferred_language"] == "en"

        # Round-trip: GET should confirm.
        body = _me(test_client)
        assert body["user"]["preferred_language"] == "en"  # type: ignore[index]

    def test_patch_persists_zh(self, test_client: TestClient) -> None:
        """PATCH {preferred_language: 'zh'} is persisted and confirmed by GET."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": "zh"})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["user"]["preferred_language"] == "zh"

        body = _me(test_client)
        assert body["user"]["preferred_language"] == "zh"  # type: ignore[index]

    def test_patch_explicit_null_unsets(self, test_client: TestClient) -> None:
        """PATCH {preferred_language: null} writes NULL after a prior set."""
        _setup_admin(test_client)
        _login(test_client)

        # First set to 'en'.
        r1 = test_client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert r1.status_code == 200
        assert r1.json()["user"]["preferred_language"] == "en"

        # Explicitly unset.
        r2 = test_client.patch("/api/auth/me", json={"preferred_language": None})
        assert r2.status_code == 200, r2.json()
        assert r2.json()["user"]["preferred_language"] is None

        # Round-trip.
        body = _me(test_client)
        assert body["user"]["preferred_language"] is None  # type: ignore[index]


# ---------------------------------------------------------------------------
# 4. Unsupported code → 422 validation.unsupported_language + no write
# ---------------------------------------------------------------------------


class TestUnsupportedLanguage:
    """An unsupported code → 422 + stored value unchanged."""

    def _assert_unsupported(self, resp_body: dict[str, object]) -> None:
        assert resp_body.get("code") == "validation.unsupported_language", resp_body
        params = resp_body.get("params")
        assert params is not None, f"Expected params in error: {resp_body}"
        assert "value" in params  # type: ignore[operator]
        assert "supported" in params  # type: ignore[operator]

    def test_unsupported_fr_returns_422(self, test_client: TestClient) -> None:
        """'fr' is not supported → 422 validation.unsupported_language."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": "fr"})
        assert resp.status_code == 422, resp.json()
        self._assert_unsupported(resp.json())

    def test_unsupported_zh_cn_returns_422(self, test_client: TestClient) -> None:
        """'zh-CN' is not in SUPPORTED_LANGUAGES → 422."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": "zh-CN"})
        assert resp.status_code == 422, resp.json()
        self._assert_unsupported(resp.json())

    def test_empty_string_returns_422(self, test_client: TestClient) -> None:
        """'' is not supported → 422."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": ""})
        assert resp.status_code == 422, resp.json()
        self._assert_unsupported(resp.json())

    def test_unsupported_no_write_happens(self, test_client: TestClient) -> None:
        """After 422, the stored value must not have changed."""
        _setup_admin(test_client)
        _login(test_client)

        # Set a known-good value first.
        r1 = test_client.patch("/api/auth/me", json={"preferred_language": "zh"})
        assert r1.status_code == 200

        # Attempt unsupported → 422.
        r2 = test_client.patch("/api/auth/me", json={"preferred_language": "fr"})
        assert r2.status_code == 422

        # Value must still be 'zh'.
        body = _me(test_client)
        assert body["user"]["preferred_language"] == "zh", (  # type: ignore[index]
            "Unsupported language update must not overwrite the stored value"
        )

    def test_unsupported_error_code_exact(self, test_client: TestClient) -> None:
        """422 error code must be exactly 'validation.unsupported_language'."""
        _setup_admin(test_client)
        _login(test_client)

        resp = test_client.patch("/api/auth/me", json={"preferred_language": "jp"})
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("code") == "validation.unsupported_language"
        # Must have both value and supported in params.
        assert body.get("params", {}).get("value") == "jp"  # type: ignore[union-attr]
        supported = body.get("params", {}).get("supported")  # type: ignore[union-attr]
        assert isinstance(supported, list)
        assert "en" in supported
        assert "zh" in supported


# ---------------------------------------------------------------------------
# 5. Empty body → no-op (existing value not clobbered)
# ---------------------------------------------------------------------------


class TestEmptyBodyNoOp:
    """Omitted field does not overwrite an existing value."""

    def test_empty_body_does_not_clobber(self, test_client: TestClient) -> None:
        """PATCH {} after setting 'en' must leave 'en' in place."""
        _setup_admin(test_client)
        _login(test_client)

        # Set to 'en'.
        r1 = test_client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert r1.status_code == 200

        # Empty body → no-op.
        r2 = test_client.patch("/api/auth/me", json={})
        assert r2.status_code == 200, r2.json()
        assert r2.json()["user"]["preferred_language"] == "en", (
            "Empty PATCH body must not clobber the existing preferred_language"
        )

        # Round-trip.
        body = _me(test_client)
        assert body["user"]["preferred_language"] == "en"  # type: ignore[index]


# ---------------------------------------------------------------------------
# 6. No session → 401 auth.not_authenticated
# ---------------------------------------------------------------------------


class TestPatchRequiresAuth:
    """PATCH /auth/me without a valid session returns 401."""

    def test_patch_me_without_session_returns_401(self, test_client: TestClient) -> None:
        """PATCH /auth/me without a session cookie → 401 auth.not_authenticated."""
        resp = test_client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert resp.status_code == 401, resp.json()
        body = resp.json()
        assert body.get("code") == "auth.not_authenticated", body


# ---------------------------------------------------------------------------
# 7. Migration round-trip: 0009 up / down
# ---------------------------------------------------------------------------


class TestMigration0009:
    """Migration 0009 adds preferred_language cleanly and is reversible."""

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

    def test_migration_0009_up_down(self) -> None:
        """Apply migrations 0001–0009, then downgrade to 0008; column appears / disappears."""
        from sqlalchemy import create_engine, inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_mig_0009_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to HEAD (includes 0009).
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            # Verify preferred_language column exists after upgrade.
            eng = create_engine(url)
            cols = {c["name"] for c in inspect(eng).get_columns("users")}
            eng.dispose()
            assert "preferred_language" in cols, (
                f"After upgrade to 0009, preferred_language column must exist. Columns: {cols}"
            )

            # Downgrade to 0008.
            rc2, output2 = self._run_alembic("downgrade", "0008", url=url)
            assert rc2 == 0, f"alembic downgrade to 0008 failed:\n{output2}"

            # Verify preferred_language column is gone after downgrade.
            eng2 = create_engine(url)
            cols2 = {c["name"] for c in inspect(eng2).get_columns("users")}
            eng2.dispose()
            assert "preferred_language" not in cols2, (
                f"After downgrade to 0008, preferred_language must be absent. Columns: {cols2}"
            )

        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# 8. OpenAPI schema cleanliness — internal tracking field must not leak
# ---------------------------------------------------------------------------


class TestUserPreferencesUpdateSchema:
    """UserPreferencesUpdate JSON schema must not expose internal fields."""

    def test_preferred_language_was_provided_not_in_json_schema(self) -> None:
        """The internal tracking field must not appear in UserPreferencesUpdate's JSON schema.

        Regression guard: Field(exclude=True) suppresses serialization output but
        NOT JSON-schema generation.  The correct fix uses model_fields_set in the
        route instead of a separate model field.
        """
        from app.schemas.auth import UserPreferencesUpdate

        schema = UserPreferencesUpdate.model_json_schema()
        properties = schema.get("properties", {})
        assert "preferred_language_was_provided" not in properties, (
            "preferred_language_was_provided must not appear in UserPreferencesUpdate's "
            f"JSON schema (contract pollution). Schema properties: {list(properties.keys())}"
        )
        # Sanity: the actual field must still be present.
        assert "preferred_language" in properties, "preferred_language must still be in the schema"
