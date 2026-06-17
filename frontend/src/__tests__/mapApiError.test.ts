/**
 * M1.5 Step 5 — mapApiError unit tests.
 *
 * Covers:
 * 1. Known code → localized string in EN (default test language).
 * 2. Known code with params → localized string with params interpolated.
 * 3. Unknown code → errors.generic fallback.
 * 4. Non-object input (null, string, undefined) → errors.generic (no crash).
 * 5. Object with no `code` field → errors.generic.
 * 6. Language switch: same code → ZH after changeLanguage('zh').
 */

import { describe, it, expect, afterEach } from "vitest";
import i18n from "../i18n";
import { mapApiError } from "../i18n/errors";

// Restore 'en' after language-switch tests.
afterEach(async () => {
  await i18n.changeLanguage("en");
});

// ---------------------------------------------------------------------------
// 1. Known code → localized EN string
// ---------------------------------------------------------------------------

describe("mapApiError — known codes resolve to EN localized strings", () => {
  it("auth.invalid_credentials → English message", () => {
    const result = mapApiError({ code: "auth.invalid_credentials", message: "Invalid credentials." });
    expect(result).toBe("Invalid email or password.");
  });

  it("auth.setup_already_complete → English message", () => {
    const result = mapApiError({ code: "auth.setup_already_complete", message: "Setup already complete." });
    expect(result).toBe("Setup is already complete. An admin account already exists.");
  });

  it("stock_instance.serial_requires_qty_one → English message", () => {
    const result = mapApiError({
      code: "stock_instance.serial_requires_qty_one",
      message: "When a serial number is provided, quantity must be exactly 1.",
    });
    expect(result).toBe("When a serial number is set, quantity must be exactly 1.");
  });

  it("tree.delete_has_children → English message", () => {
    const result = mapApiError({
      code: "tree.delete_has_children",
      message: "Cannot delete a node that still has children.",
      params: { kind: "location" },
    });
    expect(result).toContain("Cannot delete");
    expect(result).toContain("location");
  });

  it("internal.error → English message", () => {
    const result = mapApiError({ code: "internal.error", message: "An internal error occurred." });
    expect(result).toBe("An internal error occurred. Please try again later.");
  });
});

// ---------------------------------------------------------------------------
// 2. Known code with params → params are interpolated
// ---------------------------------------------------------------------------

describe("mapApiError — params interpolation", () => {
  it("location.not_found with {id: 42} → message contains 42", () => {
    const result = mapApiError({
      code: "location.not_found",
      message: "Location not found.",
      params: { id: 42 },
    });
    expect(result).toContain("42");
    expect(result).toContain("Location");
  });

  it("stock_instance.serial_duplicate with {serial: 'SN-999'} → message contains SN-999", () => {
    const result = mapApiError({
      code: "stock_instance.serial_duplicate",
      message: "Serial number is already registered.",
      params: { serial: "SN-999" },
    });
    expect(result).toContain("SN-999");
  });

  it("validation.unsupported_language with {value, supported} → interpolated", () => {
    const result = mapApiError({
      code: "validation.unsupported_language",
      message: "Unsupported language code.",
      params: { value: "fr", supported: "en, zh" },
    });
    expect(result).toContain("fr");
    expect(result).toContain("en, zh");
  });
});

// ---------------------------------------------------------------------------
// 3. Unknown code → errors.generic
// ---------------------------------------------------------------------------

describe("mapApiError — unknown code → generic fallback", () => {
  it("returns generic text for a completely unknown code", () => {
    const result = mapApiError({ code: "unknown.code.xyz", message: "Some backend message." });
    // In DEV mode the raw message may be appended — assert on the base text
    expect(result).toContain("Something went wrong. Please try again.");
  });

  it("returns generic text for an empty string code", () => {
    const result = mapApiError({ code: "", message: "Empty code." });
    expect(result).toContain("Something went wrong. Please try again.");
  });
});

// ---------------------------------------------------------------------------
// 4. Non-object / null / undefined → errors.generic (no crash)
// ---------------------------------------------------------------------------

describe("mapApiError — non-object / missing inputs handled defensively", () => {
  it("null → generic", () => {
    const result = mapApiError(null);
    expect(result).toBe("Something went wrong. Please try again.");
  });

  it("undefined → generic", () => {
    const result = mapApiError(undefined);
    expect(result).toBe("Something went wrong. Please try again.");
  });

  it("string → generic", () => {
    const result = mapApiError("network error");
    expect(result).toBe("Something went wrong. Please try again.");
  });

  it("number → generic", () => {
    const result = mapApiError(500);
    expect(result).toBe("Something went wrong. Please try again.");
  });

  it("object with no code field → generic", () => {
    const result = mapApiError({ message: "No code here." });
    // In DEV mode the raw message may be appended — assert on the base text
    expect(result).toContain("Something went wrong. Please try again.");
  });

  it("object with non-string code → generic", () => {
    const result = mapApiError({ code: 404, message: "Numeric code." });
    // In DEV mode the raw message may be appended — assert on the base text
    expect(result).toContain("Something went wrong. Please try again.");
  });
});

