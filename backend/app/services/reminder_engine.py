"""Reminder engine -- unified source-pluggable scan (M4 §4.1 / §4.2-§4.5 / §9 Steps 3+4).

``ReminderEngine.run_scan()`` evaluates all sources (best_before, warranty,
low_stock) across all active recipients and writes idempotent notification rows.

``ReminderEngine.evaluate_low_stock(definition_id)`` is the event-trigger
scoped path: same low-stock logic as run_scan, but limited to one definition
across all recipients.  Called by StockMovementService after consume/discard/
adjust, within the same DB transaction (best-effort, savepoint-isolated).

Locked decisions implemented here (M4 §2)
------------------------------------------
- **One engine, source-pluggable**: each date source is a small ``_DateSource``
  descriptor; the engine loops sources x recipients x lots in a single pass.
- **Recipients = all active users (M4)**: ``UserRepository.list_active()``.
- **"Today" honours ``household.timezone``**: ``today_local`` is computed from
  the current UTC time localised into ``household.timezone`` via
  ``zoneinfo.ZoneInfo``; never via ``date.today()`` (which returns the system
  local date and is wrong for deployments with a non-local timezone).
- **Lead resolution per-item > per-user > global** (§4.3): first non-None wins.
- **Date sources fire once per (recipient, lot, target-date)** (§4.4): the
  dedup key ``"{source}:u{uid}:i{lot_id}:{target_date}"`` makes re-runs no-ops.
- **Low-stock episodes** (§4.5 / §3.3): opener fires on going low; repeats
  fire when elapsed >= offset (catch-up); episode closes on recovery.

Testability
-----------
``run_scan(today_local=None)``  When ``None`` (the default) the scan computes
    ``today_local`` from ``household.timezone``; tests inject a fixed ``date``
    to remove clock dependency.

``evaluate_low_stock(definition_id, today_local=None)``  When ``None`` (the
    default) the date is computed from ``household.timezone``.

Out of scope (Step 4)
---------------------
- APScheduler wiring (Step 5)
- Inbox list / mark-read API (Step 6)
- External channels (Steps 7-10)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.stock import LOW_STOCK_TRIGGER_LEVEL
from app.models.notification import Notification
from app.models.stock_instance import StockInstance
from app.models.user import User
from app.repositories.household import HouseholdRepository
from app.repositories.notification import NotificationRepository
from app.repositories.stock_instance import StockInstanceRepository
from app.repositories.user import UserRepository
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source descriptor (pluggable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DateSource:
    """Descriptor for a date-based reminder source.

    Parameters
    ----------
    name:
        Stable source identifier used in the dedup key and ``notifications.source``
        column (e.g. ``"best_before"``, ``"warranty"``).
    get_target_date:
        Callable that extracts the relevant date from a ``StockInstance``; returns
        ``None`` when the date is not applicable to this lot.
    get_per_user_lead:
        Callable that extracts the per-user lead-days override from a ``User``;
        returns ``None`` when the user has no override for this source.
    message_code:
        i18n code stored in ``notifications.message_code`` (e.g.
        ``"reminder.best_before"``).
    get_lots:
        Callable that retrieves all live lots carrying this date from the
        repository.
    """

    name: str
    get_target_date: Callable[[StockInstance], date | None]
    get_per_user_lead: Callable[[User], int | None]
    message_code: str
    get_lots: Callable[[StockInstanceRepository], list[StockInstance]]


# The two date sources (best_before and warranty).
_DATE_SOURCES: list[_DateSource] = [
    _DateSource(
        name="best_before",
        get_target_date=lambda lot: lot.best_before_date,
        get_per_user_lead=lambda user: user.reminder_best_before_lead_days,
        message_code="reminder.best_before",
        get_lots=lambda repo: repo.list_live_with_best_before(),
    ),
    _DateSource(
        name="warranty",
        get_target_date=lambda lot: lot.warranty_expires,
        get_per_user_lead=lambda user: user.reminder_warranty_lead_days,
        message_code="reminder.warranty",
        get_lots=lambda repo: repo.list_live_with_warranty(),
    ),
]


# ---------------------------------------------------------------------------
# Lead resolution chain (§4.3)
# ---------------------------------------------------------------------------


def _resolve_lead(
    source: _DateSource,
    definition_lead: int | None,
    user: User,
    settings_service: SettingsService,
) -> int:
    """Resolve the effective lead-time in days for a source / definition / user.

    Resolution chain (§4.3, first non-None wins):
    1. ``definition.reminder_lead_days`` -- per-item override (applies to all
       date sources on this definition's lots).
    2. Per-user override -- ``user.reminder_best_before_lead_days`` for
       ``best_before``, ``user.reminder_warranty_lead_days`` for ``warranty``.
    3. Global default -- ``settings_service.best_before_lead_days()`` or
       ``settings_service.warranty_lead_days()``.

    All resolved values are ``>= 0`` (Pydantic-validated at write time).
    A lead of 0 means fire on the target date itself.

    Parameters
    ----------
    source:
        The ``_DateSource`` descriptor for the current source.
    definition_lead:
        ``definition.reminder_lead_days`` (may be ``None`` = "inherit").
    user:
        The recipient; carries per-user overrides.
    settings_service:
        Provides the global defaults.
    """
    # 1. Per-item override wins first
    if definition_lead is not None:
        return definition_lead

    # 2. Per-user override
    per_user = source.get_per_user_lead(user)
    if per_user is not None:
        return per_user

    # 3. Global default
    if source.name == "best_before":
        return settings_service.best_before_lead_days()
    # warranty
    return settings_service.warranty_lead_days()


# ---------------------------------------------------------------------------
# Run summary dataclass (mirrors ReminderRunSummary schema)
# ---------------------------------------------------------------------------


@dataclass
class ScanSummary:
    """Created-notification counts returned by ``run_scan()``.

    ``new_notifications`` carries the freshly created ``Notification`` rows so
    that the **caller** can dispatch them to external channels *after* committing
    the DB transaction (F1 fix: dispatch happens post-commit, never inside the
    engine).  This field is internal only — it is NOT part of the wire schema
    ``ReminderRunSummary``; existing callers that read ``.best_before``,
    ``.warranty``, or ``.low_stock`` are unaffected.
    """

    best_before: int = 0
    warranty: int = 0
    low_stock: int = 0
    new_notifications: list[Notification] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.new_notifications is None:
            self.new_notifications = []


# ---------------------------------------------------------------------------
# Decimal serialisation helper
# ---------------------------------------------------------------------------


def _decimal_to_str(value: Decimal | None) -> str | None:
    """Convert a Decimal to string for JSON params storage, or keep None as None.

    ``json.dumps(Decimal(...))`` raises TypeError.  We store Decimal values as
    strings in the params JSON blob, matching the existing wire convention for
    quantity fields (roadmap §2.9: Decimal as string on the wire).
    """
    if value is None:
        return None
    return str(value)


def _build_low_stock_params(item: object, *, offset: int | None = None) -> dict[str, object]:
    """Build the notification params dict for a low-stock notification.

    For **exact** mode the params carry numeric ``current`` / ``threshold``
    (as strings, per the Decimal-as-string wire convention).

    For **level** mode the params carry ``level`` (the qualitative trigger
    level code, currently always ``LOW_STOCK_TRIGGER_LEVEL = "low"``).
    The meaningless numeric keys are omitted so that renderers — both the
    server-side email catalog and the frontend i18n templates — never receive
    ``None`` and produce blank output.

    Parameters
    ----------
    item:
        A ``LowStockItem`` (accessed via ``getattr`` to avoid a circular
        import; the ``LowStockItem`` type is imported at call time inside
        ``run_scan`` / ``evaluate_low_stock``).
    offset:
        When present, adds ``"offset": offset`` to the dict (repeat path).
    """
    mode: str = getattr(item, "mode", "exact")
    name: object = getattr(item, "name", "")

    if mode == "level":
        params: dict[str, object] = {
            "name": name,
            "mode": mode,
            "level": LOW_STOCK_TRIGGER_LEVEL,
        }
    else:
        params = {
            "name": name,
            "current": _decimal_to_str(getattr(item, "current", None)),
            "threshold": _decimal_to_str(getattr(item, "threshold", None)),
            "mode": mode,
        }

    if offset is not None:
        params["offset"] = offset

    return params


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ReminderEngine:
    """Orchestrates the reminder scan across all sources and recipients.

    Instantiate once per request/job run; the engine is stateless between
    ``run_scan()`` calls.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._user_repo = UserRepository(db)
        self._instance_repo = StockInstanceRepository(db)
        self._notification_repo = NotificationRepository(db)
        self._household_repo = HouseholdRepository(db)
        self._settings_service = SettingsService(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_scan(self, today_local: date | None = None) -> ScanSummary:
        """Evaluate all sources for all active recipients.

        Parameters
        ----------
        today_local:
            The reference date for this scan.  When ``None`` (the default in
            production), the date is computed from ``household.timezone`` so
            that the scan honours the household's clock.  Tests inject a fixed
            date to remove clock dependency (but should also verify the tz-aware
            default path separately).

        Returns
        -------
        ScanSummary
            Per-source counts of newly created notification rows.
        """
        # ---- Resolve today ------------------------------------------------
        if today_local is None:
            household = self._household_repo.ensure()
            today_local = self._today_in_tz(household.timezone)

        # ---- Collect recipients -------------------------------------------
        recipients = self._user_repo.list_active()
        if not recipients:
            logger.debug("run_scan: no active users -- skipping.")
            return ScanSummary()

        # ---- Evaluate each date source ------------------------------------
        summary = ScanSummary()
        all_new: list[Notification] = []

        for source in _DATE_SOURCES:
            lots = source.get_lots(self._instance_repo)
            count, new_notifications = self._evaluate_date_source(
                source=source,
                lots=lots,
                recipients=recipients,
                today_local=today_local,
            )
            # Map source name to summary field
            if source.name == "best_before":
                summary.best_before += count
            elif source.name == "warranty":
                summary.warranty += count
            all_new.extend(new_notifications)

        # ---- Evaluate low-stock source ------------------------------------
        # Import LowStockService here to avoid a potential import cycle;
        # reminder_engine is imported by stock_movement which is not in this
        # module's import tree, but keeping this local is defensive.
        from app.services.low_stock import LowStockService

        low_stock_items = LowStockService(self._db).compute()
        low_now: set[int] = {item.definition_id for item in low_stock_items}
        # Build a lookup map: definition_id -> LowStockItem for params building.
        low_item_map = {item.definition_id: item for item in low_stock_items}

        repeat_offsets = sorted(
            {o for o in self._settings_service.low_stock_repeat_days() if o >= 1}
        )

        for user in recipients:
            low_count, new_notifs = self._evaluate_low_stock_for_user(
                user=user,
                low_now=low_now,
                low_item_map=low_item_map,
                repeat_offsets=repeat_offsets,
                today_local=today_local,
            )
            summary.low_stock += low_count
            all_new.extend(new_notifs)

        # ---- Return new notifications to the caller --------------------------
        # The caller (scheduler job or route handler) MUST:
        #   1. Commit the DB transaction (to persist notification rows).
        #   2. Then call build_dispatcher(db).dispatch(summary.new_notifications, ...)
        #      so that network I/O happens AFTER commit (F1 fix, M4 §2).
        # The engine does NOT dispatch itself — it never holds a dispatcher.
        summary.new_notifications = all_new

        logger.info(
            "run_scan complete: best_before=%d, warranty=%d, low_stock=%d, new=%d",
            summary.best_before,
            summary.warranty,
            summary.low_stock,
            len(all_new),
        )
        return summary

    def evaluate_low_stock(
        self,
        definition_id: int,
        today_local: date | None = None,
    ) -> list[Notification]:
        """Evaluate low-stock for a single definition across all active recipients.

        This is the event-trigger path called by StockMovementService after
        consume/discard/adjust.  It applies the same open/repeat/close logic as
        run_scan but scoped to one definition.  Used for immediate in-app
        feedback without waiting for the daily scan.

        Returns the list of newly created ``Notification`` rows so that the
        caller (route handler) can dispatch instant channels post-commit
        (Step 8 §4.6: "scan + event paths"; dispatch after commit is the F1 fix).

        Parameters
        ----------
        definition_id:
            The definition to evaluate.
        today_local:
            Reference date; when ``None``, computed from ``household.timezone``.

        Returns
        -------
        list[Notification]
            Newly created notification rows (may be empty if definition is
            not low or notifications already existed from prior evaluation).
        """
        if today_local is None:
            household = self._household_repo.ensure()
            today_local = self._today_in_tz(household.timezone)

        recipients = self._user_repo.list_active()
        if not recipients:
            return []

        from app.services.low_stock import LowStockService

        low_stock_items = LowStockService(self._db).compute()
        low_now: set[int] = {item.definition_id for item in low_stock_items}
        low_item_map = {item.definition_id: item for item in low_stock_items}

        repeat_offsets = sorted(
            {o for o in self._settings_service.low_stock_repeat_days() if o >= 1}
        )

        # Restrict low_now to just the requested definition so that the
        # per-user loop only touches this definition.
        scoped_low_now: set[int] = {definition_id} if definition_id in low_now else set()

        all_new: list[Notification] = []

        for user in recipients:
            _count, new_notifs = self._evaluate_low_stock_for_user(
                user=user,
                low_now=scoped_low_now,
                low_item_map=low_item_map,
                repeat_offsets=repeat_offsets,
                today_local=today_local,
                scoped_definition_id=definition_id,
            )
            all_new.extend(new_notifs)

        # Callers (route handlers) dispatch instant channels post-commit:
        #   build_dispatcher(db).dispatch(all_new, include_email_digest=False)
        # The engine does NOT dispatch itself (F1 fix: network I/O after commit).
        return all_new

    # ------------------------------------------------------------------
    # Internal: date-source evaluation
    # ------------------------------------------------------------------

    def _evaluate_date_source(
        self,
        *,
        source: _DateSource,
        lots: list[StockInstance],
        recipients: list[User],
        today_local: date,
    ) -> tuple[int, list[Notification]]:
        """Evaluate one date source across all recipients x lots.

        Returns (created_count, list_of_new_notifications).
        """
        created_count = 0
        new_notifications: list[Notification] = []

        for user in recipients:
            for lot in lots:
                target_date = source.get_target_date(lot)
                if target_date is None:
                    continue  # Defensive: the query should already filter these out.

                definition_lead: int | None = lot.definition.reminder_lead_days
                lead = _resolve_lead(source, definition_lead, user, self._settings_service)

                window: date = target_date - timedelta(days=lead)
                if today_local < window:
                    continue  # Too early -- fire when today_local >= window.

                dedup = f"{source.name}:u{user.id}:i{lot.id}:{target_date.isoformat()}"
                params = {
                    "name": lot.definition.name,
                    "date": target_date.isoformat(),
                    "days_remaining": (target_date - today_local).days,
                    "location_id": lot.location_id,
                }

                notification, created = self._notification_repo.create_if_absent(
                    user_id=user.id,
                    source=source.name,
                    subject_type="instance",
                    subject_id=lot.id,
                    dedup_key=dedup,
                    message_code=source.message_code,
                    params=params,
                )

                if created:
                    created_count += 1
                    new_notifications.append(notification)

        return created_count, new_notifications

    # ------------------------------------------------------------------
    # Internal: low-stock episode evaluation (§4.5)
    # ------------------------------------------------------------------

    def _evaluate_low_stock_for_user(
        self,
        *,
        user: User,
        low_now: set[int],
        low_item_map: Mapping[int, object],  # definition_id -> LowStockItem
        repeat_offsets: list[int],
        today_local: date,
        scoped_definition_id: int | None = None,
    ) -> tuple[int, list[Notification]]:
        """Apply the episode open/repeat/close logic for one user.

        Parameters
        ----------
        user:
            The recipient.
        low_now:
            Set of definition IDs currently below their threshold.
        low_item_map:
            Maps definition_id -> LowStockItem (for params building).
        repeat_offsets:
            Sorted list of repeat offset days (each >= 1).
        today_local:
            Reference date for this evaluation.
        scoped_definition_id:
            When set (event-trigger path), only close episodes for this
            definition (not all open openers for the user); episodes for
            other definitions are left untouched.

        Returns
        -------
        (created_count, new_notifications)
        """
        created_count = 0
        new_notifications: list[Notification] = []

        # --- Phase 1: open new episodes or fire repeats for low definitions --
        for def_id in low_now:
            item = low_item_map[def_id]  # LowStockItem
            opener = self._notification_repo.open_low_stock_opener(user.id, def_id)

            if opener is None:
                # No open episode -- open a new one (opener row, offset_days=0).
                params = _build_low_stock_params(item)
                dedup = f"low_stock:u{user.id}:d{def_id}:{today_local.isoformat()}:o0"
                notification, created = self._notification_repo.create_if_absent(
                    user_id=user.id,
                    source="low_stock",
                    subject_type="definition",
                    subject_id=def_id,
                    dedup_key=dedup,
                    message_code="reminder.low_stock",
                    params=params,
                    episode_started_on=today_local,
                    offset_days=0,
                )
                if created:
                    created_count += 1
                    new_notifications.append(notification)
            else:
                # Episode already open -- fire any repeat offsets whose
                # threshold has been reached.
                # elapsed >= 0 always; repeat_offsets each >= 1 so opener is
                # never also a repeat.
                elapsed = (today_local - opener.episode_started_on).days  # type: ignore[operator]
                item = low_item_map[def_id]
                for o in repeat_offsets:
                    if o > elapsed:
                        # Not reached yet; remaining offsets are even larger
                        # (list is sorted), so break early.
                        break
                    # o <= elapsed: this repeat should have fired.
                    params = _build_low_stock_params(item, offset=o)
                    dedup = (
                        f"low_stock:u{user.id}:d{def_id}"
                        f":{opener.episode_started_on.isoformat()}:o{o}"  # type: ignore[union-attr]
                    )
                    notification, created = self._notification_repo.create_if_absent(
                        user_id=user.id,
                        source="low_stock",
                        subject_type="definition",
                        subject_id=def_id,
                        dedup_key=dedup,
                        message_code="reminder.low_stock_repeat",
                        params=params,
                        episode_started_on=opener.episode_started_on,
                        offset_days=o,
                    )
                    if created:
                        created_count += 1
                        new_notifications.append(notification)

        # --- Phase 2: close episodes for recovered definitions ----------------
        # Fetch all open openers for this user.
        open_openers = self._notification_repo.open_low_stock_openers(user.id)
        for opener in open_openers:
            if opener.subject_id in low_now:
                # Still low -- leave episode open.
                continue
            # Not in low_now: the definition has recovered (or is now
            # scoped to a different definition in the event-trigger path).
            # In the event-trigger path we only close episodes for the
            # single scoped definition; other definitions' openers are
            # evaluated by their own event hooks or the daily scan.
            if scoped_definition_id is not None and opener.subject_id != scoped_definition_id:
                continue
            self._notification_repo.mark_resolved(opener)

        return created_count, new_notifications

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _today_in_tz(timezone: str) -> date:
        """Return today's date in the given IANA timezone.

        Uses ``zoneinfo.ZoneInfo`` (stdlib since Python 3.9) to convert the
        current UTC instant to the household-local date.  Never uses
        ``date.today()`` which returns the system's local date and is wrong
        when the deployment timezone differs from the household timezone.
        """
        from datetime import datetime

        tz = ZoneInfo(timezone)
        now_utc = datetime.now(tz=UTC)
        return now_utc.astimezone(tz).date()
