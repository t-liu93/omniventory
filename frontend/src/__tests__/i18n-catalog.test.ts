/**
 * M1.5 Step 4+5 — i18n catalog tests.
 *
 * Covers:
 * 1. Key-parity: en and zh have exactly the same set of keys for every
 *    content namespace (common, auth, nav, locations, categories, items,
 *    instances, errors). Fails on any missing or extra key.
 * 2. zh translation rendering: switching to 'zh' renders translated copy
 *    on a sample surface (Login page), confirming non-English strings appear.
 */

import { describe, it, expect, afterEach } from "vitest";
import i18n from "../i18n";

// ── Catalog files ─────────────────────────────────────────────────────────────

import enCommon from "../i18n/locales/en/common.json";
import enAuth from "../i18n/locales/en/auth.json";
import enNav from "../i18n/locales/en/nav.json";
import enLocations from "../i18n/locales/en/locations.json";
import enCategories from "../i18n/locales/en/categories.json";
import enItems from "../i18n/locales/en/items.json";
import enInstances from "../i18n/locales/en/instances.json";
import enErrors from "../i18n/locales/en/errors.json";
import enDashboard from "../i18n/locales/en/dashboard.json";

import zhCommon from "../i18n/locales/zh/common.json";
import zhAuth from "../i18n/locales/zh/auth.json";
import zhNav from "../i18n/locales/zh/nav.json";
import zhLocations from "../i18n/locales/zh/locations.json";
import zhCategories from "../i18n/locales/zh/categories.json";
import zhItems from "../i18n/locales/zh/items.json";
import zhInstances from "../i18n/locales/zh/instances.json";
import zhErrors from "../i18n/locales/zh/errors.json";
import zhDashboard from "../i18n/locales/zh/dashboard.json";

// ── Deep key extraction ───────────────────────────────────────────────────────

/**
 * Recursively collect all leaf key paths from a nested object.
 * e.g. { a: { b: "val" } } → ["a.b"]
 */
function collectKeys(obj: unknown, prefix = ""): string[] {
  if (typeof obj !== "object" || obj === null) return [prefix];
  const keys: string[] = [];
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      keys.push(...collectKeys(value, path));
    } else {
      keys.push(path);
    }
  }
  return keys;
}

// ── Namespace pairs ───────────────────────────────────────────────────────────

const namespacePairs: [string, unknown, unknown][] = [
  ["common", enCommon, zhCommon],
  ["auth", enAuth, zhAuth],
  ["nav", enNav, zhNav],
  ["locations", enLocations, zhLocations],
  ["categories", enCategories, zhCategories],
  ["items", enItems, zhItems],
  ["instances", enInstances, zhInstances],
  ["errors", enErrors, zhErrors],
  ["dashboard", enDashboard, zhDashboard],
];

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Catalog key parity — en and zh have identical key sets", () => {
  for (const [ns, en, zh] of namespacePairs) {
    it(`namespace '${ns}' has the same keys in en and zh`, () => {
      const enKeys = collectKeys(en).sort();
      const zhKeys = collectKeys(zh).sort();

      const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
      const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

      expect(missingInZh, `Keys in en/${ns} missing from zh/${ns}`).toEqual([]);
      expect(extraInZh, `Extra keys in zh/${ns} not present in en/${ns}`).toEqual([]);
    });
  }
});

// ── zh rendering test ─────────────────────────────────────────────────────────

describe("zh translation — i18next resolves zh strings", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("auth.login.submit is 'Sign in' in en", () => {
    expect(i18n.t("login.submit", { ns: "auth" })).toBe("Sign in");
  });

  it("auth.login.submit is not 'Sign in' in zh (translated)", async () => {
    await i18n.changeLanguage("zh");
    const zhValue = i18n.t("login.submit", { ns: "auth" });
    // zh value should not be the English text
    expect(zhValue).not.toBe("Sign in");
    // zh value should be a non-empty string (actual translation exists)
    expect(zhValue.trim().length).toBeGreaterThan(0);
  });

  it("nav.dashboard is '仪表板' in zh", async () => {
    await i18n.changeLanguage("zh");
    expect(i18n.t("dashboard", { ns: "nav" })).toBe("仪表板");
  });

  it("common.actions.save is '保存' in zh", async () => {
    await i18n.changeLanguage("zh");
    expect(i18n.t("actions.save", { ns: "common" })).toBe("保存");
  });

  it("instances.form.quantitySerialHint is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const zhHint = i18n.t("form.quantitySerialHint", { ns: "instances" });
    expect(zhHint).not.toBe("Serial is set — quantity forced to 1");
    expect(zhHint.trim().length).toBeGreaterThan(0);
  });
});
