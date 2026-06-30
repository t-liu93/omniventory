"""M9.1 Step 1 tests: LLM provider config in settings KV store.

Required coverage (per M9.1.md §5 + §9 Step 1):

SettingsService (unit tests via in-memory SQLite):
- llm defaults: enabled=False, base_url=None, model=None, api_key_is_set=False
- api_key_is_set reflects stored value (False → True → False on set/clear)
- Omit api_key in update → existing stored value kept (omit=keep)
- Set api_key to "" → cleared (api_key_is_set=False)
- Set api_key to non-empty → stored (api_key_is_set=True)
- GET response never contains the plaintext api_key value
- llm_config() getter returns the real key for internal use
- base_url, model, enabled round-trip through apply_update → get_settings

HTTP API (end-to-end via TestClient):
- GET /settings carries api_key_is_set, no plaintext key
- PATCH sets api_key → api_key_is_set=True, key not in response payload
- PATCH with "" clears api_key → api_key_is_set=False
- PATCH omitting api_key keeps the stored value (api_key_is_set stays True)
- base_url, model, enabled round-trip through PATCH→GET
- viewer is blocked from PATCH /settings (llm block) → 403 auth.forbidden
- member is blocked from PATCH /settings (llm block) → 403 auth.forbidden
- admin is permitted to PATCH /settings (llm block) → 200
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, object]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
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
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod

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
        notif_mod,
        media_file_mod,
        tag_mod,
        note_mod,
        barcode_mod,
        user_token_mod,
        audit_log_mod,
        ms_mod,
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
    """Return (sqlite url, path) for a temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m9_1_step1_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


def _reload_all_models() -> None:
    """Reload model modules to pick up fresh DB engine after monkeypatch."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
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
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
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
    importlib.reload(loc_mod)
    importlib.reload(si_mod)
    importlib.reload(sm_mod)
    importlib.reload(setting_mod)
    importlib.reload(notif_mod)
    importlib.reload(media_file_mod)
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(barcode_mod)
    importlib.reload(user_token_mod)
    importlib.reload(audit_log_mod)
    importlib.reload(ms_mod)
    importlib.reload(ms_repo_mod)


def _seed_item_kinds(engine: object) -> None:
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


def _create_user_in_db(engine: object, email: str, password: str, role: str) -> None:
    """Insert a user with the given role directly into the DB."""
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
    """Temp-file SQLite DB patched into DATABASE_URL."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m9-1-step1")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[object]:
    """TestClient with full schema + authenticated admin session."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_item_kinds(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        _create_user_in_db(engine, "admin@example.com", "adminpass", "admin")
        resp = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert resp.status_code == 200, f"Admin login failed: {resp.json()}"
        yield client

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# 1. SettingsService — unit tests (in-memory SQLite)
# ---------------------------------------------------------------------------


class TestLlmConfigDefaults:
    """LLM defaults are returned when no keys are stored."""

    def test_llm_defaults(self, db_session: Session) -> None:
        """GET settings returns llm.enabled=False, base_url=None, model=None, api_key_is_set=False."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        settings = svc.get_settings()

        llm = settings.llm
        assert llm.enabled is False
        assert llm.base_url is None
        assert llm.model is None
        assert llm.api_key_is_set is False

    def test_llm_response_has_no_api_key_field(self, db_session: Session) -> None:
        """Serialized LlmConfigResponse never contains a plaintext api_key field."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        settings = svc.get_settings()
        serialized = settings.llm.model_dump()

        assert "api_key" not in serialized
        assert "api_key_is_set" in serialized


class TestLlmApiKeySecretHandling:
    """api_key follows the omit=keep / ""=clear / non-empty=set rule."""

    def test_set_api_key_makes_is_set_true(self, db_session: Session) -> None:
        """Patching with a non-empty api_key sets api_key_is_set=True."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        result = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-secret-key")))

        assert result.llm.api_key_is_set is True

    def test_clear_api_key_with_empty_string(self, db_session: Session) -> None:
        """Patching with api_key="" clears the stored value → api_key_is_set=False."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        # First set it
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-secret-key")))
        # Then clear it
        result = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="")))

        assert result.llm.api_key_is_set is False

    def test_omit_api_key_keeps_existing_value(self, db_session: Session) -> None:
        """Omitting api_key from the update (None) leaves the stored value intact."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        # Set the key
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-keep-me")))
        # Update only enabled, omit api_key
        result = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(enabled=True)))

        # api_key_is_set must still be True (omit = keep)
        assert result.llm.api_key_is_set is True

    def test_api_key_never_appears_in_settings_response(self, db_session: Session) -> None:
        """After setting the api_key, the full SettingsResponse serialization contains no plaintext key."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-super-secret")))
        full = svc.get_settings().model_dump_json()

        assert "sk-super-secret" not in full
        assert "api_key_is_set" in full

    def test_api_key_is_set_false_by_default(self, db_session: Session) -> None:
        """Before any key is stored, api_key_is_set is False."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        assert svc.get_settings().llm.api_key_is_set is False


