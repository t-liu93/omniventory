"""APScheduler daily scan wrapper (M4 §4.7 / §9 Step 5).

``start_scheduler(app)`` starts a ``BackgroundScheduler`` in the FastAPI
lifespan and registers a daily ``CronTrigger`` that runs
``ReminderEngine.run_scan()`` at the household-local time configured in
``reminders.scan_time`` (``SettingsService.scan_time()``).

Guard conditions (either of the following prevents the scheduler starting):
- ``settings.environment == "test"`` — keeps CI/pytest free of background threads.
- ``settings.scheduler_enabled is False`` — env-level kill-switch for other
  non-test environments where the operator prefers the on-demand endpoint.

The scheduler instance (if started) is stored on ``app.state.scheduler`` so
that the lifespan can shut it down cleanly on application exit.

Session isolation
-----------------
The scheduled job opens its **own** ``Session`` via ``get_session_factory()``
and closes it in a ``finally`` block.  It never shares a session with the
request context; this is required because APScheduler runs jobs in its own
thread pool.

Failure handling
----------------
The job body wraps everything in ``try/except Exception`` so that any error
(DB error, engine bug, …) is **logged and swallowed** — it never kills the
scheduler thread.  The session is rolled back on error and always closed.

scan_time / timezone changes
----------------------------
Per M4 §12 (accepted scope limit), a ``reminders.scan_time`` or
``household.timezone`` change takes effect on **next restart**; live
re-registration is a noted refinement.  The on-demand
``POST /api/reminders/run`` endpoint covers immediate needs.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.db.base import get_session_factory
from app.repositories.household import HouseholdRepository
from app.services.reminder_engine import ReminderEngine
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


def start_scheduler(app: FastAPI) -> None:
    """Start the APScheduler background scheduler and store it on ``app.state``.

    No-ops (returns immediately without starting anything) if:
    - ``settings.environment == "test"``  — CI/test safety gate.
    - ``settings.scheduler_enabled is False`` — operator kill-switch.

    The scheduler fires ``ReminderEngine.run_scan()`` daily at the time read
    from ``SettingsService.scan_time()`` (``"HH:MM"``) in the timezone read
    from ``HouseholdRepository.ensure().timezone`` at startup time.
    """
    from app.config import get_settings

    settings = get_settings()

    # Safety gates: no threads in test mode or when explicitly disabled.
    if settings.environment == "test":
        logger.debug("start_scheduler: environment=test — scheduler suppressed.")
        app.state.scheduler = None
        return

    if not settings.scheduler_enabled:
        logger.info("start_scheduler: scheduler_enabled=False — scheduler suppressed.")
        app.state.scheduler = None
        return

    # ---- Resolve scan_time and household timezone ----------------------------
    factory = get_session_factory()
    db = factory()
    try:
        scan_time_str = SettingsService(db).scan_time()  # "HH:MM"
        try:
            hh_timezone = HouseholdRepository(db).ensure().timezone
        except Exception:
            logger.warning(
                "start_scheduler: could not read household.timezone — "
                "falling back to UTC for the cron trigger.",
                exc_info=True,
            )
            hh_timezone = "UTC"
    finally:
        db.close()

    # Parse "HH:MM"
    hour_str, minute_str = scan_time_str.split(":")
    cron_hour = int(hour_str)
    cron_minute = int(minute_str)

    # ---- Build and start the scheduler --------------------------------------
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run_scan_job,
        trigger=CronTrigger(
            hour=cron_hour,
            minute=cron_minute,
            timezone=hh_timezone,
        ),
        id="daily_reminder_scan",
        name="Daily reminder scan",
        replace_existing=True,
    )

    scheduler.start()
    app.state.scheduler = scheduler

    logger.info(
        "Scheduler started: daily reminder scan at %s in timezone %s.",
        scan_time_str,
        hh_timezone,
    )


def _run_scan_job() -> None:
    """APScheduler job: run ``ReminderEngine.run_scan()`` in its own DB session.

    - Opens a fresh session from ``get_session_factory()`` (thread-safe;
      never shares a session with request context).
    - Commits on success; rolls back on error.
    - All exceptions are caught and logged — never propagated to the scheduler
      thread (best-effort; a single failure must not kill future runs).
    """
    factory = get_session_factory()
    db = factory()
    try:
        ReminderEngine(db).run_scan()
        db.commit()
        logger.info("Scheduled reminder scan completed successfully.")
    except Exception:
        logger.exception("Scheduled reminder scan failed — rolling back.")
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback after scan failure also failed.")
    finally:
        db.close()
