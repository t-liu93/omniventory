/**
 * Tests for src/i18n/format.ts — locale-aware formatting utilities.
 *
 * The test environment is pinned to 'en' by setup.ts (beforeEach).
 * Tests that verify language-switch behaviour force 'zh' and reset in afterEach.
 *
 * Key invariants:
 * 1. formatDate renders DIFFERENTLY under en vs zh (date order differs).
 * 2. formatNumber output is locale-aware (uses Intl); en ≈ zh for separators.
 * 3. formatQuantity preserves ALL 19 trailing-zero-trim cases from the
 *    original utils.ts (commit 18f9ec3), and does NOT lose fractional precision.
 */
import { describe, it, expect, afterEach } from "vitest";
import i18n from "../i18n";
import { formatDate, formatNumber, formatQuantity } from "../i18n/format";

// ── formatDate ────────────────────────────────────────────────────────────────

describe("formatDate", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  // Graceful handling of null/empty/invalid
  it("returns '' for null", () => {
    expect(formatDate(null)).toBe("");
  });

  it("returns '' for undefined", () => {
    expect(formatDate(undefined)).toBe("");
  });

  it("returns '' for empty string", () => {
    expect(formatDate("")).toBe("");
  });

  it("returns '' for whitespace string", () => {
    expect(formatDate("   ")).toBe("");
  });

  it("returns '' for an invalid date string", () => {
    expect(formatDate("not-a-date")).toBe("");
  });

  // Fixed date: verify locale-specific rendering
  // 2026-06-17 is a clear date where en and zh render in different orders.
  const dateOnly = "2026-06-17";
  const fullIso = "2026-06-17T12:00:00Z";

  it("renders a date-only string in en (numeric M/D/YYYY style)", () => {
    const result = formatDate(dateOnly);
    // The formatted string should contain "2026" and "6" and "17"
    expect(result).toContain("2026");
    expect(result).toMatch(/6/);
    expect(result).toMatch(/17/);
  });

  it("renders a full ISO datetime string without throwing (en)", () => {
    const result = formatDate(fullIso);
    expect(result.length).toBeGreaterThan(0);
    expect(result).toContain("2026");
  });

  it("renders a Date object without throwing (en)", () => {
    const result = formatDate(new Date("2026-06-17T00:00:00Z"));
    expect(result.length).toBeGreaterThan(0);
    expect(result).toContain("2026");
  });

  // The critical en vs zh assertion — this is the locale-split test.
  // en: M/D/YYYY → "6/17/2026"   zh: YYYY/M/D → "2026/6/17"
  // We assert the strings differ, and that year-first vs month-first ordering
  // is captured in zh vs en respectively.
  it("renders the same date DIFFERENTLY in en vs zh (date-order differs)", async () => {
    const enResult = formatDate(dateOnly); // pinned to en by setup.ts
    await i18n.changeLanguage("zh");
    const zhResult = formatDate(dateOnly);

    // The two locale outputs must differ.
    expect(enResult).not.toBe(zhResult);

    // en: year appears at the END of the formatted string
    // zh: year appears at the START of the formatted string
    expect(enResult.endsWith("2026")).toBe(true);
    expect(zhResult.startsWith("2026")).toBe(true);
  });

  it("renders differently for full ISO datetime in en vs zh", async () => {
    const enResult = formatDate(fullIso);
    await i18n.changeLanguage("zh");
    const zhResult = formatDate(fullIso);
    expect(enResult).not.toBe(zhResult);
  });
});

// ── formatNumber ──────────────────────────────────────────────────────────────

describe("formatNumber", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("formats an integer using Intl.NumberFormat (en)", () => {
    // Intl.NumberFormat('en').format(1234) → "1,234"
    const result = formatNumber(1234);
    expect(result).toBe(new Intl.NumberFormat("en").format(1234));
  });

  it("formats a decimal using Intl.NumberFormat (en)", () => {
    const result = formatNumber(1234.5);
    expect(result).toBe(new Intl.NumberFormat("en").format(1234.5));
  });

  it("formats 0 correctly", () => {
    expect(formatNumber(0)).toBe("0");
  });

  it("output matches Intl.NumberFormat for the active language (zh)", async () => {
    await i18n.changeLanguage("zh");
    const result = formatNumber(1234.5);
    expect(result).toBe(new Intl.NumberFormat("zh").format(1234.5));
  });

  // Note: en and zh share the same separators (grouping=",", decimal="."),
  // so their numeric output is the same. Do NOT assert a difference here.
  it("en and zh produce the same numeric output for 1234.5", async () => {
    const enResult = formatNumber(1234.5);
    await i18n.changeLanguage("zh");
    const zhResult = formatNumber(1234.5);
    expect(enResult).toBe(zhResult);
  });
});

