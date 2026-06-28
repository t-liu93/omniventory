"""M4 Step 1 tests: KV settings store, SettingsService, and GET/PATCH /settings.

Required coverage (per M4.md §5 + §9 Step 1 Tests):

SettingsRepository:
- get() returns None for absent key
- set() / get() round-trip (upsert and retrieve)
- get_all() returns all stored pairs

SettingsService:
- Returns code-defined defaults when no keys are stored
- upsert: set a value, get returns new value; table only stores the override
- Validation failures (lead <0, repeat item <1, bad scan_time, port out-of-range)
  → RequestValidationError / validation.invalid_input (via HTTP)
- Secrets NEVER echoed (only *_is_set in response)
- Set a secret → *_is_set = True
- Clear a secret (empty string) → *_is_set = False
- Clear a secret (null/None) → *_is_set = False

Migration 0015:
- upgrade: table 'settings' exists
- downgrade: table 'settings' removed

HTTP API (end-to-end via TestClient):
- GET /settings returns 200 + correct default shape
- PATCH /settings updates reminders fields
- PATCH /settings masks secrets (*_is_set)
- GET /settings returns 401 when unauthenticated
- PATCH /settings returns 401 when unauthenticated
- PATCH /settings with invalid lead < 0 returns 422 validation.invalid_input
- PATCH /settings with invalid repeat item < 1 returns 422
- PATCH /settings with invalid scan_time returns 422
- PATCH /settings with invalid port returns 422
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
# Helpers
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


# ---------------------------------------------------------------------------
# 1. SettingsRepository
# ---------------------------------------------------------------------------


class TestSettingsRepository:
    """Unit tests for SettingsRepository data-access layer."""

    def test_get_absent_key_returns_none(self, db_session: Session) -> None:
        """get() returns None for a key that has never been stored."""
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        assert repo.get("reminders.best_before_lead_days") is None

    def test_set_and_get_round_trip(self, db_session: Session) -> None:
        """set() + get() round-trip stores and retrieves the value."""
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("reminders.best_before_lead_days", "5")
        db_session.commit()

        assert repo.get("reminders.best_before_lead_days") == "5"

    def test_upsert_updates_existing_value(self, db_session: Session) -> None:
        """A second set() on the same key overwrites the previous value."""
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("reminders.scan_time", "07:00")
        db_session.commit()
        repo.set("reminders.scan_time", "09:30")
        db_session.commit()

        assert repo.get("reminders.scan_time") == "09:30"

    def test_get_all_returns_all_stored_pairs(self, db_session: Session) -> None:
        """get_all() returns a dict of all stored key/value pairs."""
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("reminders.best_before_lead_days", "7")
        repo.set("reminders.warranty_lead_days", "60")
        db_session.commit()

        result = repo.get_all()
        assert result["reminders.best_before_lead_days"] == "7"
        assert result["reminders.warranty_lead_days"] == "60"

    def test_get_all_empty_when_no_rows(self, db_session: Session) -> None:
        """get_all() returns an empty dict when no keys are stored."""
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        assert repo.get_all() == {}

    def test_updated_at_column_has_onupdate(self) -> None:
        """Setting.updated_at must declare onupdate so UPDATEs refresh the timestamp.

        This test asserts the SQLAlchemy column configuration rather than
        measuring wall-clock timestamps (which would be flaky at sub-second
        resolution).  It directly verifies the M4 §3.1 requirement:
        "refreshed on upsert".
        """
        from app.models.setting import Setting

        col = Setting.__table__.c.updated_at
        assert col.onupdate is not None, (
            "Setting.updated_at must have onupdate=func.now() so that UPDATE "
            "statements (issued by Session.merge on existing rows) refresh the "
            "timestamp, per M4 §3.1."
        )


# ---------------------------------------------------------------------------
# 2. SettingsService — defaults, upsert, secrets
# ---------------------------------------------------------------------------


class TestSettingsServiceDefaults:
    """Code-defined defaults are returned when no keys are stored."""

    def test_reminders_defaults(self, db_session: Session) -> None:
        """Default reminder settings match the §9 Step 1 spec."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        settings = svc.get_settings()

        assert settings.reminders.best_before_lead_days == 3
        assert settings.reminders.warranty_lead_days == 30
        assert settings.reminders.low_stock_repeat_days == [1, 3, 7]
        assert settings.reminders.scan_time == "08:00"

    def test_channels_default_all_disabled(self, db_session: Session) -> None:
        """All three channels are disabled by default."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        settings = svc.get_settings()

        assert settings.channels.email.enabled is False
        assert settings.channels.http.enabled is False
        assert settings.channels.mqtt.enabled is False

    def test_channels_default_secrets_not_set(self, db_session: Session) -> None:
        """All write-only secrets report *_is_set=False by default."""
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        settings = svc.get_settings()

        assert settings.channels.email.password_is_set is False
        assert settings.channels.http.auth_header_is_set is False
        assert settings.channels.http.integration_token_is_set is False
        assert settings.channels.mqtt.password_is_set is False

    def test_table_stores_only_overridden_keys(self, db_session: Session) -> None:
        """With no overrides, the settings table remains empty."""
        from app.repositories.setting import SettingsRepository
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.get_settings()  # just reading — should not write anything

        repo = SettingsRepository(db_session)
        assert repo.get_all() == {}


class TestSettingsServiceUpsert:
    """Upsert behaviour: override stored, defaults for the rest."""

    def test_upsert_best_before_lead_days(self, db_session: Session) -> None:
        """Setting best_before_lead_days stores it; GET returns the new value."""
        from app.schemas.settings import RemindersUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(reminders=RemindersUpdate(best_before_lead_days=7)))
        db_session.commit()

        result = svc.get_settings()
        assert result.reminders.best_before_lead_days == 7
        # Other fields still return defaults
        assert result.reminders.warranty_lead_days == 30

    def test_upsert_scan_time(self, db_session: Session) -> None:
        """Updating scan_time is stored and returned."""
        from app.schemas.settings import RemindersUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(reminders=RemindersUpdate(scan_time="14:30")))
        db_session.commit()

        result = svc.get_settings()
        assert result.reminders.scan_time == "14:30"

    def test_upsert_low_stock_repeat_days(self, db_session: Session) -> None:
        """Updating low_stock_repeat_days list is stored and returned."""
        from app.schemas.settings import RemindersUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(reminders=RemindersUpdate(low_stock_repeat_days=[1, 5, 14]))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.reminders.low_stock_repeat_days == [1, 5, 14]

    def test_only_overridden_key_stored(self, db_session: Session) -> None:
        """After a single override, only that one key is in the table."""
        from app.repositories.setting import SettingsRepository
        from app.schemas.settings import RemindersUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(SettingsUpdate(reminders=RemindersUpdate(best_before_lead_days=14)))
        db_session.commit()

        repo = SettingsRepository(db_session)
        stored = repo.get_all()
        assert "reminders.best_before_lead_days" in stored
        # warranty_lead_days was not overridden — must NOT be stored
        assert "reminders.warranty_lead_days" not in stored

    def test_channel_enable(self, db_session: Session) -> None:
        """Enabling a channel is stored and reflected in the response."""
        from app.schemas.settings import ChannelsUpdate, EmailChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(email=EmailChannelUpdate(enabled=True)))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.email.enabled is True


class TestSettingsServiceSecrets:
    """Write-only secret handling: set, mask, clear."""

    def test_set_email_password_shows_is_set_true(self, db_session: Session) -> None:
        """Setting email.password makes password_is_set=True in response."""
        from app.schemas.settings import ChannelsUpdate, EmailChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(email=EmailChannelUpdate(password="s3cr3t")))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.email.password_is_set is True

    def test_clear_email_password_with_empty_string(self, db_session: Session) -> None:
        """Clearing email.password with '' makes password_is_set=False."""
        from app.schemas.settings import ChannelsUpdate, EmailChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(email=EmailChannelUpdate(password="s3cr3t")))
        )
        db_session.commit()

        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(email=EmailChannelUpdate(password="")))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.email.password_is_set is False

    def test_set_mqtt_password_shows_is_set_true(self, db_session: Session) -> None:
        """Setting mqtt.password makes password_is_set=True in response."""
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(mqtt=MqttChannelUpdate(password="mqttpass")))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.mqtt.password_is_set is True

    def test_clear_mqtt_password_with_empty_string(self, db_session: Session) -> None:
        """Clearing mqtt.password with '' makes password_is_set=False."""
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(mqtt=MqttChannelUpdate(password="mqttpass")))
        )
        db_session.commit()

        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(mqtt=MqttChannelUpdate(password="")))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.mqtt.password_is_set is False

    def test_set_integration_token_shows_is_set_true(self, db_session: Session) -> None:
        """Setting http.integration_token makes integration_token_is_set=True."""
        from app.schemas.settings import ChannelsUpdate, HttpChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(http=HttpChannelUpdate(integration_token="tok123"))
            )
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.http.integration_token_is_set is True

    def test_clear_integration_token_with_empty_string(self, db_session: Session) -> None:
        """Clearing integration_token with '' makes integration_token_is_set=False."""
        from app.schemas.settings import ChannelsUpdate, HttpChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(http=HttpChannelUpdate(integration_token="tok123"))
            )
        )
        db_session.commit()

        svc.apply_update(
            SettingsUpdate(channels=ChannelsUpdate(http=HttpChannelUpdate(integration_token="")))
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.http.integration_token_is_set is False

    def test_set_auth_header_shows_is_set_true(self, db_session: Session) -> None:
        """Setting http.auth_header makes auth_header_is_set=True."""
        from app.schemas.settings import ChannelsUpdate, HttpChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(http=HttpChannelUpdate(auth_header="Bearer secret_token"))
            )
        )
        db_session.commit()

        result = svc.get_settings()
        assert result.channels.http.auth_header_is_set is True

    def test_secret_value_not_in_response(self, db_session: Session) -> None:
        """The raw password value is never present in the SettingsResponse model."""
        from app.schemas.settings import ChannelsUpdate, EmailChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(email=EmailChannelUpdate(password="supersecret"))
            )
        )
        db_session.commit()

        result = svc.get_settings()
        result_dict = result.model_dump()
        result_str = str(result_dict)
        # The plaintext secret must not appear anywhere in the response
        assert "supersecret" not in result_str
        # It should appear only as the boolean flag
        assert result.channels.email.password_is_set is True


# ---------------------------------------------------------------------------
# 3. Pydantic validation (tested via schemas directly)
# ---------------------------------------------------------------------------


class TestSettingsSchemaValidation:
    """Pydantic validation failures are raised by the schema layer."""

    def test_best_before_lead_negative_rejected(self) -> None:
        """best_before_lead_days < 0 raises a Pydantic ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(best_before_lead_days=-1)

    def test_warranty_lead_negative_rejected(self) -> None:
        """warranty_lead_days < 0 raises a Pydantic ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(warranty_lead_days=-1)

    def test_repeat_day_less_than_one_rejected(self) -> None:
        """An entry < 1 in low_stock_repeat_days raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(low_stock_repeat_days=[0, 3, 7])

    def test_repeat_day_zero_rejected(self) -> None:
        """0 in low_stock_repeat_days raises a ValidationError (must be ≥ 1)."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(low_stock_repeat_days=[0])

    def test_scan_time_bad_format_rejected(self) -> None:
        """Non-HH:MM scan_time raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(scan_time="8:00")

    def test_scan_time_invalid_hour_rejected(self) -> None:
        """Hour > 23 in scan_time raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(scan_time="25:00")

    def test_scan_time_invalid_minute_rejected(self) -> None:
        """Minute > 59 in scan_time raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import RemindersUpdate

        with pytest.raises(ValidationError):
            RemindersUpdate(scan_time="08:60")

    def test_email_port_out_of_range_high(self) -> None:
        """email port > 65535 raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import EmailChannelUpdate

        with pytest.raises(ValidationError):
            EmailChannelUpdate(port=99999)

    def test_email_port_zero_rejected(self) -> None:
        """email port 0 raises a ValidationError (must be ≥ 1)."""
        from pydantic import ValidationError

        from app.schemas.settings import EmailChannelUpdate

        with pytest.raises(ValidationError):
            EmailChannelUpdate(port=0)

    def test_mqtt_port_out_of_range_high(self) -> None:
        """mqtt port > 65535 raises a ValidationError."""
        from pydantic import ValidationError

        from app.schemas.settings import MqttChannelUpdate

        with pytest.raises(ValidationError):
            MqttChannelUpdate(port=65536)

    def test_lead_zero_is_valid(self) -> None:
        """Lead of 0 is valid (fire on the date itself)."""
        from app.schemas.settings import RemindersUpdate

        upd = RemindersUpdate(best_before_lead_days=0)
        assert upd.best_before_lead_days == 0

    def test_valid_scan_time_accepted(self) -> None:
        """A valid HH:MM scan_time is accepted."""
        from app.schemas.settings import RemindersUpdate

        upd = RemindersUpdate(scan_time="23:59")
        assert upd.scan_time == "23:59"


# ---------------------------------------------------------------------------
# 4. Migration 0015 up/down round-trip
# ---------------------------------------------------------------------------


class TestMigration0015:
    """Migration 0015: create / drop the settings table.

    Uses a subprocess call to ``.venv/bin/alembic`` so that the local
    ``backend/alembic/`` package directory does not shadow the installed
    ``alembic`` pip package (same pattern as test_m3_step1.py).
    """

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

    def _make_temp_db(self) -> tuple[str, Path]:
        """Return (url, path) for a disposable temp-file SQLite DB."""
        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_migtest_0015_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        return f"sqlite:///{path_str}", db_path

    def test_upgrade_creates_settings_table(self) -> None:
        """After upgrade to 0015 the 'settings' table exists."""
        url, db_path = self._make_temp_db()
        try:
            rc, out = self._run_alembic("upgrade", "0015", url=url)
            assert rc == 0, f"alembic upgrade 0015 failed:\n{out}"

            engine = create_engine(url)
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            assert "settings" in table_names, f"'settings' not in tables: {table_names}"
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_settings_table_has_correct_columns(self) -> None:
        """After upgrade to 0015, settings has key/value/updated_at columns."""
        url, db_path = self._make_temp_db()
        try:
            rc, out = self._run_alembic("upgrade", "0015", url=url)
            assert rc == 0, f"alembic upgrade 0015 failed:\n{out}"

            engine = create_engine(url)
            inspector = inspect(engine)
            columns = {col["name"] for col in inspector.get_columns("settings")}
            assert "key" in columns
            assert "value" in columns
            assert "updated_at" in columns
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_removes_settings_table(self) -> None:
        """After downgrade from 0015 back to 0014, 'settings' is gone."""
        url, db_path = self._make_temp_db()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0015", url=url)
            assert rc_up == 0, f"upgrade 0015 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0014", url=url)
            assert rc_down == 0, f"downgrade 0014 failed:\n{out_down}"

            engine = create_engine(url)
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            assert "settings" not in table_names, f"'settings' still present: {table_names}"
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# 5. HTTP API (end-to-end via TestClient)
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step1_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step1")
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


@pytest.fixture()
def http_client_no_auth(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient without authentication (for 401 tests)."""
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
        yield client

    drop_all_sqlite(Base, engine)