class TestLlmConfigGetter:
    """llm_config() returns the real (decrypted) key for internal use only."""

    def test_llm_config_returns_real_key(self, db_session: Session) -> None:
        """llm_config() getter returns the actual stored api_key."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-real-key")))
        db_session.flush()

        cfg = svc.llm_config()
        assert cfg.api_key == "sk-real-key"

    def test_llm_config_returns_none_when_key_cleared(self, db_session: Session) -> None:
        """llm_config().api_key is None after clearing with empty string."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="sk-temp")))
        svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(api_key="")))
        db_session.flush()

        cfg = svc.llm_config()
        assert cfg.api_key is None

    def test_llm_config_returns_all_fields(self, db_session: Session) -> None:
        """llm_config() returns all four fields with correct values."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                llm=LlmConfigUpdate(
                    enabled=True,
                    base_url="https://openrouter.ai/api",
                    model="openai/gpt-4o-mini",
                    api_key="sk-or-key",
                )
            )
        )
        db_session.flush()

        cfg = svc.llm_config()
        assert cfg.enabled is True
        assert cfg.base_url == "https://openrouter.ai/api"
        assert cfg.model == "openai/gpt-4o-mini"
        assert cfg.api_key == "sk-or-key"


class TestLlmFieldsRoundTrip:
    """base_url, model, and enabled round-trip through apply_update → get_settings."""

    def test_enabled_round_trip(self, db_session: Session) -> None:
        """enabled can be toggled True and back to False."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        r1 = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(enabled=True)))
        assert r1.llm.enabled is True

        r2 = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(enabled=False)))
        assert r2.llm.enabled is False

    def test_base_url_round_trip(self, db_session: Session) -> None:
        """base_url is stored and returned correctly."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        result = svc.apply_update(
            SettingsUpdate(llm=LlmConfigUpdate(base_url="https://openrouter.ai/api"))
        )
        assert result.llm.base_url == "https://openrouter.ai/api"

    def test_model_round_trip(self, db_session: Session) -> None:
        """model is stored and returned correctly."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        result = svc.apply_update(SettingsUpdate(llm=LlmConfigUpdate(model="openai/gpt-4o-mini")))
        assert result.llm.model == "openai/gpt-4o-mini"

    def test_all_llm_fields_together(self, db_session: Session) -> None:
        """All four LLM fields can be set and retrieved in one update."""
        from app.schemas.settings import LlmConfigUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        result = svc.apply_update(
            SettingsUpdate(
                llm=LlmConfigUpdate(
                    enabled=True,
                    base_url="https://openrouter.ai/api",
                    model="openai/gpt-4o-mini",
                    api_key="sk-round-trip",
                )
            )
        )
        llm = result.llm
        assert llm.enabled is True
        assert llm.base_url == "https://openrouter.ai/api"
        assert llm.model == "openai/gpt-4o-mini"
        assert llm.api_key_is_set is True


