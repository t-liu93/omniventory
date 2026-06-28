"""Stock-tracking constants for the Omniventory backend.

This module defines the canonical sets of valid values for stock-tracking
fields that are validated at the application layer.

Design decisions (M2 §2, roadmap §2.11):
- Validated **app-layer** (here), not via DB CHECK constraints, so the set
  can grow without a schema migration.
- ``STOCK_TRACKING_MODES`` covers the three per-definition modes introduced
  in M2 §3.1 / §3.4.
- ``STOCK_LEVELS`` covers the qualitative level values for ``level``-mode
  instances (§3.2 / §3.4).  Used starting in Step 3 (instance alterations);
  defined here in Step 1 alongside the tracking-mode constants.
"""

# The three per-definition stock-tracking modes (M2 §2 / §3.4).
#   exact  — Decimal quantity derived from the movement ledger.
#   level  — Qualitative high/medium/low, manually set; no ledger.
#   none   — Presence-only; no quantity, no ledger.
STOCK_TRACKING_MODES: tuple[str, ...] = ("exact", "level", "none")

# Qualitative stock levels for ``level``-mode instances (M2 §3.2 / §3.4).
STOCK_LEVELS: tuple[str, ...] = ("high", "medium", "low")

# The sole trigger level for the low-stock signal in level mode (M4 walkthrough fix #2).
# A lot with stock_level == LOW_STOCK_TRIGGER_LEVEL is considered "low".
LOW_STOCK_TRIGGER_LEVEL: str = "low"

# Shopping-list row sources (M7 §3.1 / §4.1).
# Validated app-layer; no DB CHECK (roadmap §2.11).
#   auto   — materialised from the low-stock signal by reconcile_auto_items().
#   manual — user-entered (free-text or definition-linked).
SHOPPING_LIST_SOURCES: tuple[str, ...] = ("auto", "manual")

# Maintenance schedule interval units (M7 §3.2 / §4.1).
# Validated app-layer against this constant; no DB CHECK (roadmap §2.11).
#   day   — every N calendar days.
#   week  — every N weeks (7-day multiples).
#   month — every N calendar months (calendar-correct, end-of-month clamping).
#   year  — every N years (implemented as N*12 months — same clamping rules).
MAINTENANCE_INTERVAL_UNITS: tuple[str, ...] = ("day", "week", "month", "year")

# The six movement types for the append-only stock ledger (M2 §3.3 / §4.3).
# Validated app-layer (no DB CHECK — the set may grow; roadmap §2.11).
#   intake     — stock received / added.
#   consume    — stock consumed (FIFO-driven or per-lot).
#   move       — whole-lot location change; quantity_delta = 0.
#   adjust     — stock-take to an absolute counted value (signed delta).
#   discard    — stock written off / thrown away (negative delta).
#   correction — undo/reversal entry; delta = −original (M2 §4.4).
MOVEMENT_TYPES: tuple[str, ...] = ("intake", "consume", "move", "adjust", "discard", "correction")