class TestSettingsHttpApi:
    """GET /settings and PATCH /settings HTTP API tests."""

    def test_get_settings_returns_defaults(self, http_client: object) -> None:
        """GET /settings returns 200 with the correct default shape."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/settings")
        assert resp.status_code == 200

        data = resp.json()
        assert data["reminders"]["best_before_lead_days"] == 3
        assert data["reminders"]["warranty_lead_days"] == 30
        assert data["reminders"]["low_stock_repeat_days"] == [1, 3, 7]
        assert data["reminders"]["scan_time"] == "08:00"
        assert data["channels"]["email"]["enabled"] is False
        assert data["channels"]["http"]["enabled"] is False
        assert data["channels"]["mqtt"]["enabled"] is False

    def test_get_settings_secrets_masked(self, http_client: object) -> None:
        """GET /settings response contains *_is_set flags, never raw secret values."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/settings")
        assert resp.status_code == 200

        data = resp.json()
        email = data["channels"]["email"]
        http_ = data["channels"]["http"]
        mqtt = data["channels"]["mqtt"]

        assert "password_is_set" in email
        assert "password" not in email
        assert "auth_header_is_set" in http_
        assert "auth_header" not in http_
        assert "integration_token_is_set" in http_
        assert "integration_token" not in http_
        assert "password_is_set" in mqtt
        assert "password" not in mqtt

    def test_patch_settings_updates_reminder_leads(self, http_client: object) -> None:
        """PATCH /settings updates reminder lead days."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": 5, "warranty_lead_days": 45}},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["reminders"]["best_before_lead_days"] == 5
        assert data["reminders"]["warranty_lead_days"] == 45
        # Other reminder fields unchanged
        assert data["reminders"]["scan_time"] == "08:00"

    def test_patch_settings_sets_and_masks_email_password(self, http_client: object) -> None:
        """PATCH /settings sets email.password; GET shows password_is_set=True, no raw value."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"channels": {"email": {"password": "mysmtppassword"}}},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["channels"]["email"]["password_is_set"] is True
        assert "mysmtppassword" not in str(data)

    def test_patch_settings_clears_email_password(self, http_client: object) -> None:
        """PATCH /settings with empty string clears email.password (password_is_set=False)."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        # First set the password
        http_client.patch(
            "/api/settings",
            json={"channels": {"email": {"password": "mysmtppassword"}}},
        )
        # Then clear it
        resp = http_client.patch(
            "/api/settings",
            json={"channels": {"email": {"password": ""}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"]["email"]["password_is_set"] is False

    def test_patch_settings_sets_channel_enabled(self, http_client: object) -> None:
        """PATCH /settings enables a channel."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"channels": {"email": {"enabled": True}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"]["email"]["enabled"] is True

    def test_get_settings_unauthenticated_returns_401(self, http_client_no_auth: object) -> None:
        """GET /settings without session returns 401."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client_no_auth, TestClient)
        resp = http_client_no_auth.get("/api/settings")
        assert resp.status_code == 401

    def test_patch_settings_unauthenticated_returns_401(self, http_client_no_auth: object) -> None:
        """PATCH /settings without session returns 401."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client_no_auth, TestClient)
        resp = http_client_no_auth.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": 5}},
        )
        assert resp.status_code == 401

    def test_patch_settings_negative_lead_returns_422(self, http_client: object) -> None:
        """PATCH /settings with lead < 0 returns 422 validation.invalid_input."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": -1}},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_settings_repeat_item_less_than_one_returns_422(
        self, http_client: object
    ) -> None:
        """PATCH /settings with repeat item < 1 returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"reminders": {"low_stock_repeat_days": [0, 3, 7]}},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_settings_invalid_scan_time_returns_422(self, http_client: object) -> None:
        """PATCH /settings with bad scan_time format returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"reminders": {"scan_time": "8:00"}},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_settings_invalid_port_returns_422(self, http_client: object) -> None:
        """PATCH /settings with port > 65535 returns 422."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={"channels": {"email": {"port": 99999}}},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_patch_settings_sets_mqtt_fields(self, http_client: object) -> None:
        """PATCH /settings updates MQTT channel fields."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={
                "channels": {
                    "mqtt": {
                        "enabled": True,
                        "host": "mqtt.example.com",
                        "port": 1883,
                        "topic_prefix": "myhome",
                        "discovery_enabled": True,
                    }
                }
            },
        )
        assert resp.status_code == 200
        mqtt = resp.json()["channels"]["mqtt"]
        assert mqtt["enabled"] is True
        assert mqtt["host"] == "mqtt.example.com"
        assert mqtt["port"] == 1883
        assert mqtt["topic_prefix"] == "myhome"
        assert mqtt["discovery_enabled"] is True

    def test_patch_settings_sets_http_fields(self, http_client: object) -> None:
        """PATCH /settings updates HTTP channel fields and masks integration_token."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.patch(
            "/api/settings",
            json={
                "channels": {
                    "http": {
                        "enabled": True,
                        "webhook_url": "https://hook.example.com/notify",
                        "integration_token": "secret-token-123",
                    }
                }
            },
        )
        assert resp.status_code == 200
        http_ = resp.json()["channels"]["http"]
        assert http_["enabled"] is True
        assert http_["webhook_url"] == "https://hook.example.com/notify"
        assert http_["integration_token_is_set"] is True
        assert "secret-token-123" not in str(resp.json())

    def test_patch_idempotent_get_after_patch(self, http_client: object) -> None:
        """GET after PATCH returns the updated values (persisted, not just in response)."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        http_client.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": 10, "scan_time": "09:00"}},
        )

        get_resp = http_client.get("/api/settings")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["reminders"]["best_before_lead_days"] == 10
        assert data["reminders"]["scan_time"] == "09:00"
        # Other fields still at defaults
        assert data["reminders"]["warranty_lead_days"] == 30
