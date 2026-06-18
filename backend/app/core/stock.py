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