# ---------------------------------------------------------------------------
# 2. HTTP API tests (end-to-end via TestClient)
# ---------------------------------------------------------------------------


class TestLlmSettingsHttpApi:
    """GET /settings and PATCH /settings HTTP API tests for the llm block."""

    def test_get_settings_includes_llm_block(self, http_client: object) -> None:
        """GET /settings returns a llm block with all expected fields."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/settings")
        assert resp.status_code == 200

        data = resp.json()
        assert "llm" in data, "llm block missing from GET /settings response"
        llm = data["llm"]
        assert "enabled" in llm
        assert "base_url" in llm
        assert "model" in llm
        assert "api_key_is_set" in llm

    def test_get_settings_llm_defaults(self, http_client: object) -> None:
        """GET /settings llm block has correct defaults before any PATCH."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/settings")
        assert resp.status_code == 200

        llm = resp.json()["llm"]
        assert llm["enabled"] is False
        assert llm["base_url"] is None
        assert llm["model"] is None
        assert llm["api_key_is_set"] is False

    def test_get_settings_llm_no_plaintext_key(self, http_client: object) -> None:
        """GET /settings llm block never contains a plaintext api_key field."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # Set the key first
        http_client.patch("/api/settings", json={"llm": {"api_key": "sk-secret-value"}})

        resp = http_client.get("/api/settings")
        assert resp.status_code == 200

        llm = resp.json()["llm"]
        assert "api_key" not in llm, "Plaintext api_key must never appear in GET /settings response"
        assert "api_key_is_set" in llm
        assert "sk-secret-value" not in resp.text

    def test_patch_sets_api_key(self, http_client: object) -> None:
        """PATCH /settings with non-empty api_key → api_key_is_set=True; key not in payload."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch("/api/settings", json={"llm": {"api_key": "sk-new-key"}})
        assert resp.status_code == 200

        llm = resp.json()["llm"]
        assert llm["api_key_is_set"] is True
        assert "sk-new-key" not in resp.text

    def test_patch_clears_api_key_with_empty_string(self, http_client: object) -> None:
        """PATCH with api_key="" clears the key → api_key_is_set=False."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # Set
        http_client.patch("/api/settings", json={"llm": {"api_key": "sk-to-clear"}})
        # Clear
        resp = http_client.patch("/api/settings", json={"llm": {"api_key": ""}})
        assert resp.status_code == 200

        assert resp.json()["llm"]["api_key_is_set"] is False

    def test_patch_omit_api_key_keeps_existing_value(self, http_client: object) -> None:
        """Omitting api_key from PATCH body (omit=keep) leaves the stored value intact."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # Set the key
        http_client.patch("/api/settings", json={"llm": {"api_key": "sk-keep-me"}})
        # PATCH only enabled (omit api_key entirely)
        resp = http_client.patch("/api/settings", json={"llm": {"enabled": True}})
        assert resp.status_code == 200

        # api_key_is_set must remain True
        assert resp.json()["llm"]["api_key_is_set"] is True

    def test_patch_base_url_model_enabled_round_trip(self, http_client: object) -> None:
        """base_url, model, and enabled round-trip through PATCH → GET /settings."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={
                "llm": {
                    "enabled": True,
                    "base_url": "https://openrouter.ai/api",
                    "model": "openai/gpt-4o-mini",
                }
            },
        )
        assert resp.status_code == 200

        llm = resp.json()["llm"]
        assert llm["enabled"] is True
        assert llm["base_url"] == "https://openrouter.ai/api"
        assert llm["model"] == "openai/gpt-4o-mini"

        # Confirm with a fresh GET
        get_resp = http_client.get("/api/settings")
        get_llm = get_resp.json()["llm"]
        assert get_llm["enabled"] is True
        assert get_llm["base_url"] == "https://openrouter.ai/api"
        assert get_llm["model"] == "openai/gpt-4o-mini"


# ---------------------------------------------------------------------------
# 3. Permission tests: viewer and member are blocked from PATCH llm
# ---------------------------------------------------------------------------


class TestLlmSettingsPermissions:
    """viewer and member cannot PATCH the llm block; admin can."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        temp_db: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Generator[None]:
        """Set up the app with an admin-created DB and build per-role clients."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        _reload_all_models()

        from app.config import get_settings
        from app.db.base import Base, get_engine
        from app.main import create_app

        get_settings.cache_clear()
        self._engine = get_engine()
        Base.metadata.create_all(self._engine)
        _seed_item_kinds(self._engine)

        app = create_app()
        self._Base = Base

        # Admin client (authenticates as admin)
        self._admin_tc = TestClient(app, raise_server_exceptions=True)
        self._admin_tc.__enter__()
        _create_user_in_db(self._engine, "admin@perm.test", "adminpass", "admin")
        resp = self._admin_tc.post(
            "/api/auth/login",
            json={"email": "admin@perm.test", "password": "adminpass"},
        )
        assert resp.status_code == 200

        # Viewer client
        self._viewer_tc = TestClient(app, raise_server_exceptions=True)
        self._viewer_tc.__enter__()
        _create_user_in_db(self._engine, "viewer@perm.test", "viewerpass", "viewer")
        resp = self._viewer_tc.post(
            "/api/auth/login",
            json={"email": "viewer@perm.test", "password": "viewerpass"},
        )
        assert resp.status_code == 200

        # Member client
        self._member_tc = TestClient(app, raise_server_exceptions=True)
        self._member_tc.__enter__()
        _create_user_in_db(self._engine, "member@perm.test", "memberpass", "member")
        resp = self._member_tc.post(
            "/api/auth/login",
            json={"email": "member@perm.test", "password": "memberpass"},
        )
        assert resp.status_code == 200

        yield

        self._admin_tc.__exit__(None, None, None)
        self._viewer_tc.__exit__(None, None, None)
        self._member_tc.__exit__(None, None, None)
        drop_all_sqlite(self._Base, self._engine)

    def _assert_forbidden(self, resp: object) -> None:
        from fastapi.testclient import TestClient as _TC  # noqa: F401

        assert hasattr(resp, "status_code")
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.json()}"  # type: ignore[union-attr]
        body = resp.json()  # type: ignore[union-attr]
        assert body["code"] == "auth.forbidden", f"Wrong error code: {body}"

    def test_viewer_cannot_patch_llm_settings(self) -> None:
        """A viewer cannot PATCH /settings with an llm block → 403 auth.forbidden."""
        self._assert_forbidden(
            self._viewer_tc.patch(
                "/api/settings",
                json={"llm": {"enabled": True}},
            )
        )

    def test_member_cannot_patch_llm_settings(self) -> None:
        """A member cannot PATCH /settings with an llm block → 403 auth.forbidden."""
        self._assert_forbidden(
            self._member_tc.patch(
                "/api/settings",
                json={"llm": {"api_key": "sk-member-attempt"}},
            )
        )

    def test_admin_can_patch_llm_settings(self) -> None:
        """An admin can PATCH /settings with an llm block → 200."""
        resp = self._admin_tc.patch(
            "/api/settings",
            json={"llm": {"enabled": True, "base_url": "https://openrouter.ai/api"}},
        )
        assert resp.status_code == 200
        assert resp.json()["llm"]["enabled"] is True

    def test_viewer_can_read_llm_settings(self) -> None:
        """A viewer (authenticated) can still GET /settings and see the masked llm block."""
        resp = self._viewer_tc.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm" in data
        # Key is never shown
        assert "api_key" not in data["llm"]

    def test_member_can_read_llm_settings(self) -> None:
        """A member (authenticated) can GET /settings — the llm block is visible but masked."""
        resp = self._member_tc.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm" in data
        assert "api_key" not in data["llm"]
