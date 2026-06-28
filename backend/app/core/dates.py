"""Calendar-correct date arithmetic helpers (M7 §4.4).

``add_interval`` computes the next date from a base date, an interval unit, and
a count.  It uses **calendar-correct** month/year arithmetic with
**end-of-month clamping** (e.g. Jan-31 + 1 month → Feb-28 or Feb-29 in a leap
year).  Stdlib only — no ``dateutil`` dependency.

Functions
---------
add_interval(d, unit, count)
    Public API.  Adds ``count`` ``unit``s to date ``d``.  Unit must be one of
    ``'day'``, ``'week'``, ``'month'``, ``'year'``; unknown units raise
    ``ValueError``.

Design notes
------------
- ``_add_months`` is the crux: converting raw month-arithmetic overflow to a
  valid ``(year, month, day)`` triple, then clamping the day to the last valid
  day of the target month.  This is the "end-of-month clamp" that is easy to
  get wrong — see unit tests in ``tests/test_m7_step4.py``.
- ``calendar.monthrange(year, month)[1]`` returns the correct last day of any
  month, including February in a leap year.
- ``year = add_interval`` wraps at ``year=×12 months`` because year-addition
  is delegated to ``_add_months`` (``count * 12``), so ``+12 months`` and
  ``+1 year`` are identical.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta


def _days_in_month(year: int, month: int) -> int:
    """Return the number of days in the given month of the given year."""
    return calendar.monthrange(year, month)[1]


def _add_months(d: date, n: int) -> date:
    """Add ``n`` months to date ``d``, clamping the day to month-end.

    Algorithm
    ---------
    1. Compute the raw (0-indexed) month offset: ``m = (d.month - 1) + n``.
    2. Extract new year and 1-indexed month from the offset via ``divmod``.
    3. Clamp the day to the last valid day of the target month.
    4. Return ``date(year, month, day)``.

    Examples
    --------
    - Jan-31 + 1 month → Feb-28 (or Feb-29 in a leap year).
    - Aug-31 + 6 months → Feb-28 (or Feb-29 in a leap year).
    - Dec-31 + 1 month → Jan-31 (clamp: 31 ≤ 31 — no clamp needed).
    - Oct-31 + 1 month → Nov-30 (clamp: 31 → 30).
    """
    m = d.month - 1 + n  # 0-indexed total months
    year = d.year + m // 12
    month = m % 12 + 1  # back to 1-indexed
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def add_interval(d: date, unit: str, count: int) -> date:
    """Return the date that is ``count`` ``unit``s after ``d``.

    Parameters
    ----------
    d:
        The base date.
    unit:
        One of ``'day'``, ``'week'``, ``'month'``, ``'year'``.
        An unknown value raises ``ValueError`` — the service layer validates
        the unit against ``MAINTENANCE_INTERVAL_UNITS`` before calling, but
        this helper is defensive.
    count:
        Number of units to add.  Must be ≥ 1 (validated by the service and
        Pydantic before reaching here, but any positive integer is accepted).

    Returns
    -------
    date
        The computed next date.

    Raises
    ------
    ValueError
        When ``unit`` is not one of the four supported values.
    """
    if unit == "day":
        return d + timedelta(days=count)
    if unit == "week":
        return d + timedelta(weeks=count)
    if unit == "month":
        return _add_months(d, count)
    if unit == "year":
        return _add_months(d, count * 12)
    raise ValueError(
        f"Unsupported interval unit: {unit!r}. Expected one of 'day', 'week', 'month', 'year'."
    )
