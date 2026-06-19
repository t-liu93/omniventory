/**
 * ExpiryBadge — presentational, display-only component.
 *
 * Given a `best_before_date` (ISO date string or null/undefined), renders:
 *   - Red "Expired" badge when the date is in the past (days_remaining < 0).
 *   - Amber "Expires in N days" badge when within the SOON_THRESHOLD_DAYS window.
 *   - Nothing (null) when the date is absent, far off, or today+threshold+1 or beyond.
 *
 * SOON_THRESHOLD_DAYS = 30 (frontend display constant per M3 §7.3).
 *
 * This badge is purely client-side: it does NOT call any API endpoint and does
 * NOT re-implement the server's expiring list logic (that lives behind GET /expiring,
 * consumed in Step 6). It is a local convenience cue only.
 *
 * Dates are compared by parsing the ISO YYYY-MM-DD string as UTC midnight (matching
 * formatDate's convention) and comparing against today's UTC date.
 */
import { Badge } from "@mantine/core";
import { useTranslation } from "react-i18next";

/** Frontend display window for the "expiring soon" amber badge. */
export const SOON_THRESHOLD_DAYS = 30;

interface ExpiryBadgeProps {
  /** ISO date string (YYYY-MM-DD) or null/undefined for no expiry. */
  bestBeforeDate: string | null | undefined;
}

/**
 * Parse a YYYY-MM-DD ISO string as UTC midnight.
 * Returns a Date or null for absent/invalid input.
 */
function parseDateUTC(value: string | null | undefined): Date | null {
  if (!value) return null;
  const s = value.trim();
  if (!s) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    const d = new Date(s + "T00:00:00Z");
    return isNaN(d.getTime()) ? null : d;
  }
  return null;
}

/**
 * Today as a UTC date (year, month, day only — no time component).
 * We construct this as a Date object with UTC midnight of today's UTC date
 * so comparisons with parseDate are consistent.
 */
function todayUTC(): Date {
  const now = new Date();
  // Use UTC year/month/day to match the UTC midnight parsing convention.
  return new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
}

export function ExpiryBadge({ bestBeforeDate }: ExpiryBadgeProps) {
  const { t } = useTranslation("expiry");

  const date = parseDateUTC(bestBeforeDate);
  if (!date) return null;

  const today = todayUTC();
  // days_remaining: number of full days from today to the expiry date.
  // Negative = expired, 0 = expires today, positive = future.
  const msPerDay = 1000 * 60 * 60 * 24;
  const daysRemaining = Math.round((date.getTime() - today.getTime()) / msPerDay);

  if (daysRemaining < 0) {
    // Past: red "Expired" badge.
    return (
      <Badge
        size="sm"
        color="red"
        variant="filled"
        data-testid="expiry-badge-expired"
      >
        {t("expired")}
      </Badge>
    );
  }

  if (daysRemaining <= SOON_THRESHOLD_DAYS) {
    // Within the soon window: amber/yellow "Expires in N days" badge.
    return (
      <Badge
        size="sm"
        color="yellow"
        variant="filled"
        data-testid="expiry-badge-soon"
      >
        {t("expiresIn_other", { count: daysRemaining })}
      </Badge>
    );
  }

  // Far off or absent: render nothing.
  return null;
}
