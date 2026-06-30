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
import enStock from "../i18n/locales/en/stock.json";
import enExpiry from "../i18n/locales/en/expiry.json";
import enNotifications from "../i18n/locales/en/notifications.json";
import enConfiguration from "../i18n/locales/en/configuration.json";
import enAttachments from "../i18n/locales/en/attachments.json";
import enTags from "../i18n/locales/en/tags.json";
import enNotes from "../i18n/locales/en/notes.json";
import enCustomFields from "../i18n/locales/en/customFields.json";
import enBarcode from "../i18n/locales/en/barcode.json";
import enSearch from "../i18n/locales/en/search.json";
import enExport from "../i18n/locales/en/export.json";
import enRoles from "../i18n/locales/en/roles.json";
import enUsers from "../i18n/locales/en/users.json";
import enInvitations from "../i18n/locales/en/invitations.json";
import enAccount from "../i18n/locales/en/account.json";
import enResponsible from "../i18n/locales/en/responsible.json";
import enAudit from "../i18n/locales/en/audit.json";
import enShoppingList from "../i18n/locales/en/shoppingList.json";
import enMaintenance from "../i18n/locales/en/maintenance.json";
import enLlm from "../i18n/locales/en/llm.json";

import zhCommon from "../i18n/locales/zh/common.json";
import zhAuth from "../i18n/locales/zh/auth.json";
import zhNav from "../i18n/locales/zh/nav.json";
import zhLocations from "../i18n/locales/zh/locations.json";
import zhCategories from "../i18n/locales/zh/categories.json";
import zhItems from "../i18n/locales/zh/items.json";
import zhInstances from "../i18n/locales/zh/instances.json";
import zhErrors from "../i18n/locales/zh/errors.json";
import zhDashboard from "../i18n/locales/zh/dashboard.json";
import zhStock from "../i18n/locales/zh/stock.json";
import zhExpiry from "../i18n/locales/zh/expiry.json";
import zhNotifications from "../i18n/locales/zh/notifications.json";
import zhConfiguration from "../i18n/locales/zh/configuration.json";
import zhAttachments from "../i18n/locales/zh/attachments.json";
import zhTags from "../i18n/locales/zh/tags.json";
import zhNotes from "../i18n/locales/zh/notes.json";
import zhCustomFields from "../i18n/locales/zh/customFields.json";
import zhBarcode from "../i18n/locales/zh/barcode.json";
import zhSearch from "../i18n/locales/zh/search.json";
import zhExport from "../i18n/locales/zh/export.json";
import zhRoles from "../i18n/locales/zh/roles.json";
import zhUsers from "../i18n/locales/zh/users.json";
import zhInvitations from "../i18n/locales/zh/invitations.json";
import zhAccount from "../i18n/locales/zh/account.json";
import zhResponsible from "../i18n/locales/zh/responsible.json";
import zhAudit from "../i18n/locales/zh/audit.json";
import zhShoppingList from "../i18n/locales/zh/shoppingList.json";
import zhMaintenance from "../i18n/locales/zh/maintenance.json";
import zhLlm from "../i18n/locales/zh/llm.json";

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
  ["stock", enStock, zhStock],
  ["expiry", enExpiry, zhExpiry],
  ["notifications", enNotifications, zhNotifications],
  ["configuration", enConfiguration, zhConfiguration],
  ["attachments", enAttachments, zhAttachments],
  ["tags", enTags, zhTags],
  ["notes", enNotes, zhNotes],
  ["customFields", enCustomFields, zhCustomFields],
  ["barcode", enBarcode, zhBarcode],
  ["search", enSearch, zhSearch],
  ["export", enExport, zhExport],
  ["roles", enRoles, zhRoles],
  ["users", enUsers, zhUsers],
  ["invitations", enInvitations, zhInvitations],
  ["account", enAccount, zhAccount],
  ["responsible", enResponsible, zhResponsible],
  ["audit", enAudit, zhAudit],
  ["shoppingList", enShoppingList, zhShoppingList],
  ["maintenance", enMaintenance, zhMaintenance],
  ["llm", enLlm, zhLlm],
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