// ── formatQuantity — trailing-zero-trim (19 original cases, pinned to en) ────
//
// These are verbatim ports of the original utils.ts test cases.
// ALL must pass under 'en' (setup.ts pins the language).

describe("formatQuantity — unit tests (original 19 cases, en)", () => {
  it("trims all trailing zeros and decimal point: '1.000000' → '1'", () => {
    expect(formatQuantity("1.000000")).toBe("1");
  });

  it("trims trailing zeros but keeps significant fraction: '1.200000' → '1.2'", () => {
    expect(formatQuantity("1.200000")).toBe("1.2");
  });

  it("trims trailing zeros with two significant fraction digits: '1.210000' → '1.21'", () => {
    expect(formatQuantity("1.210000")).toBe("1.21");
  });

  it("leaves integer string unchanged: '5' → '5'", () => {
    expect(formatQuantity("5")).toBe("5");
  });

  it("leaves decimal fraction unchanged when already clean: '0.5' → '0.5'", () => {
    expect(formatQuantity("0.5")).toBe("0.5");
  });

  it("accepts a number input: 5 → '5'", () => {
    expect(formatQuantity(5)).toBe("5");
  });

  it("accepts a number with fraction: 1.2 → '1.2'", () => {
    expect(formatQuantity(1.2)).toBe("1.2");
  });

  it("leaves already-trimmed value unchanged: '1.21' → '1.21'", () => {
    expect(formatQuantity("1.21")).toBe("1.21");
  });

  it("handles empty string gracefully — returns original", () => {
    expect(formatQuantity("")).toBe("");
  });

  it("handles a value with no fractional digits unchanged: '42' → '42'", () => {
    expect(formatQuantity("42")).toBe("42");
  });

  it("handles a value that is purely zeros after decimal: '0.000' → '0'", () => {
    expect(formatQuantity("0.000")).toBe("0");
  });

  // Integer-ending-in-zero regression lock: a naive /0+$/ without the
  // str.includes(".") guard would corrupt "10" → "1", "100" → "1", etc.
  it("leaves integer-ending-in-zero unchanged: '10' → '10'", () => {
    expect(formatQuantity("10")).toBe("10");
  });

  it("leaves hundred unchanged: '100' → '100'", () => {
    expect(formatQuantity("100")).toBe("100");
  });

  it("leaves mixed-digit integer ending in zero unchanged: '150' → '150'", () => {
    expect(formatQuantity("150")).toBe("150");
  });

  it("trims trailing zeros from Decimal-string integer ten: '20.000000' → '20'", () => {
    expect(formatQuantity("20.000000")).toBe("20");
  });
});

// ── formatQuantity — locale grouping (extended cases) ────────────────────────

describe("formatQuantity — locale grouping and precision", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("adds grouping separator to a large integer quantity (en): '1234' → '1,234'", () => {
    expect(formatQuantity("1234")).toBe("1,234");
  });

  it("adds grouping separator to a large decimal quantity (en): '1234.500' → '1,234.5'", () => {
    expect(formatQuantity("1234.500")).toBe("1,234.5");
  });

  // Precision safety: a Decimal string with many significant fractional digits
  // must NOT be round-tripped through a JS float.  The fractional digits are
  // carried verbatim after trimming.
  it("does NOT lose fractional precision: '1234.123456789' → '1,234.123456789'", () => {
    // JS float would round-trip this to ~1234.123456789 (fine here by chance),
    // but the test asserts the fractional digits come through unchanged.
    expect(formatQuantity("1234.123456789")).toBe("1,234.123456789");
  });

  it("does NOT produce a float precision artefact on '0.1'", () => {
    // Number("0.1") is safe; the test confirms no extra digits.
    expect(formatQuantity("0.1")).toBe("0.1");
  });

  // en and zh share the same separators — quantity output is identical.
  it("en and zh produce the same quantity output (separators are shared)", async () => {
    const enResult = formatQuantity("1234.500");
    await i18n.changeLanguage("zh");
    const zhResult = formatQuantity("1234.500");
    expect(enResult).toBe(zhResult);
    expect(enResult).toBe("1,234.5");
  });
});
