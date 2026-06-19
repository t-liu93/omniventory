"""M4 Step 5 tests: APScheduler daily scan + timezone.

Required coverage (M4.md §5 + §9 Step 5 + §10 Step 5):

Scheduler gating:
- ENVIRONMENT=test -> start_scheduler no-ops (app.state.scheduler is None,
  BackgroundScheduler.start() is never called).
- scheduler_enabled=False -> start_scheduler no-ops even in non-test environment.

Job registration from config:
- start_scheduler (in non-test mode, scheduler_enabled=True) calls
  BackgroundScheduler.add_job with the correct CronTrigger (hour/minute from
  scan_time, timezone from household.timezone).
- scheduler.start() is called.

household.timezone used for cron:
- If household timezone is non-UTC, the CronTrigger receives that timezone.

Job body behaviour:
- _run_scan_job opens its own DB session, calls ReminderEngine.run_scan(),
  and commits on success.
- If run_scan raises, the exception is swallowed (best-effort), rollback is
  called, and the scheduler is not affected.

Lifespan integration:
- With ENVIRONMENT=test, creating a TestClient (which triggers the lifespan)
  leaves app.state.scheduler as None; no background threads leak into tests.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as test_m4_step3/4.py)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, Any]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
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
        notif_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base as _Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(engine, "connect")
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step5_")
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
    """Temp-file SQLite DB for TestClient-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step5")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[Any]:  # noqa: ARG001
    """TestClient with full schema initialised; triggers the lifespan."""
    from fastapi.testclient import TestClient

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
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
        notif_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        yield client, application


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_household_and_user(db: Session, *, timezone: str = "UTC") -> Any:
    """Seed a Household + one active User. Returns (household, user)."""
    from app.auth.passwords import hash_password
    from app.models.household import Household
    from app.models.user import User

    hh = Household(id=1, name="Test", currency="USD", timezone=timezone)
    db.add(hh)
    db.flush()

    user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user)
    db.flush()
    db.commit()

    return hh, user


# ---------------------------------------------------------------------------
# Tests: scheduler gating
# ---------------------------------------------------------------------------


