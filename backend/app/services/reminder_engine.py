"""Reminder engine -- unified source-pluggable scan (M4 §4.1 / §4.2-§4.5 / §9 Steps 3+4).

``ReminderEngine.run_scan()`` evaluates all sources (best_before, warranty,
low_stock) across all active recipients and writes idempotent notification rows.

``ReminderEngine.evaluate_low_stock(definition_id)`` is the event-trigger
scoped path: same low-stock logic as run_scan, but limited to one definition
across all recipients.  Called by StockMovementService after consume/discard/
adjust, within the same DB transaction (best-effort, savepoint-isolated).

Locked decisions implemented here (M4 §2 / M6 §2)
------------------------------------------
- **One engine, source-pluggable**: each date source is a small ``_DateSource``
  descriptor; the engine loops sources x lots x recipients in a single pass.
- **Recipient routing (M6 Step 5)**: each notification is routed to the
  *effective responsible party* (``lot.responsible_user_id`` → definition's
  ``responsible_user_id`` → fallback to all active users for date sources;
  ``definition.responsible_user_id`` → fallback for low-stock).  Fallback
  preserves the M4 broadcast behaviour exactly.
- **Pref gating (M6 Step 5)**: before creating a notification row for a
  recipient, the engine skips them entirely when both ``notify_in_app`` and
  ``notify_email_digest`` are False (they want nothing from any channel).
- **"Today" honours ``household.timezone``**: ``today_local`` is computed from
  the current UTC time localised into ``household.timezone`` via
  ``zoneinfo.ZoneInfo``; never via ``date.today()`` (which returns the system
  local date and is wrong for deployments with a non-local timezone).
- **Lead resolution per-item > per-user > global** (§4.3): first non-None wins.
- **Date sources fire once per (recipient, lot, target-date)** (§4.4): the
  dedup key ``"{source}:u{uid}:i{lot_id}:{target_date}"`` makes re-runs no-ops.
- **Low-stock episodes** (§4.5 / §3.3): opener fires on going low; repeats
  fire when elapsed >= offset (catch-up); episode closes on recovery.

Low-stock Phase 1 vs Phase 2 split (M6 Step 5)
-----------------------------------------------
``_evaluate_low_stock_for_user`` now receives two separate low sets:

``low_now_for_open``
    The *routed* low set for this user (Phase 1 — open new episodes or fire
    repeats).  Only definitions whose effective responsible party routes to
    this user are included.  A definition routed to another user is excluded
    from this set so the engine does not create opener rows for unrouted users.

``low_now_global``
    The *global* low set — every definition currently below its threshold,
    regardless of routing.  Used by Phase 2 (close recovered episodes): a
    previously-broadcast opener must close on recovery for everyone who holds
    an opener, even if the definition's assignment has since changed.  Phase 2
    never creates rows; it only marks existing openers resolved.

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
from app.repositories.item_definition import ItemDefinitionRepository
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
    maintenance: int = 0
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
        self._definition_repo = ItemDefinitionRepository(db)

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
        active_users = self._user_repo.list_active()
        if not active_users:
            logger.debug("run_scan: no active users -- skipping.")
            return ScanSummary()

        # Build a quick lookup: user_id -> User (for O(1) routing resolution).
        active_by_id: dict[int, User] = {u.id: u for u in active_users}

        # ---- Evaluate each date source ------------------------------------
        summary = ScanSummary()
        all_new: list[Notification] = []

        for source in _DATE_SOURCES:
            lots = source.get_lots(self._instance_repo)
            count, new_notifications = self._evaluate_date_source(
                source=source,
                lots=lots,
                active_users=active_users,
                active_by_id=active_by_id,
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

        # M6 Step 5: precompute routing per low definition so we can derive
        # each user's routed low set efficiently in O(|low_now|) rather than
        # O(|active_users| × |low_now|).
        #
        # For each low definition, resolve its responsible party and map it to
        # the recipient list (_recipients_for handles the fallback-to-all case).
        recipients_by_def: dict[int, list[User]] = {
            def_id: self._recipients_for(
                self._effective_responsible_for_definition_id(def_id),
                active_users,
                active_by_id,
            )
            for def_id in low_now
        }

        # Build a reverse mapping: user_id -> set of low def_ids routed to
        # that user.  This powers Phase 1 (open/repeat) per user.
        user_routed_low: dict[int, set[int]] = {u.id: set() for u in active_users}
        for def_id, def_recipients in recipients_by_def.items():
            for u in def_recipients:
                user_routed_low[u.id].add(def_id)

        for user in active_users:
            low_count, new_notifs = self._evaluate_low_stock_for_user(
                user=user,
                low_now_for_open=user_routed_low[user.id],  # routed: Phase 1
                low_now_global=low_now,  # all low: Phase 2 close
                low_item_map=low_item_map,
                repeat_offsets=repeat_offsets,
                today_local=today_local,
            )
            summary.low_stock += low_count
            all_new.extend(new_notifs)

        # ---- Evaluate maintenance source (M7 §4.5 — additive, untouches above) ---
        maint_count, maint_notifications = self._evaluate_maintenance(
            active_users=active_users,
            active_by_id=active_by_id,
            today_local=today_local,
        )
        summary.maintenance += maint_count
        all_new.extend(maint_notifications)

        # ---- Return new notifications to the caller --------------------------
        # The caller (scheduler job or route handler) MUST:
        #   1. Commit the DB transaction (to persist notification rows).
        #   2. Then call build_dispatcher(db).dispatch(summary.new_notifications, ...)
        #      so that network I/O happens AFTER commit (F1 fix, M4 §2).
        # The engine does NOT dispatch itself — it never holds a dispatcher.
        summary.new_notifications = all_new

        logger.info(
            "run_scan complete: best_before=%d, warranty=%d, low_stock=%d, maintenance=%d, new=%d",
            summary.best_before,
            summary.warranty,
            summary.low_stock,
            summary.maintenance,
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

        M6 Step 5: the definition's effective responsible party is resolved and
        used to route the Phase 1 opener/repeat to the correct user(s).  Phase 2
        (close on recovery) still runs for ALL users who hold an opener for this
        definition, regardless of routing — a previously-broadcast opener must
        close for everyone.

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

        active_users = self._user_repo.list_active()
        if not active_users:
            return []

        active_by_id: dict[int, User] = {u.id: u for u in active_users}

        from app.services.low_stock import LowStockService

        low_stock_items = LowStockService(self._db).compute()
        low_now: set[int] = {item.definition_id for item in low_stock_items}
        low_item_map = {item.definition_id: item for item in low_stock_items}

        repeat_offsets = sorted(
            {o for o in self._settings_service.low_stock_repeat_days() if o >= 1}
        )

        # Restrict to just the requested definition (global low set, for Phase 2).
        scoped_low_now: set[int] = {definition_id} if definition_id in low_now else set()

        # M6 Step 5: route the scoped definition to the correct recipient set.
        responsible_id = self._effective_responsible_for_definition_id(definition_id)
        def_recipients = self._recipients_for(responsible_id, active_users, active_by_id)
        def_recipient_ids: set[int] = {u.id for u in def_recipients}

        all_new: list[Notification] = []

        for user in active_users:
            # Phase 1: only route the definition to users who are recipients.
            # Phase 2 (close): all users with openers still get closed on recovery.
            low_now_for_open = scoped_low_now if user.id in def_recipient_ids else set()
            _count, new_notifs = self._evaluate_low_stock_for_user(
                user=user,
                low_now_for_open=low_now_for_open,
                low_now_global=scoped_low_now,
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
    # Internal: recipient routing helpers (M6 Step 5)
    # ------------------------------------------------------------------

    def _effective_responsible_for_lot(self, lot: StockInstance) -> int | None:
        """Return the effective responsible user ID for a date-source lot.

        Resolution (M6 §4.4 / §2 "Responsible party"):
        1. ``lot.responsible_user_id``               (per-lot override).
        2. ``lot.definition.responsible_user_id``    (definition default).
        3. ``None``                                  (unassigned → fallback to all).

        User IDs are always ≥ 1 so ``or`` short-circuits correctly on None/0.
        The definition relationship is already eager-loaded by the repository
        (``list_live_with_best_before`` / ``list_live_with_warranty`` both use
        ``joinedload(StockInstance.definition)``), so no extra query is issued.
        """
        return lot.responsible_user_id or lot.definition.responsible_user_id or None

    def _effective_responsible_for_definition_id(self, def_id: int) -> int | None:
        """Return the responsible_user_id for an ItemDefinition, or None.

        Used for low-stock routing where we have a definition_id from
        ``LowStockService.compute()`` but not a loaded definition object.
        Issues one ``SELECT`` per definition; callers that iterate many
        definitions call this once and cache the result (see ``run_scan``).
        """
        defn = self._definition_repo.get(def_id)
        if defn is None:
            return None
        return defn.responsible_user_id or None

    def _recipients_for(
        self,
        responsible_user_id: int | None,
        active_users: list[User],
        active_by_id: dict[int, User],
    ) -> list[User]:
        """Resolve the recipient list for a given effective responsible party.

        If ``responsible_user_id`` is non-None and the user is currently active,
        returns a single-element list containing that user.  Otherwise — when
        unassigned (None), or when the responsible user has been deactivated /
        deleted (SET NULL → None, or ID absent from ``active_by_id``) — returns
        the full ``active_users`` list, which is exactly the M4 broadcast
        behaviour.  This ensures no reminder is ever dropped on a SET NULL or
        deactivation event.

        Parameters
        ----------
        responsible_user_id:
            The resolved effective responsible party, or ``None``.
        active_users:
            All currently active users (the M4 fallback set).
        active_by_id:
            Mapping from user_id to User for O(1) lookup.
        """
        if responsible_user_id is not None and responsible_user_id in active_by_id:
            return [active_by_id[responsible_user_id]]
        # Unassigned, inactive, or deleted responsible → M4 broadcast fallback.
        return active_users

    # ------------------------------------------------------------------
    # Internal: date-source evaluation
    # ------------------------------------------------------------------

    def _evaluate_date_source(
        self,
        *,
        source: _DateSource,
        lots: list[StockInstance],
        active_users: list[User],
        active_by_id: dict[int, User],
        today_local: date,
    ) -> tuple[int, list[Notification]]:
        """Evaluate one date source, routing each lot to its effective recipients.

        M6 Step 5 change: the outer loop is now over *lots* (not recipients).
        For each lot, we resolve the effective responsible party and derive the
        recipient list via ``_recipients_for``.  Then we apply the existing
        per-(user, lot, date) dedup/window/create logic to exactly those
        recipients.  Dedup keys are unchanged so existing notification rows
        deduplicate correctly.

        Returns (created_count, list_of_new_notifications).
        """
        created_count = 0
        new_notifications: list[Notification] = []

        for lot in lots:
            target_date = source.get_target_date(lot)
            if target_date is None:
                continue  # Defensive: the query should already filter these out.

            # M6 Step 5: resolve recipients per lot.
            effective_rid = self._effective_responsible_for_lot(lot)
            recipients = self._recipients_for(effective_rid, active_users, active_by_id)

            definition_lead: int | None = lot.definition.reminder_lead_days

            for user in recipients:
                # M6 Step 5 pref gate: skip users who want no notifications at all.
                # Do NOT re-route to others — this is an independent per-recipient
                # skip applied after routing (M6 §4.5).
                if not user.notify_in_app and not user.notify_email_digest:
                    continue

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
        low_now_for_open: set[int],
        low_now_global: set[int],
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
        low_now_for_open:
            **Routed** set of definition IDs to consider for Phase 1
            (open new episodes or fire repeats).  Contains only the defs for
            which this user is the effective recipient (M6 §4.4).
        low_now_global:
            **Global** set of all definition IDs currently below their
            threshold.  Used by Phase 2 (close recovered episodes): a
            previously-broadcast opener must close for everyone who holds one,
            regardless of current routing (M6 §4.4 / the Phase-1-vs-Phase-2
            split described in the module docstring).
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

        # M6 Step 5 pref gate: only create rows if the user wants at least one
        # channel.  Phase 2 (close) runs regardless — resolving episodes is
        # purely marking existing rows as resolved, not creating new ones.
        create_for_user = user.notify_in_app or user.notify_email_digest

        # --- Phase 1: open new episodes or fire repeats for low definitions --
        if create_for_user:
            for def_id in low_now_for_open:
                item = low_item_map[def_id]  # LowStockItem
                opener = self._notification_repo.open_low_stock_opener(user.id, def_id)

                if opener is None:
                    # No open episode -- open a new one (opener row, offset_days=0).
                    #
                    # Same-day re-open disambiguation (walkthrough fix #3):
                    # When a definition goes low, recovers, and goes low again on the
                    # SAME calendar day, the naive dedup key
                    # "low_stock:u{uid}:d{def}:{today}:o0" collides with the
                    # already-resolved opener for the earlier episode.
                    # create_if_absent matches purely by (user_id, dedup_key) and
                    # ignores resolved_at, so it returns created=False on the clash
                    # and the user gets no new notification.
                    #
                    # Fix: count ALL opener rows (regardless of resolved_at) anchored
                    # on today.  If this is the first episode of the day (seq==0), use
                    # the legacy bare key so already-stored rows are unaffected.
                    # If seq>=1 (a re-open), append "#<seq>" to produce a fresh key
                    # that has never been written before.
                    seq = self._notification_repo.count_low_stock_openers_on(
                        user.id, def_id, today_local
                    )
                    params = _build_low_stock_params(item)
                    base_dedup = f"low_stock:u{user.id}:d{def_id}:{today_local.isoformat()}:o0"
                    # The "#<seq>" suffix is ONLY for opener (offset_days=0) disambiguation
                    # on the same anchor date.  Repeat keys (offset_days>=1) carry NO such
                    # suffix: among episodes sharing one anchor date, only the last open one
                    # can reach the repeat stage -- the earlier ones resolved the same day
                    # (elapsed=0 < every repeat offset>=1), so they never wrote a repeat row.
                    dedup = base_dedup if seq == 0 else f"{base_dedup}#{seq}"
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
            if opener.subject_id in low_now_global:
                # Still low globally -- leave episode open.
                continue
            # Not in low_now_global: the definition has recovered (or is now
            # scoped to a different definition in the event-trigger path).
            # In the event-trigger path we only close episodes for the
            # single scoped definition; other definitions' openers are
            # evaluated by their own event hooks or the daily scan.
            if scoped_definition_id is not None and opener.subject_id != scoped_definition_id:
                continue
            self._notification_repo.mark_resolved(opener)

        return created_count, new_notifications

    # ------------------------------------------------------------------
    # Internal: maintenance source (M7 §4.5 — additive pass)
    # ------------------------------------------------------------------

    def _evaluate_maintenance(
        self,
        *,
        active_users: list[User],
        active_by_id: dict[int, User],
        today_local: date,
    ) -> tuple[int, list[Notification]]:
        """Additive maintenance-due reminder pass (M7 §4.5).

        Evaluates **all** active maintenance schedules (no scalar horizon) and
        creates ``Notification`` rows for those whose advance-notice window has
        opened (``today_local >= next_due_date - lead``).

        This pass is **purely additive**: the two ``_DateSource`` evaluators and
        the low-stock episode logic are completely unchanged.

        Lead resolution
        ---------------
        Per-schedule ``lead_days`` → global ``reminders.maintenance.lead_days``
        (default 7, M7 §4.6).  A lead of 0 fires on the due date itself;
        overdue schedules (today past ``next_due_date``) also fire, with a
        negative ``days_remaining`` so the renderer shows "overdue".

        No horizon
        ----------
        ``list_active()`` returns ALL active schedules; the window test is
        applied here in Python.  A fixed DB-side horizon would silently drop a
        schedule with a large per-schedule ``lead_days`` whose window is already
        open — this is the "long-lead" B-class fix (M7 §4.1 / §4.5).

        Routing / dedup
        ---------------
        Routing reuses M6 verbatim: ``_effective_responsible_for_lot(s.instance)``
        → ``_recipients_for`` → pref gate.  Dedup fires once per
        ``(recipient, schedule, next_due_date)``; completion advances
        ``next_due_date`` so the next occurrence gets a fresh key.

        The low-stock-only columns (``episode_started_on``, ``offset_days``)
        are left at their defaults (not passed) — maintenance is NOT an episode.

        Returns
        -------
        (created_count, list_of_new_notifications)
        """
        from app.repositories.maintenance_schedule import MaintenanceScheduleRepository

        schedules = MaintenanceScheduleRepository(self._db).list_active()
        created_count = 0
        new_notifications: list[Notification] = []

        for s in schedules:
            lead = (
                s.lead_days
                if s.lead_days is not None
                else self._settings_service.maintenance_lead_days()
            )
            window: date = s.next_due_date - timedelta(days=lead)
            if today_local < window:
                # Too early: window has not opened yet.
                continue

            recipients = self._recipients_for(
                self._effective_responsible_for_lot(s.instance),
                active_users,
                active_by_id,
            )
            for u in recipients:
                # M6 pref gate: skip users who want no notifications at all.
                if not u.notify_in_app and not u.notify_email_digest:
                    continue

                dedup = f"maintenance:u{u.id}:s{s.id}:{s.next_due_date.isoformat()}"
                params: dict[str, object] = {
                    "name": s.name,
                    "instance_name": s.instance.definition.name,
                    "next_due_date": s.next_due_date.isoformat(),
                    "days_remaining": (s.next_due_date - today_local).days,
                    "location_id": s.instance.location_id,
                    "instance_id": s.instance_id,
                }
                notification, created = self._notification_repo.create_if_absent(
                    user_id=u.id,
                    source="maintenance",
                    subject_type="maintenance_schedule",
                    subject_id=s.id,
                    dedup_key=dedup,
                    message_code="reminder.maintenance",
                    params=params,
                )
                if created:
                    created_count += 1
                    new_notifications.append(notification)

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
