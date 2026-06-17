/**
 * Locale-aware formatting utilities bound to the active i18n language.
 *
 * All formatters read `i18n.language` at call time so they automatically
 * reflect a language switch without needing to be re-initialised.
 *
 * IMPORTANT: these are *output* formatters only. Per M1.5 §1 non-goals,
 * locale-aware *parsing* of inputs is out of scope — form inputs remain
 * canonical (e.g. "1.5", not "1,5").
 */

import i18n from "./index";

// ── Date formatting ───────────────────────────────────────────────────────────

/**
 * Format a date value (ISO string, Date object, or null/undefined) in the
 * active locale's short date style.
 *
 * - en: 6/17/2026  (M/D/YYYY)
 * - zh: 2026/6/17  (YYYY/M/D)
 *
 * Date-only strings (YYYY-MM-DD) are parsed as UTC midnight to avoid
 * off-by-one errors from local timezone offset. Full ISO datetimes (with a
 * 'T' or 'Z') are handed directly to the Date constructor.
 *
 * Returns "" for null, undefined, empty string, or an unparseable value.
 */
export function formatDate(value: string | Date | null | undefined): string {
  if (value == null) return "";

  let date: Date;
  if (value instanceof Date) {
    date = value;
  } else {
    const s = value.trim();
    if (!s) return "";

    // Date-only YYYY-MM-DD: parse as UTC midnight to avoid timezone shifts.
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      date = new Date(s + "T00:00:00Z");
    } else {
      date = new Date(s);
    }
  }

  if (isNaN(date.getTime())) return "";

  return new Intl.DateTimeFormat(i18n.language, {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    // For date-only values we omit the time zone so there is no UTC→local
    // shift.  For full datetimes the browser's local offset applies, which
    // is the correct user-facing behaviour.
    timeZone: value instanceof Date
      ? undefined
      : typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value.trim())
        ? "UTC"
        : undefined,
  }).format(date);
}

// ── Number formatting ─────────────────────────────────────────────────────────

/**
 * Format an integer or decimal number in the active locale.
 *
 * en and zh share the same decimal/grouping separators (`.` and `,`),
 * so output is visually identical between the two languages. The value is
 * deliberately passed as a JS number — this helper is for already-numeric
 * values, not Decimal quantity strings (use formatQuantity for those).
 */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat(i18n.language).format(value);
}

// ── Quantity formatting ───────────────────────────────────────────────────────

/**
 * Format a Decimal quantity string for human display.
 *
 * Preserves the exact trailing-zero-trim behaviour from commit 18f9ec3
 * (src/utils.ts) **without** round-tripping the fractional digits through a
 * JS float (which would risk precision loss on large Decimal values).
 *
 * Strategy to be both locale-aware and precision-safe:
 *   1. Convert to string and apply the trailing-zero trim (verbatim logic
 *      from utils.ts).
 *   2. Split on ".":  integerPart + optionally fractionalPart.
 *   3. Format the integer part with `Intl.NumberFormat` to get locale
 *      grouping separators (e.g. "1,234").
 *   4. Obtain the locale's decimal separator from Intl (e.g. "." for en/zh).
 *   5. Re-join: "intFormatted" + decimalSeparator + fractionalPart.
 *
 * The fractional digits are never fed into a float, so no precision is lost.
 *
 * For en and zh, both separators are identical ("," grouping, "." decimal),
 * so the output is the same in both locales. The split/re-join is correct
 * for any locale that might be added in future.
 *
 * All 19 original formatQuantity test cases (pinned to en) still hold.
 *
 * Examples (en):
 *   "1.000000" → "1"
 *   "1.200000" → "1.2"
 *   "1,234.500" → "1,234.5"
 *   "5"        → "5"
 *   ""          → ""
 *   "bad"       → "bad"
 *
 * @param value - The raw Decimal quantity value from the API (string or number).
 * @returns A human-readable, locale-formatted string with trailing zeros stripped.
 */
export function formatQuantity(value: string | number): string {
  const str = String(value);

  // If there is no decimal point, nothing to strip — apply grouping only.
  if (!str.includes(".")) {
    const n = Number(str);
    if (!isNaN(n) && str !== "") {
      return new Intl.NumberFormat(i18n.language, {
        maximumFractionDigits: 0,
        useGrouping: true,
      }).format(n);
    }
    // Non-numeric (e.g. "") or malformed — return as-is.
    return str;
  }

  // Strip trailing zeros after the decimal point, then a dangling dot.
  const trimmed = str.replace(/\.?0+$/, "");

  // Guard: if stripping produced an empty string or just a sign character,
  // fall back to the original to avoid confusing output.
  if (trimmed === "" || trimmed === "-") {
    return str;
  }

  // Split into integer and fractional parts (trimmed may have no "." now
  // if all fractional digits were zeros and the dot was also removed).
  const dotIndex = trimmed.indexOf(".");
  if (dotIndex === -1) {
    // All fractional digits were zeros; format the integer with grouping.
    const n = Number(trimmed);
    if (!isNaN(n)) {
      return new Intl.NumberFormat(i18n.language, {
        maximumFractionDigits: 0,
        useGrouping: true,
      }).format(n);
    }
    return trimmed;
  }

  // There is a fractional part remaining after trimming.
  const integerPart = trimmed.slice(0, dotIndex);
  const fractionalPart = trimmed.slice(dotIndex + 1);

  // Format the integer part with locale grouping.
  const intN = Number(integerPart === "" || integerPart === "-" ? integerPart + "0" : integerPart);
  const formattedInteger = isNaN(intN)
    ? integerPart
    : new Intl.NumberFormat(i18n.language, {
        maximumFractionDigits: 0,
        useGrouping: true,
      }).format(intN);

  // Derive the locale's decimal separator from Intl.
  const decimalSeparator = new Intl.NumberFormat(i18n.language)
    .formatToParts(1.1)
    .find((p) => p.type === "decimal")?.value ?? ".";

  return formattedInteger + decimalSeparator + fractionalPart;
}