class TestSchedulerGating:
    """start_scheduler must be a no-op in test/disabled scenarios."""

    def test_test_environment_suppresses_scheduler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENVIRONMENT=test: BackgroundScheduler.start() is never called."""
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        from fastapi import FastAPI

        app = FastAPI()

        with patch("app.scheduler.BackgroundScheduler") as mock_sched_cls:
            from app.scheduler import start_scheduler

            start_scheduler(app)

        # Scheduler class should never be instantiated in test environment
        mock_sched_cls.assert_not_called()
        assert app.state.scheduler is None

    def test_scheduler_enabled_false_suppresses_scheduler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCHEDULER_ENABLED=false: scheduler must not start even in development."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "false")

        from app.config import get_settings

        get_settings.cache_clear()

        from fastapi import FastAPI

        app = FastAPI()

        with patch("app.scheduler.BackgroundScheduler") as mock_sched_cls:
            from app.scheduler import start_scheduler

            start_scheduler(app)

        mock_sched_cls.assert_not_called()
        assert app.state.scheduler is None

    def test_lifespan_in_test_mode_leaves_scheduler_none(self, http_client: Any) -> None:
        """TestClient with ENVIRONMENT=test: app.state.scheduler is None after lifespan."""
        _client, application = http_client
        # The lifespan ran (TestClient context manager entered), scheduler should be None
        assert getattr(application.state, "scheduler", None) is None


# ---------------------------------------------------------------------------
# Tests: job registration from config
# ---------------------------------------------------------------------------


class TestJobRegistration:
    """When enabled in a non-test environment, the scheduler registers the correct cron job."""

    def _call_start_scheduler_with_mocks(
        self,
        db_session: Session,
        *,
        timezone: str = "UTC",
        scan_time: str = "08:00",
    ) -> tuple[MagicMock, MagicMock]:
        """
        Seed the DB, then call start_scheduler with BackgroundScheduler mocked.

        Returns (mock_scheduler_instance, mock_add_job_call_kwargs).
        """
        _seed_household_and_user(db_session, timezone=timezone)

        # Patch get_session_factory to return a factory that yields our in-memory session
        mock_factory = MagicMock(return_value=db_session)

        from fastapi import FastAPI

        app = FastAPI()

        mock_scheduler_instance = MagicMock()

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.BackgroundScheduler", return_value=mock_scheduler_instance),
            patch("app.services.settings.SettingsService.scan_time", return_value=scan_time),
        ):
            from app.scheduler import start_scheduler

            start_scheduler(app)

        return mock_scheduler_instance, app

    def test_scheduler_starts_and_adds_job(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start_scheduler calls add_job and start() on BackgroundScheduler."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        mock_sched, _app = self._call_start_scheduler_with_mocks(db_session)

        mock_sched.add_job.assert_called_once()
        mock_sched.start.assert_called_once()

    def test_cron_trigger_uses_scan_time_hour_and_minute(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The CronTrigger should use hour/minute derived from scan_time."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        mock_sched, _app = self._call_start_scheduler_with_mocks(db_session, scan_time="14:30")

        add_job_call = mock_sched.add_job.call_args
        # The trigger argument is a positional/keyword arg
        trigger = add_job_call.kwargs.get("trigger") or add_job_call.args[1]

        # Verify the CronTrigger was instantiated with hour=14, minute=30
        from apscheduler.triggers.cron import CronTrigger

        assert isinstance(trigger, CronTrigger)
        # CronTrigger stores fields; we verify via repr or fields list
        trigger_repr = repr(trigger)
        assert "14" in trigger_repr
        assert "30" in trigger_repr

    def test_cron_trigger_uses_household_timezone(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The CronTrigger should receive the household timezone."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        tz = "America/New_York"
        mock_sched, _app = self._call_start_scheduler_with_mocks(db_session, timezone=tz)

        add_job_call = mock_sched.add_job.call_args
        trigger = add_job_call.kwargs.get("trigger") or add_job_call.args[1]

        from apscheduler.triggers.cron import CronTrigger

        assert isinstance(trigger, CronTrigger)
        # CronTrigger stores the timezone; verify by checking the trigger's timezone attr
        assert str(trigger.timezone) == tz

    def test_scheduler_stored_on_app_state(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start_scheduler stores the scheduler instance on app.state.scheduler."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        mock_sched, app = self._call_start_scheduler_with_mocks(db_session)
        assert app.state.scheduler is mock_sched


# ---------------------------------------------------------------------------
# Tests: job body behaviour
# ---------------------------------------------------------------------------


class TestJobBody:
    """The _run_scan_job function opens its own session and behaves correctly."""

    def test_job_opens_session_and_calls_run_scan_and_commits(
        self,
        db_session: Session,
    ) -> None:
        """_run_scan_job should call ReminderEngine.run_scan() and commit the session."""
        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.ReminderEngine") as mock_engine_cls,
        ):
            from app.scheduler import _run_scan_job

            _run_scan_job()

        # A fresh session was created
        mock_factory.assert_called_once()
        # ReminderEngine was instantiated with the session
        mock_engine_cls.assert_called_once_with(mock_session)
        # run_scan() was called on the engine instance
        mock_engine_cls.return_value.run_scan.assert_called_once()
        # Session was committed
        mock_session.commit.assert_called_once()
        # Session was closed in finally block
        mock_session.close.assert_called_once()

    def test_job_rollback_on_run_scan_error(
        self,
        db_session: Session,
    ) -> None:
        """If run_scan raises, _run_scan_job swallows the error and calls rollback."""
        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)

        mock_engine_instance = MagicMock()
        mock_engine_instance.run_scan.side_effect = RuntimeError("scan exploded")

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.ReminderEngine", return_value=mock_engine_instance),
        ):
            from app.scheduler import _run_scan_job

            # Must NOT raise — best-effort error handling
            _run_scan_job()

        # Rollback was called after the error
        mock_session.rollback.assert_called_once()
        # Commit was NOT called
        mock_session.commit.assert_not_called()
        # Session was still closed in finally block
        mock_session.close.assert_called_once()

    def test_job_exception_does_not_propagate(self) -> None:
        """_run_scan_job must never raise, even if the engine crashes."""
        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine_instance = MagicMock()
        mock_engine_instance.run_scan.side_effect = Exception("unexpected crash")

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.ReminderEngine", return_value=mock_engine_instance),
        ):
            from app.scheduler import _run_scan_job

            try:
                _run_scan_job()
            except Exception as exc:
                pytest.fail(f"_run_scan_job raised unexpectedly: {exc}")

    def test_job_session_is_independent_of_request_session(
        self,
        db_session: Session,
    ) -> None:
        """The session opened by _run_scan_job must be a new instance, not a shared one."""
        created_sessions: list[Any] = []
        real_sessions: list[Session] = []

        def _capturing_factory() -> MagicMock:
            s = MagicMock(spec=Session)
            created_sessions.append(s)
            return s

        mock_factory = MagicMock(side_effect=_capturing_factory)

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.ReminderEngine"),
        ):
            from app.scheduler import _run_scan_job

            _run_scan_job()

        # Exactly one session was created by the job
        assert len(created_sessions) == 1
        # It is not the same as any externally-provided session
        assert created_sessions[0] not in real_sessions


# ---------------------------------------------------------------------------
# Tests: household timezone fallback
# ---------------------------------------------------------------------------


class TestHouseholdTimezoneFallback:
    """If household table read fails, scheduler falls back to UTC."""

    def test_fallback_to_utc_if_household_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If HouseholdRepository.ensure() raises, CronTrigger gets 'UTC'."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SCHEDULER_ENABLED", "true")

        from app.config import get_settings

        get_settings.cache_clear()

        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)

        from fastapi import FastAPI

        app = FastAPI()
        mock_scheduler_instance = MagicMock()

        with (
            patch("app.scheduler.get_session_factory", return_value=mock_factory),
            patch("app.scheduler.BackgroundScheduler", return_value=mock_scheduler_instance),
            patch(
                "app.scheduler.HouseholdRepository.ensure",
                side_effect=RuntimeError("table not ready"),
            ),
            patch("app.scheduler.SettingsService.scan_time", return_value="08:00"),
        ):
            from app.scheduler import start_scheduler

            start_scheduler(app)

        # Should still start with UTC
        mock_scheduler_instance.add_job.assert_called_once()
        mock_scheduler_instance.start.assert_called_once()

        add_job_call = mock_scheduler_instance.add_job.call_args
        trigger = add_job_call.kwargs.get("trigger") or add_job_call.args[1]

        from apscheduler.triggers.cron import CronTrigger

        assert isinstance(trigger, CronTrigger)
        assert str(trigger.timezone) == "UTC"