// ---------------------------------------------------------------------------
// Regression Fix 1 — object-prefix / namespace-prefix codes → generic
//   (i18next would return a diagnostic string for codes like "auth" or
//    "location" that map to a namespace object instead of a leaf string)
// ---------------------------------------------------------------------------

describe("mapApiError — object-prefix / namespace codes → generic (Fix 1 regression)", () => {
  it("bare namespace code 'auth' → generic (not an i18next diagnostic)", () => {
    const result = mapApiError({ code: "auth", message: "bad prefix" });
    expect(result).toContain("Something went wrong. Please try again.");
    expect(result).not.toMatch(/returned an object/i);
    expect(result).not.toMatch(/key '.*' returned/i);
  });

  it("bare namespace code 'location' → generic (not an i18next diagnostic)", () => {
    const result = mapApiError({ code: "location", message: "bad prefix" });
    expect(result).toContain("Something went wrong. Please try again.");
    expect(result).not.toMatch(/returned an object/i);
  });

  it("unknown leaf code 'unknown.code.xyz' → generic", () => {
    const result = mapApiError({ code: "unknown.code.xyz", message: "no such code" });
    expect(result).toContain("Something went wrong. Please try again.");
  });

  it("null error → generic (no crash)", () => {
    const result = mapApiError(null);
    expect(result).toBe("Something went wrong. Please try again.");
  });
});

// ---------------------------------------------------------------------------
// Regression Fix 3 — zh tree.* messages must NOT contain raw English kind
// ---------------------------------------------------------------------------

describe("mapApiError — zh tree.* messages use localized entity words (Fix 3 regression)", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("tree.delete_has_children zh with kind='location' → '位置', not 'location'", async () => {
    await i18n.changeLanguage("zh");
    const result = mapApiError({
      code: "tree.delete_has_children",
      message: "Cannot delete.",
      params: { kind: "location" },
    });
    expect(result).toContain("位置");
    expect(result).not.toContain("location");
    expect(result).toContain("无法删除");
  });

  it("tree.cycle zh with kind='category' → '分类', not 'category'", async () => {
    await i18n.changeLanguage("zh");
    const result = mapApiError({
      code: "tree.cycle",
      message: "Would create cycle.",
      params: { kind: "category" },
    });
    expect(result).toContain("分类");
    expect(result).not.toContain("category");
    expect(result).toContain("循环引用");
  });

  it("tree.delete_has_children en with kind='location' → 'location' (unchanged in EN)", () => {
    const result = mapApiError({
      code: "tree.delete_has_children",
      message: "Cannot delete.",
      params: { kind: "location" },
    });
    expect(result).toContain("location");
    expect(result).toContain("Cannot delete");
  });
});

// ---------------------------------------------------------------------------
// 5. Language switch: same code → ZH after changeLanguage('zh')
// ---------------------------------------------------------------------------

describe("mapApiError — language switch produces different output", () => {
  it("auth.invalid_credentials in EN → English text", () => {
    const result = mapApiError({ code: "auth.invalid_credentials", message: "Invalid credentials." });
    expect(result).toBe("Invalid email or password.");
  });

  it("auth.invalid_credentials in ZH → Chinese text", async () => {
    await i18n.changeLanguage("zh");
    const result = mapApiError({ code: "auth.invalid_credentials", message: "Invalid credentials." });
    expect(result).toBe("邮箱或密码不正确。");
  });

  it("stock_instance.serial_requires_qty_one in ZH → Chinese text", async () => {
    await i18n.changeLanguage("zh");
    const result = mapApiError({
      code: "stock_instance.serial_requires_qty_one",
      message: "When a serial number is provided, quantity must be exactly 1.",
    });
    expect(result).toBe("设置序列号时，数量必须为 1。");
  });

  it("tree.delete_has_children in ZH → contains Chinese text, not English", async () => {
    await i18n.changeLanguage("zh");
    const result = mapApiError({
      code: "tree.delete_has_children",
      message: "Cannot delete a node that still has children.",
      // Backend sends the raw English kind; mapApiError must localize it.
      params: { kind: "location" },
    });
    // Should be in Chinese, not contain "Cannot delete"
    expect(result).not.toContain("Cannot delete");
    expect(result).toContain("无法删除");
    // The entity word must be Chinese — not the raw English "location"
    expect(result).not.toContain("location");
    expect(result).toContain("位置");
  });
});
